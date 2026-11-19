"""Loopback host for simulated input and an explicit webcam preview."""

from __future__ import annotations

import argparse
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from examples.simulation.gaze_preview import GazePreview
from examples.simulation.preview import read_camera
from fovea.paths import default_model_path
from fovea.simulation import CENTERS, HEAD_SPAN_DEG, RECTS, Simulation
from fovea.webcam.backend import LandmarkObservation

_PREVIEW_LEASE_NS = 400_000_000
_PREVIEW_LAYOUT = [(rect.id, rect.x, rect.y, rect.w, rect.h) for rect in RECTS]


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port: int, *, preview: bool = False) -> None:
        super().__init__(("127.0.0.1", port), Handler)
        self.simulation = Simulation()
        self.lock = threading.Lock()
        self.preview = GazePreview(self.simulation) if preview else None
        self.preview_error: str | None = None
        self.visible_until_ns = 0
        self.camera_thread: threading.Thread | None = None
        self.stop_watchdog = threading.Event()
        self.watchdog = threading.Thread(target=self._watch, daemon=True)
        self.watchdog.start()

    def _watch(self) -> None:
        while not self.stop_watchdog.wait(0.1):
            with self.lock:
                now_ns = time.monotonic_ns()
                self.simulation.idle(now_ns)
                if self.preview is not None and 0 < self.visible_until_ns < now_ns:
                    self.visible_until_ns = 0
                    self.simulation.lose_signal()

    def start_camera(self, index: int, model: Path) -> None:
        if self.preview is None or self.camera_thread is not None:
            raise RuntimeError("La sesión de cámara requiere el modo de vista previa")
        self.camera_thread = threading.Thread(
            target=self._camera_loop, args=(index, model), name="fovea-preview", daemon=True
        )
        self.camera_thread.start()

    def _camera_loop(self, index: int, model: Path) -> None:
        try:
            read_camera(index, model, self.stop_watchdog, self.receive_face)
        except Exception as exc:
            with self.lock:
                self.preview_error = str(exc)
                self.simulation.lose_signal()

    def receive_face(
        self, face: LandmarkObservation | None, w: int, h: int, captured_ns: int
    ) -> None:
        with self.lock:
            now_ns = time.monotonic_ns()
            if (
                self.preview is not None
                and not self.stop_watchdog.is_set()
                and self.preview_error is None
                and now_ns <= self.visible_until_ns
            ):
                self.preview.observe(face, w, h, captured_ns, now_ns)

    def preview_state(self, visible: bool, *, test: bool | None = None) -> dict[str, object]:
        now_ns = time.monotonic_ns()
        if visible and self.preview_error is None:
            self.simulation.idle(now_ns)
            if 0 < self.visible_until_ns < now_ns:
                self.simulation.lose_signal()
            self.visible_until_ns = now_ns + _PREVIEW_LEASE_NS
        else:
            self.visible_until_ns = 0
            self.simulation.lose_signal()
        preview = self.preview
        if preview is not None and visible and self.preview_error is None:
            if test is True:
                preview.start_aim_check()
            elif test is False:
                preview.cancel_aim_check()
        eyes, fps, monocular = (
            preview.diagnostics(now_ns) if visible and preview is not None else (0, None, None)
        )
        return {
            **self.simulation.state(),
            "preview_layout": _PREVIEW_LAYOUT,
            "preview_input": "gaze",
            "preview_calibration": self.preview.calibration() if self.preview is not None else None,
            "preview_motion": preview is not None and preview.correction is not None,
            "preview_ready": preview is not None and preview.model is not None,
            "preview_eyes": eyes,
            "preview_fps": fps,
            "preview_monocular": monocular,
            "preview_aim_result": preview.aim_result if preview is not None else None,
            "preview_error": self.preview_error,
            "preview_signal": (
                self.preview.status(now_ns)
                if visible and self.preview is not None
                else "Pon el panel en primer plano."
            ),
        }

    def server_close(self) -> None:
        self.stop_watchdog.set()
        self.watchdog.join(timeout=1)
        if self.camera_thread is not None:
            self.camera_thread.join(timeout=1)
        with self.lock:
            self.simulation.lose_signal()
        super().server_close()


class Handler(BaseHTTPRequestHandler):
    server: Server

    def log_message(self, format: str, *args: object) -> None:
        pass

    def _send(self, status: int, data: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; frame-ancestors 'none'",
        )
        self.end_headers()
        self.wfile.write(data)

    def _allowed(self) -> bool:
        port = self.server.server_port
        hosts = (f"127.0.0.1:{port}", f"localhost:{port}")
        origin = self.headers.get("Origin")
        return (
            self.headers.get("Host") in hosts
            and (not origin or origin in tuple(f"http://{h}" for h in hosts))
            and self.headers.get("Sec-Fetch-Site", "same-origin") in ("same-origin", "none")
        )

    def do_GET(self) -> None:
        if not self._allowed():
            self._send(403, b"Forbidden", "text/plain")
            return
        files = {
            "/": ("simulation.html", "text/html; charset=utf-8"),
            "/simulation.js": ("simulation.js", "text/javascript; charset=utf-8"),
            "/simulation.css": ("simulation.css", "text/css; charset=utf-8"),
        }
        if self.path not in files:
            self._send(404, b"Not found", "text/plain")
            return
        name, mime = files[self.path]
        data = (Path(__file__).parent / name).read_bytes()
        if self.path == "/" and self.server.preview is not None:
            data = data.replace(b"<body>", b'<body data-preview="true">', 1)
        self._send(200, data, mime)

    def do_POST(self) -> None:
        if not self._allowed():
            self._send(403, b"Forbidden", "text/plain")
            return
        try:
            if self.path != "/api/frame" or self.headers.get("Content-Type") != "application/json":
                raise ValueError("Solicitud inválida")
            size = int(self.headers.get("Content-Length", "-1"))
            if not 0 <= size <= 1024:
                raise ValueError("Tamaño inválido")
            body = json.loads(self.rfile.read(size))
            if not isinstance(body, dict):
                raise ValueError("Señal inválida")
            with self.server.lock:
                if self.server.preview is not None:
                    if (
                        set(body) not in ({"visible"}, {"visible", "test"})
                        or not isinstance(body["visible"], bool)
                        or ("test" in body and not isinstance(body["test"], bool))
                    ):
                        raise ValueError("Vista previa: se requiere estado de visibilidad")
                    state = self.server.preview_state(body["visible"], test=body.get("test"))
                else:
                    state = self._manual_state(body)
            self._send(
                200,
                json.dumps(state, ensure_ascii=False).encode(),
                "application/json; charset=utf-8",
            )
        except (ValueError, TypeError, json.JSONDecodeError):
            self._send(400, b'{"error":"Senal invalida"}', "application/json")

    def _manual_state(self, body: dict[str, object]) -> dict[str, object]:
        sim = self.server.simulation
        if set(body) not in ({"target"}, {"target", "closed"}):
            raise ValueError("Señal inválida")
        now_ns = time.monotonic_ns()
        target = body["target"]
        if target is not None and (not isinstance(target, str) or target not in CENTERS):
            sim.lose_signal()
            raise ValueError("Objetivo inválido")
        if "closed" not in body:
            return sim.step(target, now_ns)
        closed = body["closed"]
        if not isinstance(closed, bool):
            sim.lose_signal()
            raise ValueError("Gesto inválido")
        if target is None:
            sim.lose_signal()
            return sim.state()
        x, y = (0.5, 0.5) if sim.calibrating else CENTERS[target]
        yaw = sim.neutral[0] + (x - 0.5) * HEAD_SPAN_DEG
        pitch = sim.neutral[1] + (y - 0.5) * HEAD_SPAN_DEG
        return sim.pose(yaw, pitch, 1.0, closed, now_ns)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--camera-preview",
        type=int,
        metavar="INDEX",
        help="calibrate and use eye gaze inside the local panel",
    )
    args = parser.parse_args()
    if args.camera_preview is not None and args.camera_preview < 0:
        parser.error("--camera-preview requires a non-negative index")
    server = Server(8766, preview=args.camera_preview is not None)
    if args.camera_preview is None:
        print("Simulación local sin cámara: http://127.0.0.1:8766/", flush=True)
    else:
        server.start_camera(args.camera_preview, default_model_path())
        print(
            "Vista previa local de la cámara, sin entrada al escritorio: http://127.0.0.1:8766/",
            flush=True,
        )
    try:
        server.serve_forever(poll_interval=0.1)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
