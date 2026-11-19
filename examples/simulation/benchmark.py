"""Repeatable head/gesture benchmark with an injected clock and no peripherals."""

from __future__ import annotations

import argparse
import json
import random

from fovea.sim_keyboard import GROUPS
from fovea.simulation import CENTERS, HEAD_SPAN_DEG, Simulation

PHRASE = "hola mundo quiero usar mi ordenador con la cabeza gracias"
FRAME_NS = 100_000_000


class Driver:
    def __init__(self, seed: int = 0) -> None:
        self.sim = Simulation()
        self.random = random.Random(seed)
        self.now_ns = 0
        self.steps = 0

    def frame(
        self, target: str, *, closed: bool = False, confidence: float = 1.0, noise: float = 0.0
    ) -> None:
        x, y = CENTERS[target]
        yaw = self.sim.neutral[0] + (x - 0.5) * HEAD_SPAN_DEG
        pitch = self.sim.neutral[1] + (y - 0.5) * HEAD_SPAN_DEG
        self.sim.pose(
            yaw + self.random.uniform(-noise, noise),
            pitch + self.random.uniform(-noise, noise),
            confidence,
            closed,
            self.now_ns,
        )
        self.now_ns += FRAME_NS

    def aim(self, target: str, frames: int = 11) -> None:
        for _ in range(frames):
            self.frame(target, noise=0.8)

    def blink(self, target: str, frames: int = 8) -> None:
        for _ in range(frames):
            self.frame(target, closed=True, noise=0.8)
        self.frame(target, noise=0.8)

    def choose(self, target: str) -> None:
        self.aim(target)
        self.blink(target)
        self.steps += 2


def type_phrase(prediction: bool) -> dict[str, object]:
    driver = Driver()
    driver.choose("resume")
    driver.steps = 0
    start = driver.now_ns
    for word in PHRASE.split():
        for index, char in enumerate(word):
            group = next(i for i, chars in enumerate(GROUPS) if char in chars)
            driver.choose(f"kbd:slot{group}")
            driver.choose(f"kbd:slot{GROUPS[group].index(char)}")
            choices = driver.sim.keyboard.predictions()
            if prediction and index == 1 and word in choices:
                driver.choose(f"kbd:predict{choices.index(word)}")
                break
        else:
            driver.choose("kbd:space")
    assert driver.sim.text == PHRASE + " ", driver.sim.text
    return {
        "dwell_or_confirm_steps": driver.steps,
        "simulated_seconds": (driver.now_ns - start) / 1e9,
        "words": len(PHRASE.split()),
    }


def false_activations(trials_per_case: int) -> dict[str, object]:
    cases = ("hover", "short", "overlong", "bad_signal", "dropout", "drift")
    counts = dict.fromkeys(cases, 0)
    for case in cases:
        for seed in range(trials_per_case):
            driver = Driver(seed)
            driver.choose("resume")
            driver.aim("document")
            assert driver.sim.control.armed and driver.sim.control.candidate
            if case == "hover":
                driver.aim("document", 30)
            elif case == "short":
                driver.blink("document", driver.random.randint(1, 4))
            elif case == "overlong":
                driver.blink("document", driver.random.randint(16, 20))
            else:
                for _ in range(4):
                    driver.frame("document", closed=True)
                if case == "bad_signal":
                    driver.frame("document", closed=True, confidence=0.6)
                elif case == "dropout":
                    driver.now_ns += 300_000_000
                elif case == "drift":
                    driver.frame("drag:start", closed=True)
                driver.blink("document", 5)
            counts[case] += bool(driver.sim.clicks or driver.sim.control.dragging)
    return {
        "false": sum(counts.values()),
        "trials": len(cases) * trials_per_case,
        "false_by_case": counts,
    }


def main() -> None:
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument("--trials-per-case", type=int, default=100)
    trials = args.parse_args().trials_per_case
    if trials <= 0:
        args.error("--trials-per-case must be positive")
    print(
        json.dumps(
            {
                "input": PHRASE,
                "without_prediction": type_phrase(False),
                "with_prediction": type_phrase(True),
                "false_activations": false_activations(trials),
            },
            indent=2,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
