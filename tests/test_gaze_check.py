"""Exercise the held-out aiming check without a camera."""

from __future__ import annotations

import pytest

from examples.simulation.gaze_check import GazeCheck
from fovea.webcam.calibration import CALIBRATION_LAYOUT

_FRAME_NS = 100_000_000


def test_reports_normalized_gaze_error_on_points_excluded_from_calibration() -> None:
    check = GazeCheck()
    calibration = {(point.x, point.y) for point in CALIBRATION_LAYOUT}
    seen = set()
    timestamp = 0

    for index in range(1, 5):
        cue = check.cue()
        assert cue is not None and cue["index"] == index
        point = (cue["x"], cue["y"])
        assert point not in calibration
        seen.add(point)
        for frame in range(18):
            error = 0.06 if frame < 16 else 0.13
            check.observe(point[0] + error, point[1], timestamp)
            timestamp += _FRAME_NS

    assert len(seen) == 4 and check.cue() is None
    assert check.result == pytest.approx({"mean": 0.074, "p90": 0.13, "samples": 40, "points": 4})
    check.observe(0.0, 0.0, timestamp)
    assert check.result["samples"] == 40


def test_gap_restarts_only_the_current_point_and_invalid_samples_do_not_progress() -> None:
    check = GazeCheck()
    cue = check.cue()
    assert cue is not None
    for frame in range(18):
        check.observe(cue["x"], cue["y"], frame * _FRAME_NS)
    cue = check.cue()
    assert cue is not None and cue["index"] == 2
    for frame in range(18, 29):
        check.observe(cue["x"], cue["y"], frame * _FRAME_NS)
    cue = check.cue()
    assert cue is not None and cue["progress"] == 0.3

    check.observe(cue["x"], cue["y"], 27 * _FRAME_NS)
    check.observe(float("nan"), cue["y"], 29 * _FRAME_NS)
    check.observe(cue["x"], 1.25, 30 * _FRAME_NS)
    assert check.cue() == cue

    check.observe(cue["x"], cue["y"], 31 * _FRAME_NS)
    assert check.cue() == cue | {"progress": 0.0}
    assert check.result is None
