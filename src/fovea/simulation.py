"""Local-only gaze simulation; no webcam or desktop input adapters."""

from __future__ import annotations

import math

from fovea.events import (
    Dwell,
    DwellProgress,
    GazePoint,
    TargetEnter,
    TargetLeave,
    TrackingState,
    TrackingStatus,
)
from fovea.sim_keyboard import SimKeyboard
from fovea.synthetic_control import SyntheticControl
from fovea.util import clamp01
from fovea.webcam.targeting import TargetRect, TargetTracker

TARGETS = (
    "resume",
    "pause",
    "cancel",
    "confirm",
    "document",
    "drag:start",
    "drag:drop",
    "scroll:up",
    "scroll:down",
    "scroll:left",
    "scroll:right",
    "key:ñ",
    "key:a",
    "key:Enter",
    "key:Backspace",
    *tuple(f"kbd:slot{i}" for i in range(8)),
    *tuple(f"kbd:predict{i}" for i in range(3)),
    "kbd:back",
    "kbd:shift",
    "kbd:ctrl",
    "kbd:alt",
    "kbd:space",
    "kbd:backspace",
    "kbd:enter",
    "double_click",
    "recalibrate",
    "aim_test",
)
RECTS = tuple(
    TargetRect(name, (i % 6) / 6, (i // 6) / 6, 0.16, 0.16) for i, name in enumerate(TARGETS)
)
CENTERS = {target.id: target.center for target in RECTS}
CENTERS[""] = (0.999, 0.999)  # Empty space within the simulated board.
FRAME_TIMEOUT_NS = 250_000_000
BLINK_MIN_NS = 650_000_000
BLINK_MAX_NS = 1_500_000_000
HEAD_SPAN_DEG = 40.0


class Simulation:
    """Target dwell and recovery logic, with a virtual-only workspace."""

    def __init__(self) -> None:
        self.control = SyntheticControl()
        self.gaze_mode = False
        self.tracker = self._new_tracker()
        self.last_frame_ns: int | None = None
        self.target: str | None = None
        self.progress = 0.0
        self.message = "En pausa. Mantén el puntero sobre Reanudar."
        self.text = ""
        self.scroll_x = 0
        self.scroll_y = 0
        self.clicks = 0
        self.history: list[str] = []
        self.keyboard = SimKeyboard()
        self.head_mode = False
        self.gaze_request: str | None = None
        self.neutral = (0.0, 0.0)
        self.calibrating = False
        self._recalibrate_latched = False
        self._neutral_start: tuple[float, float, int] | None = None
        self.pointer: tuple[float, float] | None = None
        self.confidence = 0.0
        self.eyes_closed = False
        self._seen_open = False
        self._blink: tuple[str, int] | None = None
        self._last_closed_ns: int | None = None

    def _new_tracker(self) -> TargetTracker:
        return TargetTracker(1000, 0.018 if self.gaze_mode else 0, 0, 0, RECTS)

    def _drain(self) -> None:
        for action in self.control.actions:
            kind = action[0]
            if kind == "move":
                continue
            if kind == "keyboard":
                key = action[1]
                assert isinstance(key, str)
                self.keyboard.text = self.text
                accepted = self.keyboard.apply(key, self.last_frame_ns or 0)
                self.text = self.keyboard.text
                label = "Teclado simulado: " + (key if accepted else "opción no disponible")
            elif kind == "key":
                key = action[1]
                assert isinstance(key, str)
                if key == "Backspace":
                    self.text = self.text[:-1]
                elif key == "Enter":
                    self.text = (self.text + "\n")[-120:]
                else:
                    self.text = (self.text + key)[-120:]
                label = "Tecla simulada: " + key
            elif kind == "scroll":
                assert len(action) == 3
                self.scroll_x += int(action[1])
                self.scroll_y += int(action[2])
                label = f"Scroll simulado: {int(action[1])}, {int(action[2])}"
            elif kind in ("left_click", "double_click"):
                self.clicks += 2 if kind == "double_click" else 1
                label = "Doble clic simulado" if kind == "double_click" else "Clic simulado"
            else:
                label = "Arrastre simulado: " + (
                    "iniciado" if kind == "button_down" else "liberado"
                )
            self.history = [*self.history, label][-5:]
        self.control.actions.clear()
        self.keyboard.text = self.text

    def lose_signal(self) -> None:
        self.control.pause()
        self.keyboard.reset_modifiers()
        self.tracker = self._new_tracker()
        self.last_frame_ns = None
        self.pointer = None
        self.confidence = 0.0
        self._seen_open = False
        self.eyes_closed = False
        self._blink = None
        self._last_closed_ns = None
        self._neutral_start = None
        self.calibrating = False
        self.target = None
        self.progress = 0.0
        self.message = "Sin señal simulada. Vuelve y mantén el puntero sobre Reanudar."
        self._drain()

    def idle(self, now_ns: int) -> None:
        self.keyboard.expire(now_ns)
        if (
            self.last_frame_ns is not None
            and not 0 <= now_ns - self.last_frame_ns <= FRAME_TIMEOUT_NS
        ):
            self.lose_signal()
        was_armed = self.control.armed
        self.control.tick(now_ns)
        if was_armed and not self.control.armed:
            self.keyboard.reset_modifiers()
            self._blink = None
            self.tracker = self._new_tracker()
            self.progress = 0.0
            self.message = "En pausa por seguridad. Mantén el puntero sobre Reanudar."
        self._drain()

    def step(self, target: str | None, now_ns: int) -> dict[str, object]:
        if target is None:
            self.lose_signal()
            return self.state()
        if target not in CENTERS:
            self.lose_signal()
            raise ValueError("Objetivo simulado desconocido")
        if self.head_mode or self.gaze_mode:
            self.head_mode = self.gaze_mode = False
            self.lose_signal()
        self.idle(now_ns)
        self._frame(*CENTERS[target], 1.0, now_ns)
        return self.state()

    def pose(
        self, yaw: float, pitch: float, confidence: float, closed: bool, now_ns: int
    ) -> dict[str, object]:
        """Use supplied angles and eyelid state; no sensor access."""
        if not self.head_mode:
            self.head_mode = True
            self.gaze_mode = False
            self.lose_signal()
        self.idle(now_ns)
        if not (0.8 <= confidence <= 1.0 and all(math.isfinite(v) for v in (yaw, pitch))):
            self.lose_signal()
            self.message = "Señal insuficiente. En pausa."
            return self.state()
        if self.calibrating:
            self._calibrate(yaw, pitch, confidence, closed, now_ns)
            return self.state()
        self._frame(*self._pose_point(yaw, pitch), confidence, now_ns, closed)
        return self.state()

    def gaze(self, x: float, y: float, now_ns: int) -> None:
        """Feed a reliable, calibrated gaze sample into the virtual panel."""
        if not all(math.isfinite(v) and 0 <= v <= 1 for v in (x, y)):
            self.lose_signal()
            return
        if not self.gaze_mode:
            self.head_mode = False
            self.gaze_mode = True
            self.lose_signal()
        self.idle(now_ns)
        if self.last_frame_ns is None:
            self.message = "En pausa. Mira Reanudar hasta llenar la barra."
        self._frame(x, y, 1.0, now_ns)

    def suspend_gaze(self, now_ns: int) -> None:
        """Wait for a new fixation without extending the last trusted sample."""
        self.idle(now_ns)
        self.control.cancel_candidate()
        self.tracker = self._new_tracker()
        self.target = None
        self.progress = 0.0

    def _pose_point(self, yaw: float, pitch: float) -> tuple[float, float]:
        return (
            clamp01(0.5 + (yaw - self.neutral[0]) / HEAD_SPAN_DEG),
            clamp01(0.5 + (pitch - self.neutral[1]) / HEAD_SPAN_DEG),
        )

    def hold_eye_transition(self, yaw: float, pitch: float, now_ns: int) -> None:
        """Freeze dwell while the eyelids disagree. Keep enforcing signal expiry."""
        self.idle(now_ns)
        self._neutral_start = None
        if self.calibrating:
            self.progress = 0.0
        match = self.tracker.freeze(now_ns)
        if match is not None and not any(
            rect.id == match.target_id and rect.contains(*self._pose_point(yaw, pitch))
            for rect in RECTS
        ):
            self.lose_signal()

    def begin_calibration(self) -> None:
        self.lose_signal()
        self.head_mode = True
        self.calibrating = True
        self.message = "Centra la cabeza y mantenla quieta un segundo."

    def _calibrate(
        self, yaw: float, pitch: float, confidence: float, closed: bool, now_ns: int
    ) -> None:
        self.last_frame_ns = now_ns
        self.confidence = confidence
        start = self._neutral_start
        if closed:
            self._neutral_start = None
            self.progress = 0.0
        elif start is None or math.hypot(yaw - start[0], pitch - start[1]) > 1.0:
            self._neutral_start = (yaw, pitch, now_ns)
            self.progress = 0.0
        else:
            self.progress = (now_ns - start[2]) / 1_000_000_000
            if self.progress >= 1.0:
                self.lose_signal()
                self.last_frame_ns = now_ns
                self.confidence = confidence
                self.pointer = (0.5, 0.5)
                self.neutral = (yaw, pitch)
                self.message = "Centro calibrado. Elige Reanudar y confirma con el gesto."

    def _frame(
        self, x: float, y: float, confidence: float, now_ns: int, closed: bool = False
    ) -> None:
        self.last_frame_ns = now_ns
        self.pointer = (x, y)
        self.confidence = confidence
        update = self.tracker.update(x, y, confidence, now_ns)
        target = update.match.target_id if update.match else None
        previous_target = self.target
        if previous_target != target:
            self.progress = 0.0
            if self.head_mode:
                self.control.cancel_candidate()
        self.target = target
        if target != "recalibrate":
            self._recalibrate_latched = False
        self.control.consume(TrackingState(TrackingStatus.ACTIVE, confidence, now_ns), now_ns)
        self.control.consume(GazePoint(x, y, confidence, now_ns, target_id=target), now_ns)
        if not closed and not self.eyes_closed:
            for event in update.events:
                self._target_event(event, now_ns)
        if self.head_mode:
            self._confirm_blink(closed, previous_target, now_ns)
        self._drain()

    def _target_event(
        self, event: Dwell | DwellProgress | TargetLeave | TargetEnter, now_ns: int
    ) -> None:
        if isinstance(event, DwellProgress):
            self.progress = event.progress
        elif isinstance(event, Dwell) and event.id == "resume":
            if not self.head_mode:
                self._resume()
        elif isinstance(event, Dwell) and event.id == "pause":
            self.control.pause()
            self.keyboard.reset_modifiers()
            self.message = "En pausa. Elige Reanudar para continuar."
        elif isinstance(event, Dwell) and event.id == "recalibrate":
            if self._recalibrate_latched:
                return
            self._recalibrate_latched = True
            if self.gaze_mode:
                self.lose_signal()
                self.gaze_request = "recalibrate"
            elif self.head_mode:
                self.begin_calibration()
            else:
                self.lose_signal()
                self.message = "Simulación en pausa. Activa el modo Cabeza para calibrar."
        elif isinstance(event, Dwell) and event.id == "aim_test":
            if self.gaze_mode:
                self.lose_signal()
                self.gaze_request = "aim_test"
        elif isinstance(event, (Dwell, TargetLeave)):
            if self.head_mode and event.id == "confirm":
                return
            self.control.consume(event, now_ns)

    def _resume(self) -> None:
        self.control.resume()
        self.message = (
            "Activo. Elige una acción y confirma con un parpadeo largo."
            if self.head_mode
            else "Activo. Elige una acción y después Confirma."
        )

    def _confirm_blink(self, closed: bool, previous_target: str | None, now_ns: int) -> None:
        target = self.target
        candidate = self.control.candidate
        ready = target is not None and (
            (target == "resume" and not self.control.armed and self.progress >= 1.0)
            or (self.control.armed and candidate is not None and candidate[0] == target)
        )
        if closed and not self.eyes_closed and self._seen_open and ready and target is not None:
            self._blink = (target, now_ns)
        if not ready or target != previous_target:
            self._blink = None
        blink = self._blink
        if closed:
            self._last_closed_ns = now_ns
        else:
            confirmed = (
                blink is not None
                and self._last_closed_ns is not None
                and self._last_closed_ns - blink[1] >= BLINK_MIN_NS
                and now_ns - blink[1] <= BLINK_MAX_NS
            )
            if blink is not None and confirmed:
                if blink[0] == "resume":
                    self._resume()
                else:
                    self.control.confirm_gesture(now_ns)
            if self.eyes_closed and (blink is None or confirmed):
                self.tracker = self._new_tracker()
                self.progress = 0.0
            self._seen_open = True
            self._blink = None
        self.eyes_closed = closed

    def state(self) -> dict[str, object]:
        return {
            "armed": self.control.armed,
            "pointer": self.pointer,
            "confidence": self.confidence,
            "head_mode": self.head_mode,
            "calibrating": self.calibrating,
            "eyes_closed": self.eyes_closed,
            "dragging": self.control.dragging,
            "candidate": self.control.candidate[0] if self.control.candidate else None,
            "target": self.target,
            "progress": self.progress,
            "message": self.message,
            "text": self.text,
            "scroll_x": self.scroll_x,
            "scroll_y": self.scroll_y,
            "clicks": self.clicks,
            "history": self.history,
            "keyboard_slots": self.keyboard.slots(),
            "keyboard_predictions": self.keyboard.predictions(),
            "keyboard_modifiers": list(self.keyboard.modifiers),
            "keyboard_shortcut": self.keyboard.shortcut,
        }
