"""Unit tests for service-layer quote creation helpers."""

from types import SimpleNamespace

from app.services import quote as quote_service


class _FakeSession:
    """Minimal context-manager session used to capture persisted records."""

    def __init__(self):
        self.saved = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def add(self, obj):
        self.saved.append(obj)

    def commit(self):
        return None

    def refresh(self, obj):
        return None


class _FakeQuote(SimpleNamespace):
    """Simple mutable object mirroring SQLAlchemy model constructor behavior."""



def test_create_quote_persists_computed_dim_weight(monkeypatch) -> None:
    """Computed dimensional weight should be centralized and persisted consistently.

    Inputs:
        length/width/height and pieces define dimensional weight when explicit
        ``dim_weight`` is not provided.

    Outputs:
        ``Quote.dim_weight`` matches the computed dimensional weight value used
        for billable-weight selection and threshold checks.

    External dependencies:
        Stubs ``app.services.quote.calculate_hotshot_quote``,
        ``app.services.quote.Session``, and ``app.services.quote.Quote``.
    """

    fake_db = _FakeSession()
    monkeypatch.setattr(quote_service, "Session", lambda: fake_db)
    monkeypatch.setattr(quote_service, "Quote", _FakeQuote)
    monkeypatch.setattr(
        quote_service,
        "calculate_hotshot_quote",
        lambda *_args, **_kwargs: {"quote_total": 123.0, "zone": "A", "miles": 10},
    )

    quote, _metadata = quote_service.create_quote(
        user_id=1,
        user_email="user@example.com",
        quote_type="Hotshot",
        origin="30301",
        destination="60601",
        weight=10.0,
        length=20.0,
        width=20.0,
        height=20.0,
        pieces=2,
        accessorial_total=0.0,
        accessorials=[],
    )

    expected_dim_weight = (20.0 * 20.0 * 20.0 / 166) * 2

    assert quote.dim_weight == expected_dim_weight
    assert quote.weight == expected_dim_weight
    assert quote.weight_method == "Dimensional"
