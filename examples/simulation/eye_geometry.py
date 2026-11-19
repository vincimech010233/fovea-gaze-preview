"""Iris coordinates in local eye-corner axes."""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Protocol

from fovea.webcam.features import (
    LEFT_INNER,
    LEFT_IRIS_RING,
    LEFT_OUTER,
    RIGHT_INNER,
    RIGHT_IRIS_RING,
    RIGHT_OUTER,
)

type IrisPosition = tuple[float, float]


class Landmark(Protocol):
    @property
    def x(self) -> float: ...

    @property
    def y(self) -> float: ...


def eye_geometry(
    points: Sequence[Landmark], w: float, h: float
) -> tuple[float, float, float, float] | None:
    """Return (lx, ly, rx, ry), with x increasing right and y increasing down."""
    left, right = iris_positions(points, w, h)
    if left is None or right is None:
        return None
    return (*left, *right)


def iris_positions(
    points: Sequence[Landmark], w: float, h: float
) -> tuple[IrisPosition | None, IrisPosition | None]:
    """Return left and right iris projections, with unavailable eyes set to None."""
    if not (math.isfinite(w) and math.isfinite(h) and w >= 2 and h >= 2):
        return None, None
    return (
        _eye_axes(points, w, h, LEFT_OUTER, LEFT_INNER, LEFT_IRIS_RING),
        _eye_axes(points, w, h, RIGHT_OUTER, RIGHT_INNER, RIGHT_IRIS_RING),
    )


def _eye_axes(
    points: Sequence[Landmark],
    w: float,
    h: float,
    outer: int,
    inner: int,
    iris: tuple[int, ...],
) -> IrisPosition | None:
    if len(points) <= max(outer, inner, *iris):
        return None
    start, end = points[outer], points[inner]
    if start.x > end.x:
        start, end = end, start
    ux = (end.x - start.x) * w
    uy = (end.y - start.y) * h
    width_squared = ux * ux + uy * uy
    min_width = w * 0.008
    if not math.isfinite(width_squared) or width_squared < min_width * min_width:
        return None

    dx = (sum(points[index].x for index in iris) / len(iris) - start.x) * w
    dy = (sum(points[index].y for index in iris) / len(iris) - start.y) * h
    x = (dx * ux + dy * uy) / width_squared
    y = (dy * ux - dx * uy) / width_squared
    if not (-0.35 <= x <= 1.35 and -0.6 <= y <= 0.6):
        return None
    return x, y
