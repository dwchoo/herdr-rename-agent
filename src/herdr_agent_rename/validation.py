"""Name validation without normalization or lossy input transformations."""

import re


EMPTY_NAME_MESSAGE = "Enter an agent name."
INVALID_NAME_MESSAGE = (
    "Use 1–32 characters: a lowercase letter first, then lowercase letters, "
    "digits, underscores, or hyphens."
)
NAME_PATTERN = re.compile(r"[a-z][a-z0-9_-]{0,31}", re.ASCII)


def validate_name(name: object) -> str | None:
    """Return an English error, or ``None`` for an exact valid ASCII name.

    ``fullmatch`` deliberately rejects trailing newlines (unlike ``$``). In
    particular, pasted whitespace is preserved for the user to correct.
    """
    if name == "":
        return EMPTY_NAME_MESSAGE
    if not isinstance(name, str) or NAME_PATTERN.fullmatch(name) is None:
        return INVALID_NAME_MESSAGE
    return None


def sanitize_message(message: object) -> str:
    """Keep useful diagnostics without terminal controls or multiline escapes."""
    return " ".join(
        "".join(char if char.isprintable() else " " for char in str(message)).split()
    )
