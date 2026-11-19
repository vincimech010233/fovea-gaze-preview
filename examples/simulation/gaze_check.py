"""Measure gaze accuracy on held-out targets without executing actions."""

from __future__ import annotations

import math
from statistics import mean

from fovea.simulation import FRAME_TIMEOUT_NS

_POINTS = ((0.27, 0.28), (0.73, 0.28), (0.73, 0.72), (0.27, 0.72))
_SETTLE_NS = 800_000_000
_SAMPLES_PER_POINT = 10


class GazeCheck:
    def __init__(self) -> None:
        self.result: dict[str, float | int] | None = None
        self._index = 0
        self._point_start_ns: int | None = None
        self._last_sample_ns: int | None = None
        self._current: list[float] = []
        self._errors: list[float] = []

    def cue(self) -> dict[str, float | int] | None:
        if self.result is not None:
            return None
        x, y = _POINTS[self._index]
        return {
            "x": x,
            "y": y,
            "index": self._index + 1,
            "total": len(_POINTS),
            "progress": len(self._current) / _SAMPLES_PER_POINT,
        }

    def observe(self, x: float, y: float, timestamp_ns: int) -> None:
        if self.result is not None or not (0 <= x <= 1 and 0 <= y <= 1):
            return
        previous = self._last_sample_ns
        if previous is not None and timestamp_ns <= previous:
            return
        if self._point_start_ns is None or (
            previous is not None and timestamp_ns - previous > FRAME_TIMEOUT_NS
        ):
            self._point_start_ns = timestamp_ns
            self._current.clear()
        self._last_sample_ns = timestamp_ns
        if timestamp_ns - self._point_start_ns < _SETTLE_NS:
            return

        target_x, target_y = _POINTS[self._index]
        self._current.append(math.hypot(x - target_x, y - target_y))
        if len(self._current) < _SAMPLES_PER_POINT:
            return
        self._errors.extend(self._current)
        self._current.clear()
        self._point_start_ns = None
        self._index += 1
        if self._index == len(_POINTS):
            errors = sorted(self._errors)
            self.result = {
                "mean": mean(errors),
                "p90": errors[math.ceil(0.9 * len(errors)) - 1],
                "samples": len(errors),
                "points": self._index,
            }
