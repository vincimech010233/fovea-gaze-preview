"""Use only generated landmarks to exercise preview eye geometry."""

import math
from dataclasses import replace

import pytest

from examples.simulation.eye_geometry import eye_geometry, iris_positions
from fovea.webcam.features import (
    LEFT_IRIS_RING,
    LEFT_OUTER,
    LEFT_UPPER,
    RIGHT_IRIS_RING,
    RIGHT_LOWER,
)
from tests.synth import synthetic_landmarks

_WIDTH, _HEIGHT = 640, 480


def test_geometry_compensates_image_rotation_translation_and_scale() -> None:
    points = synthetic_landmarks(gaze_dx=0.17, gaze_dy=-0.22)
    reference = eye_geometry(points, _WIDTH, _HEIGHT)
    assert reference == pytest.approx((0.67, -0.132, 0.67, -0.132))

    angle = math.radians(13)
    sine, cosine = math.sin(angle), math.cos(angle)

    def transform(x: float, y: float) -> tuple[float, float]:
        px, py = (x - 0.5) * _WIDTH, (y - 0.5) * _HEIGHT
        return (
            0.54 + 1.15 * (px * cosine - py * sine) / _WIDTH,
            0.47 + 1.15 * (px * sine + py * cosine) / _HEIGHT,
        )

    moved = [
        replace(point, x=x, y=y) for point in points for x, y in (transform(point.x, point.y),)
    ]
    assert eye_geometry(moved, _WIDTH, _HEIGHT) == pytest.approx(reference)


def test_geometry_uses_iris_motion_without_following_lid_height() -> None:
    points = synthetic_landmarks(gaze_dy=0.25)
    reference = eye_geometry(points, _WIDTH, _HEIGHT)
    assert reference == pytest.approx((0.5, 0.15, 0.5, 0.15))
    for index in (*LEFT_UPPER, *RIGHT_LOWER):
        points[index] = replace(points[index], y=points[index].y + 0.018)
    assert eye_geometry(points, _WIDTH, _HEIGHT) == reference
    moved = synthetic_landmarks(gaze_dx=-0.18, gaze_dy=-0.25)
    assert eye_geometry(moved, _WIDTH, _HEIGHT) == pytest.approx((0.32, -0.15, 0.32, -0.15))


def test_geometry_rejects_missing_or_corrupt_observations() -> None:
    points = synthetic_landmarks()
    assert eye_geometry(points[:-1], _WIDTH, _HEIGHT) is None
    assert eye_geometry(points, 0, _HEIGHT) is None
    assert eye_geometry(points, _WIDTH, float("nan")) is None
    points[LEFT_IRIS_RING[0]] = replace(points[LEFT_IRIS_RING[0]], y=float("nan"))
    assert eye_geometry(points, _WIDTH, _HEIGHT) is None
    points = synthetic_landmarks()
    for index in LEFT_IRIS_RING:
        points[index] = replace(points[index], x=points[LEFT_OUTER].x + 0.7)
    assert eye_geometry(points, _WIDTH, _HEIGHT) is None
    left, right = iris_positions(points, _WIDTH, _HEIGHT)
    assert left is None
    assert right == pytest.approx((0.5, 0.0))


@pytest.mark.parametrize(
    ("missing_eye", "index"),
    [(0, LEFT_IRIS_RING[0]), (1, RIGHT_IRIS_RING[0])],
)
def test_iris_positions_preserve_the_valid_eye(missing_eye: int, index: int) -> None:
    points = synthetic_landmarks(gaze_dx=0.14, gaze_dy=-0.20)
    points[index] = replace(points[index], y=float("nan"))
    readings = iris_positions(points, _WIDTH, _HEIGHT)
    assert readings[missing_eye] is None
    assert readings[1 - missing_eye] == pytest.approx((0.64, -0.12))
    assert eye_geometry(points, _WIDTH, _HEIGHT) is None


def test_iris_positions_keep_a_complete_eye_when_landmarks_are_truncated() -> None:
    points = synthetic_landmarks(gaze_dx=-0.11)
    left, right = iris_positions(points[: LEFT_IRIS_RING[0]], _WIDTH, _HEIGHT)
    assert left is None
    assert right == pytest.approx((0.39, 0.0))
    assert iris_positions(points, float("inf"), _HEIGHT) == (None, None)
