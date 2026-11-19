"""Exercise the optional preview with generated landmarks and fake camera objects."""

from __future__ import annotations

import json
import threading
import time
from dataclasses import replace
from http.client import HTTPConnection
from pathlib import Path

import numpy as np
import pytest

from examples.simulation import preview
from examples.simulation.server import Server
from fovea.simulation import CENTERS, HEAD_SPAN_DEG, Simulation
from fovea.webcam.backend import LandmarkObservation
from tests.synth import synthetic_landmarks

FRAME_NS = 100_000_000


def test_face_observations_drive_only_virtual_actions() -> None:
    sim = Simulation()
    face_preview = preview.FacePreview(sim)
    now_ns = 0

    def hold(target: str, frames: int, *, closed: bool = False) -> None:
        nonlocal now_ns
        x, y = CENTERS[target] if target else (0.5, 0.5)
        landmarks = synthetic_landmarks(
            yaw=(x - 0.5) * HEAD_SPAN_DEG + 4,
            pitch=(y - 0.5) * HEAD_SPAN_DEG - 3,
            blink=closed,
        )
        face = LandmarkObservation(landmarks, {})
        for _ in range(frames):
            now_ns += FRAME_NS
            face_preview.observe(face, 640, 480, now_ns, now_ns)

    hold("", 6)
    assert sim.calibrating and sim.progress == pytest.approx(0.5)
    hold("", 5)
    assert sim.neutral == pytest.approx((4, -3))
    assert not sim.calibrating and not sim.control.armed
    assert sim.pointer == (0.5, 0.5) and sim.confidence == 1.0
    assert sim.message.startswith("Centro calibrado.")
    assert face_preview.status(now_ns) == ""
    assert face_preview.status(now_ns + 300_000_000).startswith("Se interrumpieron")
    hold("resume", 11)
    assert not sim.control.armed
    hold("resume", 8, closed=True)
    hold("resume", 1)
    assert sim.control.armed

    hold("document", 11)
    hold("document", 2, closed=True)
    hold("document", 1)
    assert sim.clicks == 0
    hold("document", 8, closed=True)
    assert sim.clicks == 0
    hold("document", 1)
    assert sim.clicks == 1

    hold("drag:start", 11)
    hold("drag:start", 8, closed=True)
    hold("drag:start", 1)
    assert sim.control.dragging
    face_preview.observe(None, 0, 0, now_ns + FRAME_NS, now_ns + FRAME_NS)
    assert not sim.control.armed and not sim.control.dragging
    assert sim.history[-1] == "Arrastre simulado: liberado"


@pytest.mark.parametrize("invalid", ["eye_missing", "too_far", "nonfinite", "stale", "old"])
def test_ambiguous_or_stale_observations_disarm(invalid: str) -> None:
    sim = Simulation()
    face_preview = preview.FacePreview(sim)
    points = synthetic_landmarks()
    now_ns = 1_000_000_000
    face_preview.observe(LandmarkObservation(points, {}), 640, 480, now_ns, now_ns)
    sim.control.resume()
    now_ns += FRAME_NS
    captured_ns = now_ns
    if invalid == "eye_missing":
        points = synthetic_landmarks(right_valid=False)
    elif invalid == "too_far":
        points = synthetic_landmarks(face_width=0.04)
    elif invalid == "nonfinite":
        points[1] = replace(points[1], x=float("nan"))
    elif invalid == "stale":
        now_ns += 300_000_000
    elif invalid == "old":
        captured_ns -= FRAME_NS
    face_preview.observe(LandmarkObservation(points, {}), 640, 480, captured_ns, now_ns)
    assert not sim.control.armed and sim.pointer is None


@pytest.mark.parametrize(
    "frames,move_away,expected",
    [(8, False, 1), (7, False, 0), (8, True, 0)],
)
def test_eye_transition_does_not_confirm_without_a_long_bilateral_blink(
    frames: int, move_away: bool, expected: int
) -> None:
    sim = Simulation()
    sim.head_mode = True
    face_preview = preview.FacePreview(sim)
    face_preview.needs_center = False
    sim.control.resume()
    now_ns = 0

    def send(target: str, *, low_left: bool = False, low_right: bool = False) -> None:
        nonlocal now_ns
        x, y = CENTERS[target]
        face = LandmarkObservation(
            synthetic_landmarks(
                yaw=(x - 0.5) * HEAD_SPAN_DEG,
                pitch=(y - 0.5) * HEAD_SPAN_DEG,
                left_closed=low_left,
                right_closed=low_right,
            ),
            {},
        )
        face_preview.observe(face, 640, 480, now_ns, now_ns)
        now_ns += FRAME_NS

    for _ in range(11):
        send("document")
    assert sim.control.candidate and sim.clicks == 0
    send("document", low_left=True)
    assert sim.control.armed and sim.clicks == 0
    for _ in range(frames):
        send("document", low_left=True, low_right=True)
    send("pause" if move_away else "document", low_left=True)
    assert sim.clicks == 0
    send("document")
    assert sim.clicks == expected
    assert sim.control.armed is not move_away


def test_sustained_disagreement_disarms() -> None:
    sim = Simulation()
    face_preview = preview.FacePreview(sim)
    sim.control.resume()
    face = LandmarkObservation(synthetic_landmarks(left_closed=True), {})
    for now_ns in (0, 100_000_000):
        face_preview.observe(face, 640, 480, now_ns, now_ns)
        assert sim.control.armed
    face_preview.observe(face, 640, 480, 200_000_000, 200_000_000)
    assert not sim.control.armed and sim.clicks == 0


def test_preview_signals_never_accept_manual_targets_and_expire_with_tab() -> None:
    with Server(0, preview=True) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def post(data: dict[str, object]) -> tuple[int, bytes]:
            connection = HTTPConnection("127.0.0.1", server.server_port, timeout=2)
            try:
                connection.request(
                    "POST", "/api/frame", json.dumps(data), {"Content-Type": "application/json"}
                )
                response = connection.getresponse()
                return response.status, response.read()
            finally:
                connection.close()

        try:
            assert post({"target": "resume", "closed": False})[0] == 400
            status, state = post({"visible": True})
            assert status == 200 and len(json.loads(state)["preview_layout"]) == len(CENTERS) - 1
            assert server.visible_until_ns > time.monotonic_ns()
            with server.lock:
                server.simulation.control.resume()
                server.visible_until_ns = time.monotonic_ns() - 1
            status, state = post({"visible": True})
            assert status == 200 and not json.loads(state)["armed"]
            assert post({"visible": False})[0] == 200
            assert server.visible_until_ns == 0
        finally:
            server.shutdown()
            thread.join(timeout=2)


def test_camera_preview_fails_before_opening_camera_without_model(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def unexpected_open(*_args: object, **_kwargs: object) -> None:
        pytest.fail("Camera and estimator must wait for a verified model")

    monkeypatch.setattr(preview, "Webcam", unexpected_open)
    monkeypatch.setattr(preview, "FaceLandmarkEstimator", unexpected_open)
    with pytest.raises(FileNotFoundError, match="Face landmarker model not found"):
        preview.read_camera(0, tmp_path / "missing.task", threading.Event(), unexpected_open)


def test_camera_preview_passes_landmarks_to_panel_and_closes_fake_resources(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    events = []
    stop = threading.Event()
    face = LandmarkObservation(synthetic_landmarks(), {})
    model = tmp_path / "fake.task"

    class Camera:
        def connect(self) -> None:
            events.append("connect")

        def read(self) -> np.ndarray:
            return np.zeros((480, 640, 3), dtype=np.uint8)

        def disconnect(self) -> None:
            events.append("disconnect")

    class Estimator:
        def process(self, _frame: np.ndarray) -> LandmarkObservation:
            return face

        def close(self) -> None:
            events.append("close_estimator")

    def receive(observation: LandmarkObservation | None, w: int, h: int, ts: int) -> None:
        assert observation is face and (w, h) == (640, 480) and ts > 0
        events.append("observation")
        stop.set()

    monkeypatch.setattr(preview, "verify_face_landmarker", lambda path: events.append(path))
    monkeypatch.setattr(preview, "Webcam", lambda *_args, **_kwargs: Camera())
    monkeypatch.setattr(preview, "FaceLandmarkEstimator", lambda **_kwargs: Estimator())
    preview.read_camera(0, model, stop, receive)
    assert events == [model, "connect", "observation", "disconnect", "close_estimator"]
