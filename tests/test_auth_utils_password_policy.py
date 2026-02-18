"""Unit tests for password validation policy."""

from app.services.auth_utils import is_valid_password


def test_is_valid_password_accepts_twelve_char_complex_password() -> None:
    """Complex passwords with 12 characters should pass validation."""

    assert is_valid_password("Aa1!bcdefghi")


def test_is_valid_password_rejects_eleven_char_complex_password() -> None:
    """Complex passwords shorter than 12 characters should be rejected."""

    assert not is_valid_password("Aa1!bcdefgh")


def test_is_valid_password_accepts_twenty_eight_char_passphrase() -> None:
    """Long passphrases should pass even without mixed character classes."""

    assert is_valid_password("a" * 28)


def test_is_valid_password_rejects_short_plain_passphrase() -> None:
    """Plain passphrases shorter than 28 characters should fail."""

    assert not is_valid_password("a" * 27)
