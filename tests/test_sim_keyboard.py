from fovea.sim_keyboard import EXPIRY_NS, SimKeyboard


def test_group_selection_prediction_and_new_line() -> None:
    keyboard = SimKeyboard()
    assert keyboard.apply("slot1", 0)
    assert keyboard.text == "" and keyboard.slots()[0] == "h"
    assert keyboard.apply("slot0", 0)
    assert keyboard.predictions()[0] == "hola"
    assert keyboard.apply("predict0", 0)
    assert keyboard.apply("enter", 0)
    assert keyboard.apply("slot1", 0)
    assert keyboard.apply("slot0", 0)
    assert keyboard.predictions()[0] == "hola"
    assert keyboard.text == "hola \nh"


def test_modifiers_are_one_shot_and_expire() -> None:
    keyboard = SimKeyboard()
    assert keyboard.apply("shift", 0)
    assert keyboard.apply("slot0", 1)
    assert keyboard.apply("slot0", 2)
    assert keyboard.text == "A" and not keyboard.modifiers
    assert keyboard.apply("ctrl", 3)
    assert keyboard.apply("slot0", 4)
    assert keyboard.apply("slot0", 5)
    assert keyboard.shortcut == "ctrl+a" and keyboard.text == "A"
    assert keyboard.apply("alt", 6)
    assert keyboard.apply("slot0", 7)
    assert keyboard.apply("slot0", 6 + EXPIRY_NS)
    assert not keyboard.modifiers and keyboard.text == "Aa"
