"""Small local keyboard for simulation, never sends system key events."""

from __future__ import annotations

GROUPS = ("abcdefg", "hijklmnñ", "opqrstu", "vwxyz", "áéíóúü¿¡", "01234567", "89.,;:!?")
GROUP_LABELS = ("a-g", "h-ñ", "o-u", "v-z", "á · ¿", "0-7", "8 · ,")
# shortcut: small demonstration dictionary; expand with a local frequency list after user tests.
WORDS = (
    "hola",
    "mundo",
    "quiero",
    "usar",
    "mi",
    "ordenador",
    "con",
    "la",
    "cabeza",
    "gracias",
    "casa",
    "correo",
    "texto",
    "puedo",
    "abrir",
    "ventana",
    "mañana",
    "ayuda",
    "también",
)
MODIFIERS = {"shift", "ctrl", "alt"}
EXPIRY_NS = 10_000_000_000


class SimKeyboard:
    def __init__(self) -> None:
        self.text = ""
        self.group: int | None = None
        self.modifiers: dict[str, int] = {}
        self.shortcut = ""

    def expire(self, now_ns: int) -> None:
        self.modifiers = {key: until for key, until in self.modifiers.items() if now_ns < until}

    def reset_modifiers(self) -> None:
        self.modifiers.clear()

    def _prefix(self) -> str:
        return self.text.split()[-1] if self.text and not self.text[-1].isspace() else ""

    def predictions(self) -> list[str]:
        prefix = self._prefix().lower()
        if not prefix or not prefix.isalpha():
            return []
        return [word for word in WORDS if word.startswith(prefix)][:3]

    def slots(self) -> list[str]:
        return list(GROUPS[self.group]) if self.group is not None else list(GROUP_LABELS)

    def apply(self, command: str, now_ns: int) -> bool:
        self.expire(now_ns)
        if command == "back":
            self.group = None
            return True
        if command in MODIFIERS:
            if command in self.modifiers:
                del self.modifiers[command]
            else:
                self.modifiers[command] = now_ns + EXPIRY_NS
            return True
        if command.startswith("slot") and command[4:].isdigit():
            index = int(command[4:])
            if self.group is None:
                if index >= len(GROUPS):
                    return False
                self.group = index
                return True
            chars = GROUPS[self.group]
            if index >= len(chars):
                return False
            value = chars[index]
            self.group = None
        elif command.startswith("predict") and command[7:].isdigit():
            choices = self.predictions()
            index = int(command[7:])
            if index >= len(choices) or any(k in self.modifiers for k in ("ctrl", "alt")):
                return False
            prefix = self._prefix()
            value = choices[index][len(prefix) :] + " "
        elif command == "space":
            value = " "
        elif command == "enter":
            value = "\n"
        elif command == "backspace":
            self.text = self.text[:-1]
            self.reset_modifiers()
            return True
        else:
            return False
        if "ctrl" in self.modifiers or "alt" in self.modifiers:
            self.shortcut = "+".join(
                [k for k in ("ctrl", "alt", "shift") if k in self.modifiers] + [value]
            )
        else:
            self.text = (self.text + (value.upper() if "shift" in self.modifiers else value))[-500:]
        self.reset_modifiers()
        return True
