"""Opt-in X11 pointer adapter; never started by the simulator or gaze engine.

The worker owns the X11 connection and releases held buttons when its command
pipe closes or the host stops sending heartbeats. No keyboard support yet.
"""

from __future__ import annotations

import contextlib
import ctypes
import json
import math
import os
import select
import signal
import subprocess
import sys
import time
from typing import Protocol

from fovea.x11_guardian import GuardianClient

_TIMEOUT = 0.75
_MAX_LINE = 1024


class PointerBackend(Protocol):
    def move(self, x: float, y: float) -> None: ...
    def button(self, button: int, pressed: bool) -> None: ...
    def close(self) -> None: ...


class X11Backend:
    """Use XTEST only when explicitly instantiated by the worker."""

    def __init__(self) -> None:
        if os.environ.get("XDG_SESSION_TYPE") != "x11" or not os.environ.get("DISPLAY"):
            raise RuntimeError("An explicit X11 session and DISPLAY are required")
        self.x11 = ctypes.CDLL("libX11.so.6")
        self.xtst = ctypes.CDLL("libXtst.so.6")
        self.x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
        self.x11.XOpenDisplay.restype = ctypes.c_void_p
        self.x11.XDefaultScreen.argtypes = [ctypes.c_void_p]
        self.x11.XDefaultScreen.restype = ctypes.c_int
        for name in ("XDisplayWidth", "XDisplayHeight"):
            fn = getattr(self.x11, name)
            fn.argtypes = [ctypes.c_void_p, ctypes.c_int]
            fn.restype = ctypes.c_int
        self.x11.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self.x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
        self.xtst.XTestQueryExtension.argtypes = [ctypes.c_void_p] + [
            ctypes.POINTER(ctypes.c_int)
        ] * 4
        self.xtst.XTestQueryExtension.restype = ctypes.c_int
        self.xtst.XTestFakeMotionEvent.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_ulong,
        ]
        self.xtst.XTestFakeMotionEvent.restype = ctypes.c_int
        self.xtst.XTestFakeButtonEvent.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint,
            ctypes.c_int,
            ctypes.c_ulong,
        ]
        self.xtst.XTestFakeButtonEvent.restype = ctypes.c_int
        self.display = self.x11.XOpenDisplay(None)
        if not self.display:
            raise RuntimeError("Cannot open X11 display")
        try:
            event, error, major, minor = (ctypes.c_int() for _ in range(4))
            if not self.xtst.XTestQueryExtension(
                self.display,
                ctypes.byref(event),
                ctypes.byref(error),
                ctypes.byref(major),
                ctypes.byref(minor),
            ):
                raise RuntimeError("XTEST extension is unavailable")
            self.screen = self.x11.XDefaultScreen(self.display)
            self.width = self.x11.XDisplayWidth(self.display, self.screen)
            self.height = self.x11.XDisplayHeight(self.display, self.screen)
            if self.width < 2 or self.height < 2:
                raise RuntimeError("Invalid X11 screen geometry")
        except BaseException:
            self.x11.XCloseDisplay(self.display)
            self.display = None
            raise

    def move(self, x: float, y: float) -> None:
        if not self.display:
            raise RuntimeError("X11 display is closed")
        if not all(type(v) in (int, float) and math.isfinite(v) and 0 <= v <= 1 for v in (x, y)):
            raise ValueError("Invalid coordinates")
        if not self.xtst.XTestFakeMotionEvent(
            self.display, self.screen, round(x * (self.width - 1)), round(y * (self.height - 1)), 0
        ):
            raise RuntimeError("X11 motion request failed")
        self.x11.XSync(self.display, 0)

    def button(self, button: int, pressed: bool) -> None:
        if not self.display:
            raise RuntimeError("X11 display is closed")
        if type(button) is not int or button not in (1, 3, 4, 5, 6, 7) or type(pressed) is not bool:
            raise ValueError("Invalid button")
        if not self.xtst.XTestFakeButtonEvent(self.display, button, int(pressed), 0):
            raise RuntimeError("X11 button request failed")
        self.x11.XSync(self.display, 0)

    def close(self) -> None:
        if self.display:
            self.x11.XCloseDisplay(self.display)
            self.display = None


def run_worker(
    fd: int,
    backend: PointerBackend,
    timeout: float = _TIMEOUT,
    *,
    guardian: GuardianClient | None = None,
) -> None:
    """Release every tracked press on EOF, timeout, invalid input or exception."""
    held: set[int] = set()
    buffer = b""
    deadline = time.monotonic() + timeout
    next_guard_ping = time.monotonic() + 0.2

    def button(number: int, pressed: bool) -> None:
        if pressed:
            if number in held:
                raise ValueError("Button already held")
            if guardian is not None:
                guardian.send(f"P{number}".encode())
            held.add(number)
            backend.button(number, True)
        else:
            if number not in held:
                raise ValueError("Button not held")
            backend.button(number, False)
            if guardian is not None:
                guardian.send(f"R{number}".encode())
            held.remove(number)

    try:
        while True:
            if guardian is not None and time.monotonic() >= next_guard_ping:
                guardian.send(b"H")
                next_guard_ping = time.monotonic() + 0.2
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            wait = (
                min(remaining, max(0.0, next_guard_ping - time.monotonic()))
                if guardian
                else remaining
            )
            if not select.select([fd], [], [], wait)[0]:
                if guardian is not None:
                    continue
                break
            chunk = os.read(fd, 4096)
            if not chunk:
                break
            buffer += chunk
            if len(buffer) > _MAX_LINE and b"\n" not in buffer:
                raise ValueError("Command too long")
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                if len(line) > _MAX_LINE:
                    raise ValueError("Command too long")
                command = json.loads(line)
                if not isinstance(command, dict):
                    raise ValueError("Invalid command")
                op = command.get("op")
                if op == "heartbeat" and set(command) == {"op"}:
                    pass
                elif op == "release" and set(command) == {"op"}:
                    for number in sorted(held):
                        button(number, False)
                elif op == "stop" and set(command) == {"op"}:
                    return
                elif op == "move" and set(command) == {"op", "x", "y"}:
                    coords = (command["x"], command["y"])
                    if not all(
                        type(v) in (int, float) and math.isfinite(v) and 0 <= v <= 1 for v in coords
                    ):
                        raise ValueError("Invalid coordinates")
                    backend.move(float(coords[0]), float(coords[1]))
                elif op == "button" and set(command) == {"op", "button", "pressed"}:
                    number, pressed = command["button"], command["pressed"]
                    if (
                        type(number) is not int
                        or number not in (1, 3, 4, 5, 6, 7)
                        or type(pressed) is not bool
                    ):
                        raise ValueError("Invalid button")
                    button(number, pressed)
                else:
                    raise ValueError("Invalid command")
                deadline = time.monotonic() + timeout
                if guardian is not None and time.monotonic() >= next_guard_ping:
                    guardian.send(b"H")
                    next_guard_ping = time.monotonic() + 0.2
    finally:
        release_error: Exception | None = None
        for number in sorted(held):
            try:
                backend.button(number, False)
                if guardian is not None:
                    guardian.send(f"R{number}".encode())
            except Exception as exc:
                release_error = exc
        backend.close()
        if release_error is not None:
            raise RuntimeError("Could not release every held button") from release_error


class X11Adapter:
    """Explicit opt-in handle; caller must send heartbeat from its control loop."""

    def __init__(self, *, enabled: bool = False) -> None:
        if not enabled:
            raise ValueError("Real X11 control requires explicit authorization")
        read_fd, write_fd = os.pipe()
        self._status_pipe = os.fdopen(read_fd, "rb", buffering=0)
        os.set_blocking(read_fd, False)
        self._recovery_state: str | None = None
        self._worker_dead_at: float | None = None
        try:
            self.process = subprocess.Popen(
                [sys.executable, "-m", "fovea.x11_adapter", "--worker", str(write_fd)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                pass_fds=(write_fd,),
            )
        except BaseException:
            self._status_pipe.close()
            raise
        finally:
            os.close(write_fd)
        assert self.process.stdout is not None
        if (
            not select.select([self.process.stdout], [], [], 3.0)[0]
            or self.process.stdout.readline() != b"READY\n"
        ):
            self.process.kill()
            self.process.wait()
            self._status_pipe.close()
            raise RuntimeError("X11 worker did not start")

    def recovery_status(self) -> str:
        """Report guardian result without treating missing evidence as success."""
        if self._recovery_state is not None:
            return self._recovery_state
        running = self.process.poll() is None
        if not running and self._worker_dead_at is None:
            self._worker_dead_at = time.monotonic()
        try:
            data = self._status_pipe.read(32)
        except OSError:
            data = b""
        if data is None:
            if running:
                return "running"
            if self._worker_dead_at is not None and time.monotonic() - self._worker_dead_at <= 50:
                return "recovering"
        self._recovery_state = {
            b"released\n": "released",
            b"failed\n": "failed",
        }.get(data, "unknown")
        self._status_pipe.close()
        return self._recovery_state

    def send(self, command: dict[str, object]) -> None:
        if self.recovery_status() != "running" or self.process.stdin is None:
            raise RuntimeError("X11 worker stopped; control is unsafe")
        data = json.dumps(command, allow_nan=False, separators=(",", ":")).encode() + b"\n"
        if len(data) > _MAX_LINE:
            raise ValueError("Command too long")
        try:
            self.process.stdin.write(data)
            self.process.stdin.flush()
        except (OSError, ValueError) as exc:
            raise RuntimeError("X11 worker stopped; control is unsafe") from exc

    def heartbeat(self) -> None:
        self.send({"op": "heartbeat"})

    def apply(self, action: tuple[str, float, float] | tuple[str, str]) -> None:
        """Translate supported synthetic actions; keyboard is deliberately unsupported."""
        kind = action[0]
        if len(action) != 3:
            raise ValueError("Unsupported action")
        x, y = action[1], action[2]
        if kind == "move":
            self.send({"op": "move", "x": x, "y": y})
        elif kind == "left_click":
            self.send({"op": "move", "x": x, "y": y})
            self.send({"op": "button", "button": 1, "pressed": True})
            self.send({"op": "button", "button": 1, "pressed": False})
        elif kind == "button_down":
            self.send({"op": "move", "x": x, "y": y})
            self.send({"op": "button", "button": 1, "pressed": True})
        elif kind == "button_up":
            # Do not move on loss of tracking; drop coordinates need an explicit safe design.
            self.send({"op": "release"})
        elif kind == "scroll":
            buttons = {(0.0, -1.0): 4, (0.0, 1.0): 5, (-1.0, 0.0): 6, (1.0, 0.0): 7}
            if (x, y) not in buttons:
                raise ValueError("Unsupported scroll")
            number = buttons[(x, y)]
            self.send({"op": "button", "button": number, "pressed": True})
            self.send({"op": "button", "button": number, "pressed": False})
        else:
            raise ValueError("Unsupported action")

    def release(self) -> None:
        self.send({"op": "release"})

    def close(self) -> None:
        if self.process.stdin:
            with contextlib.suppress(BrokenPipeError):
                self.process.stdin.close()
        try:
            result = self.process.wait(timeout=2)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("X11 worker did not release safely") from exc
        finally:
            if self.process.stdout:
                self.process.stdout.close()
        if result != 0:
            raise RuntimeError("X11 worker failed; button release is unverified")


def _main() -> None:
    if len(sys.argv) != 3 or sys.argv[1] != "--worker":
        raise SystemExit("Worker-only module; use the explicit adapter API")
    status_fd = int(sys.argv[2])
    _install_signal_handlers()
    backend = X11Backend()
    guardian: GuardianClient | None = None
    try:
        guardian = GuardianClient(status_fd)
        os.close(status_fd)
        status_fd = -1
        print("READY", flush=True)
        run_worker(sys.stdin.fileno(), backend, guardian=guardian)
    finally:
        if status_fd >= 0:
            os.close(status_fd)
        backend.close()
        if guardian is not None:
            guardian.close()


def _install_signal_handlers() -> None:
    def terminate(signum: int, _frame: object) -> None:
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)


if __name__ == "__main__":
    _main()
