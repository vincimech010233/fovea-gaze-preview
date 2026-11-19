"""Iris calibration and movement compensation for the virtual gaze panel."""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from typing import override

import numpy as np
from numpy.typing import NDArray

from examples.simulation.eye_geometry import iris_positions
from examples.simulation.gaze_check import GazeCheck
from examples.simulation.preview import FacePreview
from fovea.simulation import FRAME_TIMEOUT_NS, Simulation
from fovea.webcam.backend import LandmarkObservation
from fovea.webcam.calibration import (
    CALIBRATION_LAYOUT,
    CalibrationModel,
    fit_ridge,
    leave_one_out_error,
)
from fovea.webcam.features import NOSE, EyeBox, GazeFeatures
from fovea.webcam.sampler import PointCollector
from fovea.webcam.smoothing import OneEuroPoint

_POINTS = CALIBRATION_LAYOUT[:5] + CALIBRATION_LAYOUT[6:]
_SAMPLES = 16
_SETTLE_NS = 800_000_000
_MOTION_NS = 5_000_000_000
_RETRY_NS = 2_000_000_000
_RIDGE = 0.002
_MAX_ERROR = 0.12

type Row = NDArray[np.float64]


@dataclass(frozen=True, slots=True)
class MovementCorrection:
    reference: Row
    lower: Row
    upper: Row
    weights: Row

    def apply(self, eye_row: Row, pose: Row) -> Row:
        # Outside the observed range, cap the adjustment instead of extrapolating it.
        return eye_row - (np.clip(pose, self.lower, self.upper) - self.reference) @ self.weights


def _learn_movement(
    samples: list[tuple[Row, Row]],
    reference: Row,
) -> MovementCorrection | None:
    if len(samples) < 16:
        return None
    eyes = np.stack([eye for eye, _pose in samples])
    poses = np.stack([pose for _eye, pose in samples])
    if float(np.ptp(poses, axis=0).max()) < 0.15:
        return None

    train_eyes, train_poses = eyes[::2], poses[::2]
    pose_center = np.median(train_poses, axis=0)
    eye_center = np.median(train_eyes, axis=0)
    design = train_poses - pose_center
    weights = np.linalg.solve(
        design.T @ design + 0.10 * np.eye(design.shape[1]),
        design.T @ (train_eyes - eye_center),
    )
    test_errors = eyes[1::2] - eye_center
    residual = test_errors - (poses[1::2] - pose_center) @ weights
    before = float(np.mean(np.linalg.norm(test_errors, axis=1)))
    after = float(np.mean(np.linalg.norm(residual, axis=1)))
    if not (before - after >= 0.004 and after <= 0.80 * before):
        return None
    return MovementCorrection(
        reference,
        np.minimum(poses.min(axis=0), reference),
        np.maximum(poses.max(axis=0), reference),
        weights,
    )


class GazePreview(FacePreview):
    require_both_eyes = False

    def __init__(self, sim: Simulation) -> None:
        super().__init__(sim)
        self.model: CalibrationModel | None = None
        self._eye_models: dict[int, CalibrationModel] = {}
        self._aim_check: GazeCheck | None = None
        self.aim_result: dict[str, float | int] | None = None
        self.monocular_pointer: tuple[float, float] | None = None
        self._tracked_eyes = 0
        self._frame_times: deque[int] = deque(maxlen=16)
        self.correction: MovementCorrection | None = None
        self._anchors: list[tuple[Row, Row]] = []
        self._collector = PointCollector(_SAMPLES, _SAMPLES)
        self._pose_rows: list[Row] = []
        self._movement: list[tuple[Row, Row]] = []
        self._motion_progress = 0.0
        self._point_since: int | None = None
        self._last_calibration_ns: int | None = None
        self._retry_at = 0
        self._last_gaze_ns: int | None = None
        self._smoother = OneEuroPoint(1.6, 0.04)

    @override
    def observe(
        self, face: LandmarkObservation | None, w: int, h: int, captured_ns: int, now_ns: int
    ) -> None:
        self.monocular_pointer = None
        self._tracked_eyes = 0
        if self._frame_times and now_ns - self._frame_times[-1] > FRAME_TIMEOUT_NS:
            self._frame_times.clear()
        if w >= 2 and h >= 2:
            self._frame_times.append(now_ns)
        super().observe(face, w, h, captured_ns, now_ns)

    def diagnostics(self, now_ns: int) -> tuple[int, float | None, tuple[float, float] | None]:
        if self.last_arrival_ns is None or now_ns - self.last_arrival_ns > FRAME_TIMEOUT_NS:
            return 0, None, None
        frames = self._frame_times
        fps = (
            (len(frames) - 1) * 1e9 / (frames[-1] - frames[0])
            if (len(frames) > 1 and frames[-1] > frames[0])
            else None
        )
        return self._tracked_eyes, fps, self.monocular_pointer

    def calibration(self) -> dict[str, float | int] | None:
        if self._aim_check is not None:
            cue = self._aim_check.cue()
            return {**cue, "validation": True} if cue is not None else None
        if self.model is not None:
            return None
        index = len(self._anchors)
        if index == len(_POINTS):
            return {
                "index": index + 1,
                "total": index + 1,
                "x": 0.5,
                "y": 0.5,
                "progress": self._motion_progress,
                "motion": True,
            }
        target = _POINTS[index]
        return {
            "index": index + 1,
            "total": len(_POINTS),
            "x": target.x,
            "y": target.y,
            "progress": self._collector.count / _SAMPLES,
        }

    @override
    def _hold(self, sample: GazeFeatures, captured_ns: int) -> None:
        self.sim.idle(captured_ns)
        self.sim.tracker.freeze(captured_ns)

    @override
    def _invalid_face(self, message: str, captured_ns: int, face_visible: bool) -> None:
        if not face_visible:
            super()._invalid_face(message, captured_ns, face_visible)
            return
        self.signal = message
        self.eye_transition_ns = None
        # A transient invalid sample must not advance an action or refresh the watchdog.
        self.sim.suspend_gaze(captured_ns)
        self._last_gaze_ns = None

    @override
    def _accept_face(
        self, sample: GazeFeatures, captured_ns: int, face: LandmarkObservation, w: int, h: int
    ) -> None:
        self.signal = ""
        left, right = iris_positions(face.landmarks, w, h)
        left_open = _eye_is_open(sample.left, face.blendshapes.get("eyeblinkleft"))
        right_open = _eye_is_open(sample.right, face.blendshapes.get("eyeblinkright"))
        if not left_open:
            left = None
        if not right_open:
            right = None
        self._tracked_eyes = int(left is not None) + int(right is not None)
        if self._tracked_eyes == 0:
            if not left_open and not right_open and sample.both_eyes and sample.blink:
                self.signal = "Ojos cerrados. Vuelve a abrirlos para seguir."
                self._hold(sample, captured_ns)
            else:
                self._invalid_face("No hay una lectura útil de los iris.", captured_ns, True)
            return
        if sample.tracking in {"LOST", "POOR"}:
            self._invalid_face("La posición de la cara no es fiable.", captured_ns, True)
            return

        nose = face.landmarks[NOSE]
        pose = np.array(
            [
                sample.yaw_deg / 15,
                sample.pitch_deg / 15,
                sample.roll_deg / 15,
                (nose.x - 0.5) * 5,
                (nose.y - 0.5) * 5,
                math.log(sample.face_width) * 3,
            ]
        )
        if left is None or right is None:
            self._invalid_face(
                "Solo se lee un ojo. Las acciones esperan a los dos.", captured_ns, True
            )
            self._one_eye(left, right, pose)
            return

        lx, ly = left
        rx, ry = right
        row = np.array(
            [
                1.0,
                (lx + rx) / 2 - 0.5,
                (ly + ry) / 2,
                lx - rx,
                ly - ry,
                0.0,
                0.0,
                0.0,
                sample.blend_x,
                sample.blend_y,
            ]
        )
        if not np.isfinite(row).all():
            self._invalid_face("La lectura de los ojos es incierta.", captured_ns, True)
            return
        if self.model is None:
            self._collect(row, pose, captured_ns)
            return
        if self.correction is not None:
            row = self.correction.apply(row, pose)
        x, y = (float(row @ self.model.coef_x), float(row @ self.model.coef_y))
        if not (-0.2 <= x <= 1.2 and -0.2 <= y <= 1.2):
            self._invalid_face(
                "Mira dentro del panel. La posición estimada queda fuera.", captured_ns, True
            )
            return
        previous = self._last_gaze_ns
        if previous is None or captured_ns - previous > FRAME_TIMEOUT_NS:
            self._smoother.reset()
        dt = 1 / 15 if previous is None else (captured_ns - previous) / 1e9
        self._last_gaze_ns = captured_ns
        x, y = self._smoother.filter(x, y, dt)
        x, y = float(np.clip(x, 0, 1)), float(np.clip(y, 0, 1))
        if self._aim_check is not None:
            self._aim_check.observe(x, y, captured_ns)
            if self._aim_check.result is not None:
                self.aim_result = self._aim_check.result
                self._aim_check = None
                self.sim.message = "Prueba terminada. Mira Reanudar para volver al panel."
            return

        self.sim.gaze(x, y, captured_ns)
        request = self.sim.gaze_request
        self.sim.gaze_request = None
        if request == "recalibrate":
            self._reset()
        elif request == "aim_test":
            self.start_aim_check()

    def start_aim_check(self) -> None:
        if self.model is not None and self._aim_check is None:
            self.sim.lose_signal()
            self._aim_check = GazeCheck()
            self.aim_result = None

    def cancel_aim_check(self) -> None:
        if self._aim_check is not None:
            self._aim_check = None
            self.sim.lose_signal()
            self.sim.message = "Prueba cancelada. Mira Reanudar para volver al panel."

    def _one_eye(
        self,
        left: tuple[float, float] | None,
        right: tuple[float, float] | None,
        pose: Row,
    ) -> None:
        if self._aim_check is not None:
            return
        eye = left if left is not None else right
        side = 1 if left is not None else -1
        model = self._eye_models.get(side)
        if eye is None or model is None:
            return
        x, y = eye[0] - 0.5, eye[1]
        if self.correction is not None:
            offset = self.correction.apply(np.zeros(10), pose)
            x += offset[1] + side * offset[3] / 2
            y += offset[2] + side * offset[4] / 2
        row = _single_eye_row(x, y)
        sx, sy = float(row @ model.coef_x), float(row @ model.coef_y)
        if 0 <= sx <= 1 and 0 <= sy <= 1:
            self.monocular_pointer = (sx, sy)

    def _collect(self, row: Row, pose: Row, captured_ns: int) -> None:
        if captured_ns < self._retry_at:
            self.signal = "Calibración imprecisa. Volverá a empezar en un momento."
            return
        previous = self._last_calibration_ns
        self._last_calibration_ns = captured_ns
        if previous is not None and captured_ns - previous > FRAME_TIMEOUT_NS:
            self._clear_point()
        if self._point_since is None:
            self._point_since = captured_ns
        elapsed = captured_ns - self._point_since
        if elapsed < _SETTLE_NS:
            return
        if len(self._anchors) == len(_POINTS):
            self._movement.append((row, pose))
            self._motion_progress = min(1.0, (elapsed - _SETTLE_NS) / _MOTION_NS)
            if self._motion_progress >= 1.0:
                self._finish(captured_ns)
            return

        before = self._collector.count
        self._collector.add(row, "GOOD", False)
        if self._collector.count > before:
            self._pose_rows.append(pose)
        if self._collector.done():
            self._anchors.append((self._collector.median(), np.median(self._pose_rows, axis=0)))
            self._clear_point()

    def _finish(self, captured_ns: int) -> None:
        coordinates = [(point.x, point.y) for point in _POINTS]
        self.correction = _learn_movement(self._movement, self._anchors[0][1])
        rows = [
            row if self.correction is None else self.correction.apply(row, pose)
            for row, pose in self._anchors
        ]
        error = leave_one_out_error(rows, coordinates, ridge=_RIDGE)
        if not np.isfinite(error) or error > _MAX_ERROR:
            self._reset()
            self._retry_at = captured_ns + _RETRY_NS
            self.signal = "Calibración imprecisa. Volverá a empezar en un momento."
            return
        counts = {point.label: _SAMPLES for point in _POINTS}
        self.model = fit_ridge(
            rows,
            coordinates,
            counts,
            dict.fromkeys(counts, "GOOD"),
            ridge=_RIDGE,
            targets=_POINTS,
        )
        for side in (1, -1):
            one_eye = [
                _single_eye_row(row[1] + side * row[3] / 2, row[2] + side * row[4] / 2)
                for row in rows
            ]
            if leave_one_out_error(one_eye, coordinates, ridge=_RIDGE) <= _MAX_ERROR:
                self._eye_models[side] = fit_ridge(
                    one_eye,
                    coordinates,
                    counts,
                    dict.fromkeys(counts, "GOOD"),
                    ridge=_RIDGE,
                    targets=_POINTS,
                )
        self.sim.message = "Mirada calibrada. Mira Reanudar hasta llenar la barra."
        self._anchors.clear()
        self._clear_point()

    def _clear_point(self) -> None:
        self._collector = PointCollector(_SAMPLES, _SAMPLES)
        self._pose_rows.clear()
        self._movement.clear()
        self._motion_progress = 0.0
        self._point_since = None

    def _reset(self) -> None:
        self.model = None
        self._eye_models.clear()
        self._aim_check = None
        self.aim_result = None
        self.correction = None
        self._anchors.clear()
        self._clear_point()
        self._last_calibration_ns = None
        self._last_gaze_ns = None
        self._smoother.reset()
        self.sim.lose_signal()


def _eye_is_open(eye: EyeBox, blink_score: float | None) -> bool:
    if not eye.valid or not math.isfinite(eye.ear):
        return False
    if blink_score is not None and 0 <= blink_score <= 1:
        if blink_score >= 0.75:
            return False
        if blink_score <= 0.25 and eye.ear >= 0.085:
            return True
    return eye.ear >= 0.16


def _single_eye_row(x: float, y: float) -> Row:
    row = np.zeros(10)
    row[:3] = (1.0, x, y)
    return row
