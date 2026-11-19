"""Independent X11 worker monitor; invoked only by the opt-in X11 worker."""

from __future__ import annotations

import contextlib
import os
import select
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from typing import Protocol


class RecoveryBackend(Protocol):
    def button(self, button: int, pressed: bool) -> None: ...
    def close(self) -> None: ...


def _recover_once(
    backend_factory: Callable[[], RecoveryBackend], held: set[int], timeout: float
) -> bool:
    """Isolate a potentially blocked X11 recovery call in a killable process."""
    read_fd, write_fd = os.pipe()
    try:
        pid = os.fork()
    except BaseException:
        os.close(read_fd)
        os.close(write_fd)
        raise
    if pid == 0:
        os.close(read_fd)
        try:
            backend = backend_factory()
            try:
                for button in sorted(held):
                    backend.button(button, False)
            finally:
                backend.close()
            os.write(write_fd, b"1")
        except BaseException:
            pass
        finally:
            os._exit(0)
    os.close(write_fd)
    finished = False
    try:
        ready = select.select([read_fd], [], [], timeout)[0]
        if ready:
            finished = True
            return os.read(read_fd, 1) == b"1"
        return False
    finally:
        if not finished:
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)
        os.close(read_fd)
        os.waitpid(pid, 0)


def run_guardian(
    fd: int,
    ack_fd: int,
    pidfd: int,
    backend_factory: Callable[[], RecoveryBackend],
    *,
    timeout: float = 1.5,
    attempts: int = 30,
    retry_delay: float = 0.5,
    attempt_timeout: float = 1.0,
) -> bool:
    """Track possible presses before they occur and recover only after worker exit."""
    held: set[int] = set()
    buffer = b""
    deadline = time.monotonic() + timeout
    terminated = False
    try:
        os.write(ack_fd, b"R")
        while True:
            ready = select.select([fd, pidfd], [], [], max(0.0, deadline - time.monotonic()))[0]
            if pidfd in ready:
                terminated = True
                break
            if fd not in ready:
                break
            chunk = os.read(fd, 128)
            if not chunk:
                break
            buffer += chunk
            if len(buffer) > 128:
                break
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                if line == b"H":
                    pass
                elif line == b"Q" and not held:
                    os.write(ack_fd, b"A")
                    return True
                elif len(line) == 2 and line[:1] in (b"P", b"R") and line[1:] in b"134567":
                    number = int(line[1:])
                    if line[:1] == b"P":
                        held.add(number)
                    else:
                        held.discard(number)
                else:
                    raise ValueError("Invalid guardian message")
                os.write(ack_fd, b"A")
                deadline = time.monotonic() + timeout
    except (OSError, ValueError):
        pass  # Treat a broken journal as worker failure, retaining possible presses.

    if not terminated:
        try:
            signal.pidfd_send_signal(pidfd, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except OSError:
            return False
        if not select.select([pidfd], [], [], 3.0)[0]:
            return False
    if not held:
        return True
    for attempt in range(attempts):
        with contextlib.suppress(Exception):
            if _recover_once(backend_factory, held, attempt_timeout):
                return True
        if attempt + 1 < attempts:
            time.sleep(retry_delay)
    return False


class GuardianClient:
    """Synchronous journal prevents a press before the guardian acknowledges it."""

    def __init__(self, status_fd: int | None = None) -> None:
        args = [sys.executable, "-m", "fovea.x11_guardian", str(os.getpid())]
        if status_fd is not None:
            args.append(str(status_fd))
        self.process = subprocess.Popen(
            args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            pass_fds=(status_fd,) if status_fd is not None else (),
        )
        assert self.process.stdin is not None and self.process.stdout is not None
        if (
            not select.select([self.process.stdout], [], [], 2)[0]
            or os.read(self.process.stdout.fileno(), 1) != b"R"
        ):
            self.process.kill()
            self.process.wait()
            raise RuntimeError("Independent guardian unavailable")

    def send(self, message: bytes) -> None:
        assert self.process.stdin is not None and self.process.stdout is not None
        try:
            os.write(self.process.stdin.fileno(), message + b"\n")
            if (
                not select.select([self.process.stdout], [], [], 0.5)[0]
                or os.read(self.process.stdout.fileno(), 1) != b"A"
            ):
                raise RuntimeError("Independent guardian did not acknowledge")
        except OSError as exc:
            raise RuntimeError("Independent guardian stopped") from exc

    def close(self) -> None:
        assert self.process.stdin is not None and self.process.stdout is not None
        try:
            self.send(b"Q")
        finally:
            self.process.stdin.close()
        try:
            result = self.process.wait(timeout=2)
        except subprocess.TimeoutExpired as exc:
            self.process.stdout.close()
            raise RuntimeError("Guardian recovery still running; state unverified") from exc
        self.process.stdout.close()
        if result:
            raise RuntimeError("Independent guardian reported failure")


def report_result(status_fd: int, ok: bool) -> None:
    with contextlib.suppress(OSError):
        os.write(status_fd, b"released\n" if ok else b"failed\n")


def _main() -> None:
    from fovea.x11_adapter import X11Backend

    if len(sys.argv) not in (2, 3):
        raise SystemExit("Internal guardian only")
    status_fd = int(sys.argv[2]) if len(sys.argv) == 3 else None
    pidfd = os.pidfd_open(int(sys.argv[1]))
    ok = False
    try:
        ok = run_guardian(sys.stdin.fileno(), sys.stdout.fileno(), pidfd, X11Backend)
    finally:
        os.close(pidfd)
        if status_fd is not None:
            report_result(status_fd, ok)
            os.close(status_fd)
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    _main()
