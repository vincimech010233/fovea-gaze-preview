"""These tests never open a camera or send operating-system input."""

import math

import pytest

from fovea.events import Dwell, GazePoint, TargetLeave, TrackingState, TrackingStatus
from fovea.synthetic_control import SyntheticControl
from fovea.webcam.targeting import TargetRect, TargetTracker


def frame(control: SyntheticControl, ts: int, target: str, *, confidence: float = 1.0) -> None:
    control.consume(TrackingState(TrackingStatus.ACTIVE, confidence, ts), ts)
    control.consume(GazePoint(0.4, 0.6, confidence, ts, target_id=target), ts)


def test_requires_two_distinct_dwells_and_records_only_synthetic_click() -> None:
    control = SyntheticControl()
    frame(control, 1, "document")
    control.consume(Dwell("document", 1), 1)
    assert control.actions == []
    control.resume()
    frame(control, 2, "document")
    control.consume(Dwell("document", 2), 2)
    assert control.candidate == ("document", 0.4, 0.6)
    assert all(action[0] != "left_click" for action in control.actions)
    frame(control, 3, "confirm")
    control.consume(Dwell("confirm", 3), 3)
    control.consume(Dwell("confirm", 3), 3)
    assert [a for a in control.actions if a[0] == "left_click"] == [("left_click", 0.4, 0.6)]


def test_uncertain_tracking_disarms_and_requires_explicit_resume() -> None:
    control = SyntheticControl()
    control.resume()
    frame(control, 10, "document")
    control.consume(Dwell("document", 10), 10)
    control.consume(TrackingState(TrackingStatus.LOST, 0, 11), 11)
    frame(control, 12, "confirm")
    control.consume(Dwell("confirm", 12), 12)
    assert not control.armed
    assert control.candidate is None
    assert not any(a[0] == "left_click" for a in control.actions)


def test_stale_future_and_out_of_order_events_disarm() -> None:
    for event_ts, now, previous in [(1, 250_000_002, 0), (3, 2, 0), (1, 2, 2)]:
        control = SyntheticControl()
        control.resume()
        frame(control, previous, "document")
        control.consume(TrackingState(TrackingStatus.ACTIVE, 1, event_ts), now)
        assert not control.armed
    control = SyntheticControl()
    control.resume()
    frame(control, 1, "document")
    control.tick(250_000_002)
    assert not control.armed


def test_invalid_or_weak_gaze_and_wrong_target_cannot_select() -> None:
    for confidence, x in [(0.2, 0.4), (1.0, math.nan), (1.0, 1.1)]:
        control = SyntheticControl()
        control.resume()
        control.consume(TrackingState(TrackingStatus.ACTIVE, 1.0, 1), 1)
        control.consume(GazePoint(x, 0.5, confidence, 1, target_id="document"), 1)
        control.consume(Dwell("document", 1), 1)
        assert not control.armed
        assert control.actions == []
    control = SyntheticControl()
    control.resume()
    frame(control, 1, "document")
    control.consume(Dwell("other", 1), 1)
    assert control.candidate is None


def test_cancel_and_leaving_confirmation_clear_candidate() -> None:
    for cancel in ("cancel", "leave"):
        control = SyntheticControl()
        control.resume()
        frame(control, 1, "document")
        control.consume(Dwell("document", 1), 1)
        frame(control, 2, cancel if cancel == "cancel" else "confirm")
        if cancel == "cancel":
            control.consume(Dwell("cancel", 2), 2)
        else:
            control.consume(TargetLeave("confirm", 2), 2)
        assert control.candidate is None


def test_real_target_tracker_requires_full_dwell_for_selection_and_confirmation() -> None:
    control = SyntheticControl()
    control.resume()
    tracker = TargetTracker(
        500,
        0,
        0,
        0,
        (
            TargetRect("document", 0.1, 0.1, 0.2, 0.2),
            TargetRect("confirm", 0.6, 0.6, 0.2, 0.2),
        ),
    )
    for ms in range(0, 1400, 100):
        ts = ms * 1_000_000
        point = (0.2, 0.2) if ms < 700 else (0.7, 0.7)
        update = tracker.update(*point, 1.0, ts)
        control.consume(TrackingState(TrackingStatus.ACTIVE, 1.0, ts), ts)
        control.consume(
            GazePoint(*point, 1.0, ts, target_id=update.match.target_id if update.match else None),
            ts,
        )
        for event in update.events:
            if isinstance(event, (Dwell, TargetLeave)):
                control.consume(event, ts)
        if ms < 1200:
            assert not any(a[0] == "left_click" for a in control.actions)
    assert [a for a in control.actions if a[0] == "left_click"] == [("left_click", 0.2, 0.2)]


def test_candidate_expires_even_while_tracking_remains_active() -> None:
    control = SyntheticControl()
    control.resume()
    frame(control, 0, "document")
    control.consume(Dwell("document", 0), 0)
    for ms in range(100, 5200, 100):
        frame(control, ms * 1_000_000, "confirm")
    control.consume(Dwell("confirm", 5_100_000_000), 5_100_000_000)
    assert control.candidate is None
    assert not any(a[0] == "left_click" for a in control.actions)


def confirmed(control: SyntheticControl, target: str, ts: int) -> None:
    frame(control, ts, target)
    control.consume(Dwell(target, ts), ts)
    frame(control, ts + 1, "confirm")
    control.consume(Dwell("confirm", ts + 1), ts + 1)


def test_drag_drop_requires_confirmation_and_releases_once() -> None:
    control = SyntheticControl()
    control.resume()
    frame(control, 1, "drag:start")
    control.consume(Dwell("drag:start", 1), 1)
    assert not control.dragging
    frame(control, 2, "confirm")
    control.consume(Dwell("confirm", 2), 2)
    assert control.dragging
    confirmed(control, "drag:drop", 3)
    assert not control.dragging
    control.close()
    assert [a[0] for a in control.actions if a[0].startswith("button_")] == [
        "button_down",
        "button_up",
    ]


@pytest.mark.parametrize(
    "failure",
    ["lost", "uncertain", "low_confidence", "stale", "pause", "close", "invalid_gaze", "cancel"],
)
def test_drag_is_released_on_interruption(failure: str) -> None:
    control = SyntheticControl()
    control.resume()
    confirmed(control, "drag:start", 1)
    assert control.dragging
    if failure in ("lost", "uncertain"):
        status = TrackingStatus.LOST if failure == "lost" else TrackingStatus.UNCERTAIN
        control.consume(TrackingState(status, 0.0, 3), 3)
    elif failure == "low_confidence":
        control.consume(TrackingState(TrackingStatus.ACTIVE, 0.1, 3), 3)
    elif failure == "stale":
        control.tick(250_000_003)
    elif failure == "invalid_gaze":
        control.consume(TrackingState(TrackingStatus.ACTIVE, 1.0, 3), 3)
        control.consume(GazePoint(math.nan, 0.5, 1.0, 3), 3)
    elif failure == "cancel":
        frame(control, 3, "cancel")
        control.consume(Dwell("cancel", 3), 3)
    elif failure == "close":
        control.close()
    else:
        control.pause()
    assert not control.dragging
    assert [a[0] for a in control.actions if a[0].startswith("button_")] == [
        "button_down",
        "button_up",
    ]
    control.pause()
    assert sum(a[0] == "button_up" for a in control.actions) == 1


@pytest.mark.parametrize(
    "direction,delta",
    [("up", (0.0, -1.0)), ("down", (0.0, 1.0)), ("left", (-1.0, 0.0)), ("right", (1.0, 0.0))],
)
def test_scroll_is_confirmed_and_synthetic(direction: str, delta: tuple[float, float]) -> None:
    control = SyntheticControl()
    control.resume()
    confirmed(control, "scroll:" + direction, 1)
    assert ("scroll", *delta) in control.actions
    assert not any(a[0] == "left_click" for a in control.actions)


@pytest.mark.parametrize("key", ["a", "ñ", "€", "Enter", "Backspace", "Tab"])
def test_key_requires_confirmation_and_is_synthetic(key: str) -> None:
    control = SyntheticControl()
    control.resume()
    frame(control, 1, "key:" + key)
    control.consume(Dwell("key:" + key, 1), 1)
    assert not any(a[0] == "key" for a in control.actions)
    frame(control, 2, "confirm")
    control.consume(Dwell("confirm", 2), 2)
    assert ("key", key) in control.actions


def test_invalid_operations_never_fall_back_to_click() -> None:
    control = SyntheticControl()
    control.resume()
    for ts, target in enumerate(
        ("key:", "key:Ctrl+Alt+Delete", "scroll:diagonal", "drag:drop", "drag:invalid")
    ):
        confirmed(control, target, ts * 2 + 1)
    assert not any(a[0] in ("left_click", "key", "scroll", "button_down") for a in control.actions)


@pytest.mark.parametrize("interruption", ["out_of_order", "future", "candidate_timeout"])
def test_drag_released_on_time_errors_and_confirmation_timeout(interruption: str) -> None:
    control = SyntheticControl()
    control.resume()
    confirmed(control, "drag:start", 10)
    if interruption == "out_of_order":
        control.consume(TrackingState(TrackingStatus.ACTIVE, 1, 9), 12)
    elif interruption == "future":
        control.consume(TrackingState(TrackingStatus.ACTIVE, 1, 14), 13)
    else:
        frame(control, 12, "drag:drop")
        control.consume(Dwell("drag:drop", 12), 12)
        for i in range(1, 53):
            frame(control, 12 + i * 100_000_000, "confirm")
    assert not control.dragging
    assert not control.armed
    assert sum(a[0] == "button_up" for a in control.actions) == 1


@pytest.mark.parametrize("target", ["scroll:up", "key:ñ", "drag:start"])
def test_no_operation_if_signal_lost_before_confirmation(target: str) -> None:
    control = SyntheticControl()
    control.resume()
    frame(control, 1, target)
    control.consume(Dwell(target, 1), 1)
    control.consume(TrackingState(TrackingStatus.LOST, 0.0, 2), 2)
    frame(control, 3, "confirm")
    control.consume(Dwell("confirm", 3), 3)
    assert not any(a[0] in ("key", "scroll", "button_down", "left_click") for a in control.actions)


def test_drag_blocks_scroll_and_keyboard_until_release() -> None:
    control = SyntheticControl()
    control.resume()
    confirmed(control, "drag:start", 1)
    confirmed(control, "scroll:up", 3)
    confirmed(control, "key:a", 5)
    assert control.dragging
    assert not any(a[0] in ("key", "scroll") for a in control.actions)
    control.close()
    assert not control.dragging


def test_drag_has_a_time_limit_even_when_tracking_remains_valid() -> None:
    control = SyntheticControl()
    control.resume()
    confirmed(control, "drag:start", 1)
    for i in range(1, 153):
        frame(control, 2 + i * 100_000_000, "elsewhere")
    assert not control.dragging
    assert not control.armed
    assert sum(a[0] == "button_up" for a in control.actions) == 1
