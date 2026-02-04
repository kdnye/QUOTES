from __future__ import annotations

from pathlib import Path


def test_register_template_uses_neutral_branding() -> None:
    """Ensure the register template uses text-only neutral branding.

    Args:
        None.

    Returns:
        None.

    External Dependencies:
        * Reads ``templates/register.html`` via :meth:`pathlib.Path.read_text`.
    """

    template_path = Path(__file__).resolve().parents[1] / "templates" / "register.html"
    template_contents = template_path.read_text(encoding="utf-8")

    assert "FSI" not in template_contents
    assert "Quote Tool" in template_contents
