"""Camera-free interaction prototype: records actions, never controls the OS."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from fovea.events import Dwell, GazePoint, TargetLeave, TrackingState, TrackingStatus

_MAX_AGE_NS = 250_000_000
_CONFIRM_TIMEOUT_NS = 5_000_000_000
_DRAG_TIMEOUT_NS = 15_000_000_000
_KEYS = {"Enter", "Backspace", "Tab", "Escape", "Space", "Delete", "Left", "Right", "Up", "Down"}
_SCROLL = {"up": (0.0, -1.0), "down": (0.0, 1.0), "left": (-1.0, 0.0), "right": (1.0, 0.0)}


@dataclass
class SyntheticControl:
    """Select a target with dwell, then dwell on confirm; starts disarmed."""

    actions: list[tuple[str, float, float] | tuple[str, str]] = field(
        default_factory=list, init=False
    )
    armed: bool = field(default=False, init=False)
    candidate: tuple[str, float, float] | None = field(default=None, init=False)
    _tracking_ns: int | None = field(default=None, init=False)
    _gaze: GazePoint | None = field(default=None, init=False)
    _last_ns: int | None = field(default=None, init=False)
    _candidate_ns: int | None = field(default=None, init=False)
    _pointer: tuple[float, float] | None = field(default=None, init=False)
    _drag_started_ns: int | None = field(default=None, init=False)
    dragging: bool = field(default=False, init=False)

    def resume(self) -> None:
        """Explicitly arm; fresh tracking and gaze are still required."""
        self.pause()
        self.armed = True

    def pause(self) -> None:
        self._release()
        self.armed = False
        self.cancel_candidate()
        self._tracking_ns = None
        self._gaze = None

    def cancel_candidate(self) -> None:
        self.candidate = None
        self._candidate_ns = None

    def _release(self, position: tuple[float, float] | None = None) -> None:
        if self.dragging:
            assert self._pointer is not None
            x, y = position if position is not None else self._pointer
            self.actions.append(("button_up", x, y))
            self.dragging = False
            self._drag_started_ns = None

    def close(self) -> None:
        """Release any held synthetic button and disarm on host shutdown."""
        self.pause()

    def tick(self, now_ns: int) -> None:
        """Disarm when the host has not received a fresh tracking state."""
        if self._tracking_ns is not None and not 0 <= now_ns - self._tracking_ns <= _MAX_AGE_NS:
            self.pause()
        if self._drag_started_ns is not None and now_ns - self._drag_started_ns > _DRAG_TIMEOUT_NS:
            self.pause()
        if self._candidate_ns is not None and now_ns - self._candidate_ns > _CONFIRM_TIMEOUT_NS:
            if self.dragging:
                self.pause()
            self.candidate = None
            self._candidate_ns = None

    def consume(self, event: TrackingState | GazePoint | Dwell | TargetLeave, now_ns: int) -> None:
        """Consume Fovea events using the same wall-clock time base as their timestamps."""
        ts = event.timestamp_ns
        if not 0 <= now_ns - ts <= _MAX_AGE_NS or (
            self._last_ns is not None and ts < self._last_ns
        ):
            self.pause()
            return
        self._last_ns = ts
        self.tick(now_ns)
        if isinstance(event, TrackingState):
            if event.status != TrackingStatus.ACTIVE or not 0.8 <= event.confidence <= 1.0:
                self.pause()
            elif self.armed:
                self._tracking_ns = ts
                self._gaze = None
            return
        if not self.armed or self._tracking_ns != ts:
            return
        if isinstance(event, GazePoint):
            if not 0.8 <= event.confidence <= 1.0 or not all(
                math.isfinite(v) and 0 <= v <= 1 for v in (event.x, event.y)
            ):
                self.pause()
            else:
                self._gaze = event
                self._pointer = (event.x, event.y)
                self.actions.append(("move", event.x, event.y))
        elif isinstance(event, TargetLeave):
            if self.candidate and event.id == "confirm":
                self.candidate = None
                self._candidate_ns = None
        elif isinstance(event, Dwell) and self._gaze is not None and self._gaze.timestamp_ns == ts:
            if self._gaze.target_id != event.id:
                return
            if event.id == "cancel":
                self._release()
                self.candidate = None
                self._candidate_ns = None
            elif event.id == "confirm" and self.candidate is not None:
                self._confirm(ts)
            elif event.id != "confirm":
                self.candidate = (event.id, self._gaze.x, self._gaze.y)
                self._candidate_ns = ts

    def confirm_gesture(self, timestamp_ns: int) -> None:
        """Confirm against the fresh sample on the original candidate."""
        self.tick(timestamp_ns)
        if (
            self.armed
            and self.candidate is not None
            and self._gaze is not None
            and self._gaze.timestamp_ns == self._tracking_ns == timestamp_ns
            and self._gaze.target_id == self.candidate[0]
        ):
            self._confirm(timestamp_ns)

    def _confirm(self, ts: int) -> None:
        assert self.candidate is not None
        target, x, y = self.candidate
        if target == "drag:start" and not self.dragging:
            self.actions.append(("button_down", x, y))
            self._pointer = (x, y)
            self.dragging = True
            self._drag_started_ns = ts
        elif target == "drag:drop" and self.dragging:
            self._release((x, y))
        elif target.startswith("scroll:") and not self.dragging:
            direction = target.removeprefix("scroll:")
            if direction in _SCROLL:
                dx, dy = _SCROLL[direction]
                self.actions.append(("scroll", dx, dy))
        elif target.startswith("key:") and not self.dragging:
            key = target.removeprefix("key:")
            if key in _KEYS or (len(key) == 1 and key.isprintable()):
                self.actions.append(("key", key))
        elif target.startswith("kbd:") and not self.dragging:
            self.actions.append(("keyboard", target.removeprefix("kbd:")))
        elif target == "double_click" and not self.dragging:
            self.actions.append(("double_click", x, y))
        elif not self.dragging and not target.startswith(("drag:", "scroll:", "key:")):
            self.actions.append(("left_click", x, y))
        self.candidate = None
        self._candidate_ns = None
