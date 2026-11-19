"""Recovery tests run separate processes against an in-memory fake desktop."""

from __future__ import annotations

import multiprocessing
import os
import select
import subprocess
import sys
import time

import pytest

from fovea.x11_adapter import X11Adapter, run_worker
from fovea.x11_guardian import report_result, run_guardian


class FakeBackend:
    def __init__(self, output: int) -> None:
        self.output = output

    def button(self, number: int, pressed: bool) -> None:
        os.write(self.output, f"button {number} {pressed}\n".encode())

    def move(self, _x: float, _y: float) -> None:
        pass

    def close(self) -> None:
        os.write(self.output, b"closed\n")


def _guardian_child(
    read_fd: int,
    write_fd: int,
    ack_read: int,
    ack_write: int,
    pidfd: int,
    event_read: int,
    event_write: int,
    attempts_counter: object,
    failures: int,
    hang: bool,
    extra_close: tuple[int, ...] = (),
    result_write: int | None = None,
) -> None:
    os.close(write_fd)
    os.close(ack_read)
    os.close(event_read)
    for fd in extra_close:
        os.close(fd)

    def factory() -> FakeBackend:
        with attempts_counter.get_lock():  # type: ignore[attr-defined]
            attempts_counter.value += 1  # type: ignore[attr-defined]
            count = attempts_counter.value  # type: ignore[attr-defined]
        os.write(event_write, f"connect {count}\n".encode())
        if hang:
            time.sleep(20)
        if count <= failures:
            raise OSError("fake X11 unavailable")
        return FakeBackend(event_write)

    result = run_guardian(
        read_fd,
        ack_write,
        pidfd,
        factory,
        timeout=0.35,
        attempts=3,
        retry_delay=0.01,
        attempt_timeout=0.1,
    )
    os.write(event_write, b"RECOVERED\n" if result else b"FAILED\n")
    if result_write is not None:
        report_result(result_write, result)
        os.close(result_write)
    for fd in (read_fd, ack_write, pidfd, event_write):
        os.close(fd)


def simulate_recovery(
    *,
    failures: int = 0,
    hang: bool = False,
    kill_worker: bool = True,
    release_before_exit: bool = False,
    mark_press: bool = True,
):
    ctx = multiprocessing.get_context("fork")
    counter = ctx.Value("i", 0)
    read_fd, write_fd = os.pipe()
    ack_read, ack_write = os.pipe()
    event_read, event_write = os.pipe()
    source = (
        "import os,sys,time; w=int(sys.argv[1]); a=int(sys.argv[2]); assert os.read(a,1)==b'R'; "
    )
    if mark_press:
        source += "os.write(w,b'P1\\n'); assert os.read(a,1)==b'A'; "
    if release_before_exit:
        source += "os.write(w,b'R1\\n'); assert os.read(a,1)==b'A'; "
    source += "print('ARMED',flush=True); time.sleep(20)"
    worker = subprocess.Popen(
        [sys.executable, "-c", source, str(write_fd), str(ack_read)],
        pass_fds=(write_fd, ack_read),
        stdout=subprocess.PIPE,
    )
    pidfd = os.pidfd_open(worker.pid)
    result_read, result_write = os.pipe()
    guardian = ctx.Process(
        target=_guardian_child,
        args=(
            read_fd,
            write_fd,
            ack_read,
            ack_write,
            pidfd,
            event_read,
            event_write,
            counter,
            failures,
            hang,
            (result_read,),
            result_write,
        ),
    )
    guardian.start()
    adapter = _adapter_with_pipe(worker, result_read)
    for fd in (read_fd, write_fd, ack_read, ack_write, pidfd, event_write, result_write):
        os.close(fd)
    try:
        assert worker.stdout is not None
        assert worker.stdout.readline() == b"ARMED\n"
        if kill_worker:
            worker.kill()
        worker.wait(timeout=4)
        guardian.join(timeout=5)
        assert not guardian.is_alive()
        assert guardian.exitcode == 0
        log = os.read(event_read, 4096).decode()
        expected = "released" if "RECOVERED\n" in log else "failed"
        assert adapter.recovery_status() == expected
        assert adapter.recovery_status() == expected and adapter._status_pipe.closed
        with pytest.raises(RuntimeError, match="unsafe"):
            adapter.heartbeat()
        return log, counter.value, worker.returncode
    finally:
        if worker.poll() is None:
            worker.kill()
            worker.wait(timeout=3)
        if guardian.is_alive():
            guardian.kill()
            guardian.join(timeout=3)
        if worker.stdout:
            worker.stdout.close()
        os.close(event_read)
        adapter._status_pipe.close()


def test_guardian_recovers_after_worker_sigkill() -> None:
    log, attempts, _ = simulate_recovery()
    assert attempts == 1
    assert "button 1 False\n" in log and "RECOVERED\n" in log


def test_guardian_does_not_release_when_worker_already_released() -> None:
    log, attempts, _ = simulate_recovery(release_before_exit=True)
    assert attempts == 0
    assert "button 1 False\n" not in log and "RECOVERED\n" in log


def test_guardian_retries_when_x11_is_temporarily_unavailable() -> None:
    log, attempts, _ = simulate_recovery(failures=2)
    assert attempts == 3
    assert "button 1 False\n" in log and "RECOVERED\n" in log


def test_guardian_reports_failure_when_x11_stays_unavailable() -> None:
    log, attempts, _ = simulate_recovery(failures=3)
    assert attempts == 3
    assert "FAILED\n" in log and "button 1 False\n" not in log


def test_guardian_times_out_blocked_recovery_calls() -> None:
    log, attempts, _ = simulate_recovery(hang=True)
    assert attempts == 3
    assert "FAILED\n" in log and "button 1 False\n" not in log


def test_guardian_kills_unresponsive_worker_before_recovery() -> None:
    log, attempts, returncode = simulate_recovery(kill_worker=False)
    assert returncode == -9 and attempts == 1
    assert "button 1 False\n" in log and "RECOVERED\n" in log


def test_guardian_stops_unresponsive_worker_even_without_a_held_button() -> None:
    log, attempts, returncode = simulate_recovery(kill_worker=False, mark_press=False)
    assert returncode == -9 and attempts == 0
    assert "RECOVERED\n" in log


def _guarded_worker_child(
    host_read: int,
    host_write: int,
    journal_read: int,
    journal_write: int,
    ack_read: int,
    ack_write: int,
    event_read: int,
    event_write: int,
) -> None:
    for fd in (host_write, journal_read, ack_write, event_read):
        os.close(fd)

    class PipeGuardian:
        def send(self, message: bytes) -> None:
            os.write(journal_write, message + b"\n")
            if os.read(ack_read, 1) != b"A":
                raise RuntimeError("fake guardian unavailable")

    assert os.read(ack_read, 1) == b"R"
    guardian = PipeGuardian()
    run_worker(host_read, FakeBackend(event_write), timeout=3, guardian=guardian)  # type: ignore[arg-type]
    guardian.send(b"Q")


@pytest.mark.parametrize("failure", ["worker", "host"])
def test_actual_worker_journals_and_recovers_on_process_failure(failure: str) -> None:
    ctx = multiprocessing.get_context("fork")
    counter = ctx.Value("i", 0)
    host_read, host_write = os.pipe()
    journal_read, journal_write = os.pipe()
    ack_read, ack_write = os.pipe()
    event_read, event_write = os.pipe()
    worker = ctx.Process(
        target=_guarded_worker_child,
        args=(
            host_read,
            host_write,
            journal_read,
            journal_write,
            ack_read,
            ack_write,
            event_read,
            event_write,
        ),
    )
    worker.start()
    pidfd = os.pidfd_open(worker.pid)
    guardian = ctx.Process(
        target=_guardian_child,
        args=(
            journal_read,
            journal_write,
            ack_read,
            ack_write,
            pidfd,
            event_read,
            event_write,
            counter,
            0,
            False,
            (host_read, host_write),
        ),
    )
    guardian.start()
    for fd in (host_read, journal_read, journal_write, ack_read, ack_write, pidfd, event_write):
        os.close(fd)
    try:
        os.write(host_write, b'{"op":"button","button":1,"pressed":true}\n')
        assert select.select([event_read], [], [], 3)[0]
        assert b"button 1 True\n" in os.read(event_read, 4096)
        if failure == "worker":
            worker.kill()
        else:
            os.close(host_write)
            host_write = -1
        worker.join(timeout=3)
        guardian.join(timeout=4)
        assert not guardian.is_alive()
        log = os.read(event_read, 4096).decode()
        assert "button 1 False\n" in log and "RECOVERED\n" in log
        assert counter.value == (1 if failure == "worker" else 0)
    finally:
        if host_write >= 0:
            os.close(host_write)
        os.close(event_read)
        for proc in (worker, guardian):
            if proc.is_alive():
                proc.kill()
                proc.join(timeout=3)


def test_worker_does_not_press_without_guardian_ack() -> None:
    class RefusingGuardian:
        def send(self, _message: bytes) -> None:
            raise RuntimeError("no acknowledgement")

    class Recorder:
        def __init__(self) -> None:
            self.actions: list[tuple[int, bool]] = []

        def move(self, _x: float, _y: float) -> None:
            pass

        def button(self, number: int, pressed: bool) -> None:
            self.actions.append((number, pressed))

        def close(self) -> None:
            pass

    read_fd, write_fd = os.pipe()
    os.write(write_fd, b'{"op":"button","button":1,"pressed":true}\n')
    os.close(write_fd)
    backend = Recorder()
    try:
        with pytest.raises(RuntimeError, match="acknowledgement"):
            run_worker(read_fd, backend, guardian=RefusingGuardian())  # type: ignore[arg-type]
    finally:
        os.close(read_fd)
    assert backend.actions == []


def test_worker_releases_if_guardian_dies_during_drag() -> None:
    class BrokenGuardian:
        def send(self, message: bytes) -> None:
            if message != b"P1":
                raise RuntimeError("guardian died")

    class Recorder:
        def __init__(self) -> None:
            self.actions: list[tuple[int, bool]] = []

        def move(self, _x: float, _y: float) -> None:
            pass

        def button(self, number: int, pressed: bool) -> None:
            self.actions.append((number, pressed))

        def close(self) -> None:
            pass

    read_fd, write_fd = os.pipe()
    os.write(write_fd, b'{"op":"button","button":1,"pressed":true}\n')
    backend = Recorder()
    try:
        with pytest.raises(RuntimeError):
            run_worker(read_fd, backend, timeout=0.7, guardian=BrokenGuardian())  # type: ignore[arg-type]
    finally:
        os.close(write_fd)
        os.close(read_fd)
    assert backend.actions == [(1, True), (1, False)]


@pytest.mark.parametrize("result,expected", [(True, b"released\n"), (False, b"failed\n")])
def test_guardian_reports_result_over_inherited_pipe(result: bool, expected: bytes) -> None:
    read_fd, write_fd = os.pipe()
    try:
        report_result(write_fd, result)
        assert os.read(read_fd, 32) == expected
    finally:
        os.close(read_fd)
        os.close(write_fd)


def _adapter_with_pipe(worker, read_fd: int) -> X11Adapter:
    os.set_blocking(read_fd, False)
    adapter = X11Adapter.__new__(X11Adapter)
    adapter.process = worker
    adapter._status_pipe = os.fdopen(read_fd, "rb", buffering=0)
    adapter._recovery_state = None
    adapter._worker_dead_at = None
    return adapter


def test_adapter_stays_disarmed_until_guardian_result() -> None:
    class DeadWorker:
        stdin = None

        def poll(self) -> int:
            return -9

    read_fd, write_fd = os.pipe()
    adapter = _adapter_with_pipe(DeadWorker(), read_fd)
    try:
        assert adapter.recovery_status() == "recovering"
        with pytest.raises(RuntimeError, match="unsafe"):
            adapter.send({"op": "move", "x": 0.5, "y": 0.5})
        report_result(write_fd, True)
        assert adapter.recovery_status() == "released"
        with pytest.raises(RuntimeError, match="unsafe"):
            adapter.send({"op": "heartbeat"})
    finally:
        adapter._status_pipe.close()
        os.close(write_fd)


@pytest.mark.parametrize("result", ["failed", "unknown"])
def test_adapter_reports_failure_or_unknown_without_rearming(result: str) -> None:
    class DeadWorker:
        stdin = None

        def poll(self) -> int:
            return -9

    read_fd, write_fd = os.pipe()
    adapter = _adapter_with_pipe(DeadWorker(), read_fd)
    if result == "failed":
        report_result(write_fd, False)
    os.close(write_fd)
    try:
        assert adapter.recovery_status() == result
        with pytest.raises(RuntimeError, match="unsafe"):
            adapter.send({"op": "heartbeat"})
    finally:
        adapter._status_pipe.close()
