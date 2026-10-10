import pytest

from herdr_agent_rename.validation import (
    EMPTY_NAME_MESSAGE,
    INVALID_NAME_MESSAGE,
    sanitize_message,
    validate_name,
)


@pytest.mark.parametrize("name", ["a", "reviewer", "backend-api", "a_01", "a" * 32])
def test_exact_ascii_names_are_valid(name):
    assert validate_name(name) is None


@pytest.mark.parametrize(
    "name",
    [
        "A", "1a", "_a", "-a", "a" * 33, " a", "a ", "a\n", "a\r",
        "a\nb", "a\t", "a\x00", "á", "а", "a🙂", "a.b", "$(id)", None, 1,
    ],
)
def test_invalid_names_are_rejected_without_transforming_them(name):
    assert validate_name(name) == INVALID_NAME_MESSAGE


def test_empty_name_has_specific_error():
    assert validate_name("") == EMPTY_NAME_MESSAGE


def test_server_diagnostics_cannot_emit_terminal_controls():
    message = sanitize_message("Server\x1b[31m error\nwith\x00 details")
    assert "Server" in message and "details" in message
    assert all(character.isprintable() for character in message)
