"""Local preview source. It only passes face observations to the virtual panel."""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from pathlib import Path

from fovea.simulation import FRAME_TIMEOUT_NS, Simulation
from fovea.webcam.backend import LandmarkObservation
from fovea.webcam.camera import Webcam
from fovea.webcam.engine import GazeSettings
from fovea.webcam.features import (
    GazeFeatures,
    extract_features,
    face_width,
    try_estimate_head_pose,
)
from fovea.webcam.landmarks import FaceLandmarkEstimator
from fovea.webcam.model import verify_face_landmarker

_SETTINGS = GazeSettings()
_EYE_TRANSITION_NS = 120_000_000
type ObservationReceiver = Callable[[LandmarkObservation | None, int, int, int], None]


class FacePreview:
    require_both_eyes = True

    def __init__(self, sim: Simulation) -> None:
        self.sim = sim
        self.needs_center = True
        self.last_observation_ns: int | None = None
        self.last_arrival_ns: int | None = None
        self.eye_transition_ns: int | None = None
        self.signal = "Esperando fotogramas."

    def status(self, now_ns: int) -> str:
        if self.last_arrival_ns is not None and now_ns - self.last_arrival_ns > FRAME_TIMEOUT_NS:
            return "Se interrumpieron los fotogramas. En pausa."
        return self.signal

    def observe(
        self, face: LandmarkObservation | None, w: int, h: int, captured_ns: int, now_ns: int
    ) -> None:
        self.last_arrival_ns = now_ns
        previous = self.last_observation_ns
        if not 0 <= now_ns - captured_ns <= FRAME_TIMEOUT_NS or (
            previous is not None and captured_ns <= previous
        ):
            self.signal = "Fotogramas atrasados. En pausa."
            self.sim.lose_signal()
            return
        self.last_observation_ns = captured_ns
        sample = _face_features(face, w, h, require_both_eyes=self.require_both_eyes)
        if isinstance(sample, str) or face is None:
            message = sample if isinstance(sample, str) else "No se detecta la cara."
            self._invalid_face(message, captured_ns, face is not None)
            return
        self._accept_face(sample, captured_ns, face, w, h)

    def _accept_face(
        self, sample: GazeFeatures, captured_ns: int, face: LandmarkObservation, w: int, h: int
    ) -> None:
        left = sample.left.ear < _SETTINGS.blink_ear
        right = sample.right.ear < _SETTINGS.blink_ear
        closed = left if left == right else None
        if closed is None and self.eye_transition_ns is None:
            self.eye_transition_ns = captured_ns
        transition = self.eye_transition_ns
        if transition is not None and captured_ns - transition > _EYE_TRANSITION_NS:
            self.sim.lose_signal()
        if closed is None:
            self.signal = "Párpados en transición. Esperando una señal clara."
            self._hold(sample, captured_ns)
            return
        self.signal = ""
        self.eye_transition_ns = None
        self._drive(sample, closed, captured_ns, face, w, h)

    def _invalid_face(self, message: str, captured_ns: int, face_visible: bool) -> None:
        self.signal = message
        self.eye_transition_ns = None
        self.sim.lose_signal()

    def _hold(self, sample: GazeFeatures, captured_ns: int) -> None:
        self.sim.hold_eye_transition(sample.yaw_deg, sample.pitch_deg, captured_ns)

    def _drive(
        self,
        sample: GazeFeatures,
        closed: bool,
        captured_ns: int,
        face: LandmarkObservation,
        w: int,
        h: int,
    ) -> None:
        self.sim.idle(captured_ns)
        if self.needs_center and not self.sim.calibrating:
            self.sim.begin_calibration()
        self.sim.pose(sample.yaw_deg, sample.pitch_deg, 1.0, closed, captured_ns)
        if not self.sim.calibrating:
            self.needs_center = False


def _face_features(
    face: LandmarkObservation | None, w: int, h: int, *, require_both_eyes: bool
) -> GazeFeatures | str:
    if w < 2 or h < 2:
        return "La cámara no entrega fotogramas."
    if face is None:
        return "No se detecta la cara."
    points = face.landmarks
    if len(points) <= 454 or (
        require_both_eyes
        and (
            len(points) < 478 or not all(math.isfinite(p.x) and math.isfinite(p.y) for p in points)
        )
    ):
        return "La detección de la cara es incompleta."
    width = face_width(points) * w / min(w, h)
    if not math.isfinite(width) or width < _SETTINGS.min_face_width:
        return "Acércate un poco a la cámara."

    pose = try_estimate_head_pose(points, w, h)
    if pose is None:
        return "La posición de la cabeza es incierta."
    yaw, pitch, roll = pose
    if max(abs(yaw), abs(pitch), abs(roll)) > _SETTINGS.max_yaw_deg:
        return "Vuelve a orientar la cabeza hacia la pantalla."

    features = extract_features(
        points,
        w,
        h,
        _SETTINGS.blink_ear,
        _SETTINGS.min_face_width,
        _SETTINGS.max_yaw_deg,
        blendshapes=face.blendshapes,
        head_pose=pose,
    )
    if require_both_eyes and not features.both_eyes:
        return "No se distinguen los dos ojos."
    return features


def read_camera(
    index: int, model_path: Path, stop: threading.Event, receive: ObservationReceiver
) -> None:
    """Explicit preview entry point. No recordings, file writes, or desktop events."""
    verify_face_landmarker(model_path)
    estimator = FaceLandmarkEstimator(model_path=model_path)
    camera = Webcam(index, 640, 480, mirror=True)
    try:
        if stop.is_set():
            return
        camera.connect()
        while not stop.is_set():
            started = time.monotonic()
            pixels = camera.read()
            captured_ns = time.monotonic_ns()
            if pixels is None:
                receive(None, 0, 0, captured_ns)
            else:
                h, w = pixels.shape[:2]
                receive(estimator.process(pixels), w, h, captured_ns)
            stop.wait(max(0.0, 1 / 15 - (time.monotonic() - started)))
    finally:
        try:
            camera.disconnect()
        finally:
            estimator.close()
