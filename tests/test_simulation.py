"""UI host tests use generated targets only, with no camera or desktop adapter."""

import json
import threading
from http.client import HTTPConnection

import pytest

from examples.simulation.benchmark import FRAME_NS, Driver, false_activations, type_phrase
from examples.simulation.server import Server
from fovea.simulation import Simulation


def hold(sim: Simulation, target: str, start: int) -> int:
    for i in range(12):
        sim.step(target, start + i * 100_000_000)
    return start + 1_200_000_000


def choose(sim: Simulation, target: str, start: int) -> int:
    start = hold(sim, target, start)
    return hold(sim, "confirm", start)


def test_simulated_interface_requires_recovery_then_confirmed_actions() -> None:
    sim = Simulation()
    now = hold(sim, "document", 1)
    assert not sim.control.armed and sim.clicks == 0
    now = hold(sim, "resume", now)
    assert sim.control.armed
    now = hold(sim, "document", now)
    assert sim.control.candidate is not None and sim.clicks == 0
    now = hold(sim, "confirm", now)
    assert sim.clicks == 1
    now = choose(sim, "key:ñ", now)
    assert sim.text == "ñ"
    now = choose(sim, "scroll:down", now)
    assert sim.scroll_y == 1
    now = hold(sim, "pause", now)
    assert not sim.control.armed
    now = hold(sim, "resume", now)
    assert sim.control.armed
    assert sim.clicks == 1 and sim.text == "ñ"


def test_signal_loss_releases_drag_and_requires_deliberate_recovery() -> None:
    sim = Simulation()
    now = hold(sim, "resume", 1)
    now = choose(sim, "drag:start", now)
    assert sim.control.dragging
    sim.step(None, now)
    assert not sim.control.armed and not sim.control.dragging
    assert sim.history[-1] == "Arrastre simulado: liberado"
    now = hold(sim, "confirm", now + 100_000_000)
    assert not sim.control.armed and sim.clicks == 0
    hold(sim, "resume", now)
    assert sim.control.armed and not sim.control.dragging


def test_missing_frames_timeout_and_unknown_targets_fail_closed() -> None:
    sim = Simulation()
    now = hold(sim, "resume", 1)
    now = choose(sim, "drag:start", now)
    assert sim.control.dragging
    sim.idle(now + 300_000_000)
    assert not sim.control.armed and not sim.control.dragging
    with pytest.raises(ValueError):
        sim.step("unknown", now + 400_000_000)
    assert not sim.control.armed


def test_pause_during_drag_releases_and_recovery_requires_new_dwell() -> None:
    sim = Simulation()
    now = hold(sim, "resume", 1)
    now = choose(sim, "drag:start", now)
    assert sim.control.dragging
    now = hold(sim, "pause", now)
    assert not sim.control.armed and not sim.control.dragging
    assert sim.history[-1] == "Arrastre simulado: liberado"
    now = hold(sim, "document", now)
    assert not sim.control.armed and sim.control.candidate is None
    now = hold(sim, "resume", now)
    assert sim.control.armed and sim.control.candidate is None
    now = choose(sim, "scroll:right", now)
    assert sim.scroll_x == 1
    now = choose(sim, "key:Enter", now)
    assert sim.text == "\n"
    now = choose(sim, "key:Backspace", now)
    assert sim.text == ""


@pytest.mark.parametrize("finish", ["drag:drop", "cancel"])
def test_simulated_interface_can_finish_or_cancel_drag(finish: str) -> None:
    sim = Simulation()
    now = hold(sim, "resume", 1)
    now = choose(sim, "drag:start", now)
    assert sim.control.dragging
    if finish == "cancel":
        hold(sim, finish, now)
    else:
        choose(sim, finish, now)
    assert not sim.control.dragging
    assert sim.history[-1] == "Arrastre simulado: liberado"


def test_local_http_host_blocks_foreign_origin_and_has_no_camera() -> None:
    server = Server(0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_port

    def call(method: str, path: str, body: bytes | None = None, origin: str | None = None):
        conn = HTTPConnection("127.0.0.1", port, timeout=2)
        headers = {"Host": f"127.0.0.1:{port}"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if origin:
            headers["Origin"] = origin
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        data = response.read()
        status = response.status
        conn.close()
        return status, data

    try:
        status, page = call("GET", "/")
        assert status == 200 and b"no usa la c" in page
        status, data = call("POST", "/api/frame", b'{"target":"resume"}')
        assert status == 200 and json.loads(data)["armed"] is False
        assert call("POST", "/api/frame", b'{"target":"resume"}', "https://example.com")[0] == 403
        assert call("POST", "/api/frame", b'{"target":"unknown"}')[0] == 400
        status, pose_data = call("POST", "/api/frame", b'{"target":"resume","closed":false}')
        assert status == 200 and json.loads(pose_data)["head_mode"] is True
        assert call("POST", "/api/frame", b'{"target":"resume","closed":1}')[0] == 400
        assert not server.simulation.control.armed
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def test_head_gesture_click_drag_scroll_and_recovery() -> None:
    driver = Driver()
    sim = driver.sim
    driver.aim("resume")
    assert not sim.control.armed
    driver.blink("resume")
    assert sim.control.armed
    driver.aim("document")
    assert sim.control.candidate and sim.clicks == 0
    driver.blink("document", 2)
    assert sim.clicks == 0
    driver.blink("document")
    assert sim.clicks == 1
    driver.choose("double_click")
    assert sim.clicks == 3
    driver.choose("scroll:left")
    assert sim.scroll_x == -1
    driver.choose("drag:start")
    assert sim.control.dragging
    driver.choose("drag:drop")
    assert not sim.control.dragging
    driver.choose("drag:start")
    driver.frame("document", confidence=0.79)
    assert not sim.control.armed and not sim.control.dragging
    assert sim.history[-1] == "Arrastre simulado: liberado"
    driver.choose("document")
    assert sim.clicks == 3
    driver.choose("resume")
    assert sim.control.armed
    driver.aim("pause")
    assert not sim.control.armed


def test_calibration_needs_stable_pose_and_deliberate_resume() -> None:
    driver = Driver()
    sim = driver.sim
    driver.choose("resume")
    driver.aim("recalibrate")
    assert sim.calibrating and not sim.control.armed
    for i in range(13):
        sim.pose(4.0 if i % 2 else 6.0, -3.0, 1.0, False, driver.now_ns)
        driver.now_ns += FRAME_NS
    assert sim.calibrating and sim.neutral == (0.0, 0.0)
    for _ in range(11):
        sim.pose(4.0, -3.0, 1.0, False, driver.now_ns)
        driver.now_ns += FRAME_NS
    assert not sim.calibrating and sim.neutral == (4.0, -3.0)
    assert not sim.control.armed
    driver.aim("resume")
    assert not sim.control.armed
    driver.blink("resume")
    assert sim.control.armed


def test_synthetic_phrase_and_false_activations() -> None:
    baseline = type_phrase(False)
    predicted = type_phrase(True)
    assert baseline["words"] == predicted["words"] == 10
    assert int(str(predicted["dwell_or_confirm_steps"])) < int(
        str(baseline["dwell_or_confirm_steps"])
    )
    assert false_activations(2)["false"] == 0
