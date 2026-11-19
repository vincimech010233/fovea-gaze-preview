"""X11 adapter tests use fake libraries and a fake desktop only."""

from __future__ import annotations

import json
import multiprocessing
import os
import subprocess
import sys
import threading

import pytest

from fovea.x11_adapter import X11Adapter, X11Backend, _install_signal_handlers, run_worker


class FakeDesktop:
    def __init__(self, events: object) -> None:
        self.events = events

    def _record(self, event: tuple[object, ...]) -> None:
        if isinstance(self.events, list):
            self.events.append(event)
        else:
            self.events.put(event)  # type: ignore[attr-defined]

    def move(self, x: float, y: float) -> None:
        self._record(("move", x, y))

    def button(self, number: int, pressed: bool) -> None:
        self._record(("button", number, pressed))

    def close(self) -> None:
        self._record(("closed",))


def command(op: str, **fields: object) -> bytes:
    return (json.dumps({"op": op, **fields}) + "\n").encode()


def test_adapter_is_disabled_without_explicit_opt_in() -> None:
    with pytest.raises(ValueError, match="authorization"):
        X11Adapter()


def test_worker_handles_commands_and_releases_on_closed_pipe() -> None:
    read_fd, write_fd = os.pipe()
    events: list[tuple[object, ...]] = []
    thread = threading.Thread(target=run_worker, args=(read_fd, FakeDesktop(events), 0.5))
    thread.start()
    os.write(write_fd, command("move", x=0.3, y=0.7) + command("button", button=1, pressed=True))
    os.close(write_fd)
    thread.join(timeout=2)
    os.close(read_fd)
    assert not thread.is_alive()
    assert events == [("move", 0.3, 0.7), ("button", 1, True), ("button", 1, False), ("closed",)]


def test_worker_timeout_releases_held_button() -> None:
    read_fd, write_fd = os.pipe()
    events: list[tuple[object, ...]] = []
    thread = threading.Thread(target=run_worker, args=(read_fd, FakeDesktop(events), 0.1))
    thread.start()
    os.write(write_fd, command("button", button=1, pressed=True))
    thread.join(timeout=2)
    os.close(write_fd)
    os.close(read_fd)
    assert not thread.is_alive()
    assert events == [("button", 1, True), ("button", 1, False), ("closed",)]


def test_invalid_command_fails_closed_and_releases() -> None:
    read_fd, write_fd = os.pipe()
    events: list[tuple[object, ...]] = []
    os.write(write_fd, command("button", button=1, pressed=True) + command("move", x=2, y=0.5))
    os.close(write_fd)
    with pytest.raises(ValueError, match="coordinates"):
        run_worker(read_fd, FakeDesktop(events))
    os.close(read_fd)
    assert events == [("button", 1, True), ("button", 1, False), ("closed",)]


def _worker_child(read_fd: int, write_fd: int, events: object, handle_term: bool = False) -> None:
    os.close(write_fd)
    if handle_term:
        _install_signal_handlers()
    try:
        run_worker(read_fd, FakeDesktop(events), timeout=3)
    finally:
        os.close(read_fd)


def test_worker_releases_after_sender_process_is_killed() -> None:
    ctx = multiprocessing.get_context("fork")
    events = ctx.Queue()
    read_fd, write_fd = os.pipe()
    worker = ctx.Process(target=_worker_child, args=(read_fd, write_fd, events))
    worker.start()
    sender = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import os,sys,time; os.write(int(sys.argv[1]), "
            'b\'{"op":"button","button":1,"pressed":true}\\n\'); time.sleep(20)',
            str(write_fd),
        ],
        pass_fds=(write_fd,),
    )
    os.close(read_fd)
    os.close(write_fd)
    try:
        assert events.get(timeout=3) == ("button", 1, True)
        sender.kill()
        sender.wait(timeout=3)
        assert events.get(timeout=3) == ("button", 1, False)
        assert events.get(timeout=3) == ("closed",)
        worker.join(timeout=3)
        assert worker.exitcode == 0
    finally:
        if sender.poll() is None:
            sender.kill()
            sender.wait(timeout=3)
        if worker.is_alive():
            worker.terminate()
            worker.join(timeout=3)
        events.close()


def test_worker_sigterm_attempts_release_in_fake_desktop() -> None:
    ctx = multiprocessing.get_context("fork")
    events = ctx.Queue()
    read_fd, write_fd = os.pipe()
    worker = ctx.Process(target=_worker_child, args=(read_fd, write_fd, events, True))
    worker.start()
    os.close(read_fd)
    try:
        os.write(write_fd, command("button", button=1, pressed=True))
        assert events.get(timeout=3) == ("button", 1, True)
        worker.terminate()
        assert events.get(timeout=3) == ("button", 1, False)
        assert events.get(timeout=3) == ("closed",)
        worker.join(timeout=3)
        assert worker.exitcode != 0
    finally:
        os.close(write_fd)
        if worker.is_alive():
            worker.kill()
            worker.join(timeout=3)
        events.close()


class FakeFunction:
    def __init__(self, result: int = 1) -> None:
        self.result = result
        self.calls: list[tuple[object, ...]] = []
        self.argtypes: object = None
        self.restype: object = None

    def __call__(self, *args: object) -> int:
        self.calls.append(args)
        return self.result


class FakeLibrary:
    def __init__(self) -> None:
        self.functions: dict[str, FakeFunction] = {}

    def __getattr__(self, name: str) -> FakeFunction:
        if name not in self.functions:
            result = {"XDisplayWidth": 1920, "XDisplayHeight": 1080, "XDefaultScreen": 0}.get(
                name, 1
            )
            self.functions[name] = FakeFunction(result)
        return self.functions[name]


def test_x11_mapping_with_fake_libraries_only(monkeypatch: pytest.MonkeyPatch) -> None:
    x11, xtst = FakeLibrary(), FakeLibrary()
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    monkeypatch.setenv("DISPLAY", ":fake")
    monkeypatch.setattr(
        "fovea.x11_adapter.ctypes.CDLL", lambda name: x11 if "libX11" in name else xtst
    )
    backend = X11Backend()
    backend.move(0.5, 0.5)
    with pytest.raises(ValueError, match="coordinates"):
        backend.move(float("nan"), 0.5)
    backend.button(1, True)
    backend.button(1, False)
    backend.close()
    with pytest.raises(RuntimeError, match="closed"):
        backend.move(0.5, 0.5)
    assert xtst.XTestFakeMotionEvent.calls == [(1, 0, 960, 540, 0)]
    assert xtst.XTestFakeButtonEvent.calls == [(1, 1, 1, 0), (1, 1, 0, 0)]
    assert len(x11.XSync.calls) == 3


def test_missing_xtest_closes_fake_display(monkeypatch: pytest.MonkeyPatch) -> None:
    x11, xtst = FakeLibrary(), FakeLibrary()
    xtst.XTestQueryExtension.result = 0
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    monkeypatch.setenv("DISPLAY", ":fake")
    monkeypatch.setattr(
        "fovea.x11_adapter.ctypes.CDLL", lambda name: x11 if "libX11" in name else xtst
    )
    with pytest.raises(RuntimeError, match="XTEST"):
        X11Backend()
    assert x11.XCloseDisplay.calls == [(1,)]


def test_release_failure_is_reported_and_other_buttons_are_still_released() -> None:
    class FailedRelease(FakeDesktop):
        def button(self, number: int, pressed: bool) -> None:
            super().button(number, pressed)
            if number == 1 and not pressed:
                raise OSError("fake failure")

    read_fd, write_fd = os.pipe()
    events: list[tuple[object, ...]] = []
    os.write(
        write_fd,
        command("button", button=1, pressed=True) + command("button", button=3, pressed=True),
    )
    os.close(write_fd)
    with pytest.raises(RuntimeError, match="release"):
        run_worker(read_fd, FailedRelease(events))
    os.close(read_fd)
    assert ("button", 3, False) in events
    assert events[-1] == ("closed",)


def test_synthetic_action_translation_never_opens_a_display(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[dict[str, object]] = []
    monkeypatch.setattr(X11Adapter, "send", lambda _self, value: commands.append(value))
    adapter = X11Adapter.__new__(X11Adapter)
    adapter.apply(("left_click", 0.2, 0.3))
    adapter.apply(("button_down", 0.4, 0.5))
    adapter.apply(("button_up", 0.4, 0.5))
    adapter.apply(("scroll", 0.0, 1.0))
    with pytest.raises(ValueError, match="Unsupported"):
        adapter.apply(("key", "ñ"))
    assert commands == [
        {"op": "move", "x": 0.2, "y": 0.3},
        {"op": "button", "button": 1, "pressed": True},
        {"op": "button", "button": 1, "pressed": False},
        {"op": "move", "x": 0.4, "y": 0.5},
        {"op": "button", "button": 1, "pressed": True},
        {"op": "release"},
        {"op": "button", "button": 5, "pressed": True},
        {"op": "button", "button": 5, "pressed": False},
    ]
