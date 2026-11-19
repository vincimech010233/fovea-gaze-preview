"""Drive the virtual panel from iris landmarks without opening any device."""

from __future__ import annotations

from dataclasses import replace

import pytest

from examples.simulation.gaze_preview import GazePreview
from fovea.simulation import CENTERS, Simulation
from fovea.webcam.backend import LandmarkObservation
from fovea.webcam.features import LEFT_IRIS_RING
from tests.synth import SyntheticLandmark, synthetic_landmarks

_FRAME_NS = 100_000_000
_MOTION_POSES = (
    {"yaw": -6.0},
    {"pitch": 6.0},
    {"shift_x": -0.07},
    {"shift_y": 0.06},
    {"scale": 1.13},
    {"yaw": 14.0},
    {"pitch": -12.0},
    {"shift_x": 0.07},
    {"shift_y": -0.06},
    {"scale": 0.87},
)
_MOVED_POSE = {"yaw": 11.0, "pitch": 3.0, "shift_x": 0.035, "shift_y": 0.03, "scale": 1.08}


class IrisFixture:
    def __init__(self, *, biased_iris: bool = False) -> None:
        self.biased_iris = biased_iris
        self.sim = Simulation()
        self.preview = GazePreview(self.sim)
        self.now_ns = 0

    def look(
        self,
        x: float,
        y: float,
        frames: int,
        *,
        yaw: float = 4.0,
        pitch: float = -3.0,
        shift_x: float = 0.0,
        shift_y: float = 0.0,
        scale: float = 1.0,
        closed: bool = False,
    ) -> None:
        iris_x = (x - 0.5) * 0.75
        iris_y = (y - 0.5) * 0.75
        if self.biased_iris:
            iris_x += 0.006 * (yaw - 4.0) + 0.55 * shift_x + 0.18 * (scale - 1.0)
            iris_y += 0.007 * (pitch + 3.0) + 0.5 * shift_y + 0.22 * (scale - 1.0)
        points = synthetic_landmarks(
            yaw=yaw, pitch=pitch, gaze_dx=iris_x, gaze_dy=iris_y, blink=closed
        )
        if shift_x or shift_y or scale != 1.0:
            points = [
                SyntheticLandmark(
                    0.5 + (point.x - 0.5) * scale + shift_x,
                    0.5 + (point.y - 0.5) * scale + shift_y,
                    point.z,
                )
                for point in points
            ]
        face = LandmarkObservation(points, {})
        for _ in range(frames):
            self.now_ns += _FRAME_NS
            self.preview.observe(face, 640, 480, self.now_ns, self.now_ns)

    def calibrate(self, *, follow_targets: bool = True, learn_movement: bool = False) -> None:
        seen = set()
        for index in range(1, 10):
            cue = self.preview.calibration()
            assert cue is not None
            assert cue["index"] == index and cue["total"] == 9
            point = (cue["x"], cue["y"])
            seen.add(point)
            for _ in range(40):
                self.look(*(point if follow_targets else (0.5, 0.5)), 1)
                current = self.preview.calibration()
                if current is None or current.get("motion") or current["index"] != index:
                    break
            else:
                pytest.fail(f"Calibration did not move on from point {index}")
        assert len(seen) == 9
        cue = self.preview.calibration()
        if follow_targets:
            assert cue is not None and cue.get("motion") is True
            assert (cue["x"], cue["y"]) == (0.5, 0.5)
        for frame in range(64):
            cue = self.preview.calibration()
            if cue is None or not cue.get("motion"):
                break
            motion = _MOTION_POSES[frame % len(_MOTION_POSES)] if learn_movement else {}
            self.look(0.5, 0.5, 1, **motion)
        else:
            pytest.fail("Calibration did not finish its center movement stage")

    def aim(self, target: str, *, frames: int = 32, **motion: float) -> None:
        self.look(*CENTERS[target], frames, **motion)


def test_iris_calibration_drives_a_fixed_head_and_separate_confirmation() -> None:
    fixture = IrisFixture()
    fixture.calibrate()
    sim = fixture.sim
    assert fixture.preview.calibration() is None
    assert not sim.control.armed and sim.clicks == 0

    fixture.aim("resume")
    assert sim.control.armed and sim.clicks == 0
    fixture.aim("confirm")
    assert sim.clicks == 0

    fixture.aim("document")
    assert sim.control.candidate is not None
    assert sim.control.candidate[0] == "document"
    assert sim.clicks == 0

    pointer = sim.pointer
    fixture.look(*CENTERS["document"], 8, yaw=12.0)
    assert sim.pointer == pytest.approx(pointer, abs=0.012)
    assert sim.clicks == 0

    fixture.aim("confirm")
    assert sim.clicks == 1
    fixture.aim("confirm")
    assert sim.clicks == 1


def test_stationary_iris_calibration_is_rejected() -> None:
    fixture = IrisFixture()
    fixture.calibrate(follow_targets=False)
    assert fixture.preview.model is None
    assert fixture.preview.status(fixture.now_ns).startswith("Calibración imprecisa")
    assert not fixture.sim.control.armed and fixture.sim.pointer is None


def test_gaze_recalibration_disarms_and_creates_a_new_wizard() -> None:
    fixture = IrisFixture()
    fixture.calibrate()
    fixture.aim("resume")
    assert fixture.sim.control.armed
    fixture.aim("recalibrate")
    assert not fixture.sim.control.armed
    cue = fixture.preview.calibration()
    assert cue is not None and cue["index"] == 1


def test_closed_eyes_and_missing_face_cannot_confirm_and_release_drag() -> None:
    fixture = IrisFixture()
    fixture.calibrate()
    sim = fixture.sim
    fixture.aim("resume")
    fixture.aim("document")
    fixture.look(*CENTERS["confirm"], 4, closed=True)
    assert not sim.control.armed and sim.clicks == 0

    fixture.aim("resume")
    fixture.aim("drag:start")
    fixture.aim("confirm")
    assert sim.control.dragging
    fixture.now_ns += _FRAME_NS
    fixture.preview.observe(None, 640, 480, fixture.now_ns, fixture.now_ns)
    assert not sim.control.dragging and not sim.control.armed
    assert sim.history[-1] == "Arrastre simulado: liberado"


def test_gaze_confirmation_types_in_virtual_keyboard_only() -> None:
    fixture = IrisFixture()
    fixture.calibrate()
    fixture.aim("resume")
    fixture.aim("kbd:slot1")
    fixture.aim("confirm")
    assert fixture.sim.keyboard.slots()[0] == "h"
    assert fixture.sim.text == ""
    fixture.aim("kbd:slot0")
    assert fixture.sim.text == ""
    fixture.aim("confirm")
    assert fixture.sim.text == "h"
    fixture.aim("kbd:predict0")
    fixture.aim("confirm")
    assert fixture.sim.text == "hola "


def test_same_look_stays_on_target_after_calibrated_head_and_camera_movement() -> None:
    fixed = IrisFixture(biased_iris=True)
    fixed.calibrate()
    fixed.look(0.5, 0.5, 32, **_MOVED_POSE)
    fixed_pointer = fixed.sim.pointer
    assert fixed_pointer is not None
    assert max(abs(value - 0.5) for value in fixed_pointer) > 0.075

    trained = IrisFixture(biased_iris=True)
    trained.calibrate(learn_movement=True)
    trained.look(0.5, 0.5, 32, **_MOVED_POSE)
    assert trained.sim.pointer == pytest.approx((0.5, 0.5), abs=0.045)

    trained.aim("resume", **_MOVED_POSE)
    assert trained.sim.control.armed
    trained.aim("document", **_MOVED_POSE)
    candidate = trained.sim.control.candidate
    assert candidate is not None and candidate[0] == "document"
    assert trained.sim.clicks == 0
    trained.aim("confirm", **_MOVED_POSE)
    assert trained.sim.clicks == 1


def test_brief_obscured_eye_drops_candidate_without_requiring_rearm() -> None:
    fixture = IrisFixture()
    fixture.calibrate()
    fixture.aim("resume")
    fixture.aim("document")
    assert fixture.sim.control.candidate is not None

    obscured = LandmarkObservation(synthetic_landmarks(left_valid=False), {})
    fixture.now_ns += _FRAME_NS
    fixture.preview.observe(obscured, 640, 480, fixture.now_ns, fixture.now_ns)
    assert fixture.sim.control.armed
    assert fixture.sim.control.candidate is None
    assert fixture.sim.clicks == 0

    fixture.look(0.5, 0.5, 5)
    fixture.aim("document")
    fixture.aim("confirm")
    assert fixture.sim.clicks == 1

    fixture.aim("drag:start")
    fixture.aim("confirm")
    assert fixture.sim.control.dragging
    for _ in range(3):
        fixture.now_ns += _FRAME_NS
        fixture.preview.observe(obscured, 640, 480, fixture.now_ns, fixture.now_ns)
    assert not fixture.sim.control.armed and not fixture.sim.control.dragging


def test_one_missing_iris_moves_only_the_preview() -> None:
    fixture = IrisFixture()
    fixture.calibrate()
    fixture.aim("resume")
    fixture.aim("document")
    assert fixture.sim.control.candidate is not None
    x, y = CENTERS["confirm"]
    points = synthetic_landmarks(
        yaw=4, pitch=-3, gaze_dx=(x - 0.5) * 0.75, gaze_dy=(y - 0.5) * 0.75
    )
    for index in LEFT_IRIS_RING:
        points[index] = replace(points[index], x=float("nan"))
    partial = LandmarkObservation(points, {})
    for _ in range(12):
        fixture.now_ns += _FRAME_NS
        fixture.preview.observe(partial, 640, 480, fixture.now_ns, fixture.now_ns)
    eyes, fps, pointer = fixture.preview.diagnostics(fixture.now_ns)
    assert eyes == 1 and fps == pytest.approx(10.0)
    assert pointer == pytest.approx((x, y), abs=0.035)
    assert fixture.sim.control.candidate is None
    assert not fixture.sim.control.armed and fixture.sim.clicks == 0
    assert fixture.preview.diagnostics(fixture.now_ns + 300_000_000) == (0, None, None)


def test_aim_check_runs_without_sending_actions_and_reports_new_targets() -> None:
    fixture = IrisFixture()
    fixture.calibrate()
    fixture.aim("resume")
    fixture.aim("document")
    assert fixture.sim.control.candidate is not None
    fixture.aim("aim_test", frames=20)
    assert fixture.preview.aim_result is None
    count = 0
    while (cue := fixture.preview.calibration()) is not None:
        assert cue.get("validation")
        fixture.look(cue["x"], cue["y"], 1)
        assert not fixture.sim.control.armed and fixture.sim.clicks == 0
        count += 1
        assert count < 120
    assert fixture.preview.aim_result is not None
    assert fixture.preview.aim_result["points"] == 4
    assert fixture.preview.aim_result["samples"] == 40
    assert fixture.preview.aim_result["p90"] < 0.04
    fixture.preview.start_aim_check()
    fixture.preview.cancel_aim_check()
    assert fixture.preview.calibration() is None
    assert not fixture.sim.control.armed and fixture.sim.clicks == 0
    fixture.aim("resume")
    assert fixture.sim.control.armed


def test_narrow_open_eyes_can_be_used_when_model_reports_no_blink() -> None:
    fixture = IrisFixture()
    fixture.calibrate()
    points = synthetic_landmarks(blink=True)
    open_scores = LandmarkObservation(points, {"eyeblinkleft": 0.1, "eyeblinkright": 0.1})
    fixture.now_ns += _FRAME_NS
    fixture.preview.observe(open_scores, 640, 480, fixture.now_ns, fixture.now_ns)
    assert fixture.preview.diagnostics(fixture.now_ns)[0] == 2
    assert fixture.sim.pointer is not None and fixture.sim.clicks == 0

    closed_scores = LandmarkObservation(points, {"eyeblinkleft": 0.95, "eyeblinkright": 0.95})
    fixture.now_ns += _FRAME_NS
    fixture.preview.observe(closed_scores, 640, 480, fixture.now_ns, fixture.now_ns)
    assert fixture.preview.diagnostics(fixture.now_ns)[0] == 0
    assert fixture.sim.clicks == 0


def test_small_edge_jitter_keeps_fixation_but_does_not_click() -> None:
    sim = Simulation()
    for i in range(12):
        sim.gaze(*CENTERS["resume"], i * _FRAME_NS)
    assert sim.control.armed
    _, y = CENTERS["document"]
    sim.gaze(*CENTERS["document"], 12 * _FRAME_NS)
    for i in range(13, 24):
        sim.gaze(0.827 if i % 2 else 0.823, y, i * _FRAME_NS)
    assert sim.control.candidate is not None and sim.control.candidate[0] == "document"
    assert sim.clicks == 0
