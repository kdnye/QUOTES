"""Import Hotshot-related rate tables from CSV files."""

from __future__ import annotations

import sys
from pathlib import Path

# Allow running this script directly by ensuring project root is on ``sys.path``
sys.path.append(str(Path(__file__).resolve().parents[1]))

from argparse import ArgumentParser
from typing import Any, List, Optional, Tuple

import numpy as np

import pandas as pd
from sqlalchemy.orm import Session as SASession

from app.database import Accessorial, BeyondRate, HotshotRate, Session
from app.services.rate_sets import DEFAULT_RATE_SET, normalize_rate_set


def load_hotshot_rates(df: pd.DataFrame) -> List[HotshotRate]:
    """Normalize and convert a hotshot rates DataFrame to model objects.

    Column names are stripped of whitespace and mapped via a rename table. The
    resulting DataFrame is validated for required columns and ``NaN`` values are
    converted to ``None``. Rows lacking a ``miles`` value are ignored to prevent
    malformed :class:`HotshotRate` objects.

    Args:
        df: Raw DataFrame parsed from ``Hotshot_Rates.csv``.

    Raises:
        ValueError: If any required column is missing after normalization.
    """

    rename_map = {
        "Miles": "miles",
        "ZONE": "zone",
        "PER LB": "per_lb",
        "PER MILE": "per_mile",
        "MIN": "min_charge",
        "Weight Break": "weight_break",
        "Fuel": "fuel_pct",
        "Rate Set": "rate_set",
    }
    df.columns = df.columns.str.strip()
    normalized = df.rename(columns=rename_map)
    required = {"miles", "zone", "per_lb", "min_charge", "fuel_pct"}
    missing = required.difference(normalized.columns)
    if missing:
        raise ValueError(f"Missing required columns: {', '.join(sorted(missing))}")
    normalized = normalized.replace({np.nan: None, pd.NA: None})
    normalized = normalized[normalized["miles"].notna()]
    normalized["rate_set"] = normalized.get("rate_set", DEFAULT_RATE_SET)
    normalized["rate_set"] = normalized["rate_set"].apply(normalize_rate_set)
    return [HotshotRate(**row) for row in normalized.to_dict(orient="records")]


def _parse_currency(value: Any) -> Optional[float]:
    """Convert currency-formatted strings to floats.

    Args:
        value: The raw value which may include ``$`` or comma separators.

    Returns:
        The numeric value as ``float`` or ``None`` if the input is missing or
        cannot be parsed.
    """

    if pd.isna(value):
        return None
    if isinstance(value, str):
        cleaned = value.replace("$", "").replace(",", "").strip()
        if not cleaned:
            return None
        try:
            return float(cleaned)
        except ValueError:
            return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def load_beyond_rates(df: pd.DataFrame) -> List[BeyondRate]:
    """Normalize and convert a beyond rates DataFrame to model objects.

    ``NaN`` or ``pd.NA`` values are converted to ``None``. Currency strings in
    rate columns (e.g., ``"$1,234.56"``) are parsed to floats. Rows without a
    destination ``zone`` are skipped to avoid integrity errors when inserting
    into the database. See :class:`app.models.BeyondRate` for model details.

    Args:
        df: Raw DataFrame parsed from the "Beyond Price" spreadsheet.

    Returns:
        A list of :class:`app.models.BeyondRate` instances ready for bulk
        insertion.
    """

    rename_map = {
        "ZONE": "zone",
        "RATE": "rate",
        "UP TO MILES": "up_to_miles",
        "RATE SET": "rate_set",
    }
    df.columns = df.columns.str.strip().str.upper()
    normalized = df.rename(columns=rename_map)
    normalized = normalized.replace({np.nan: None, pd.NA: None})
    normalized["rate"] = normalized["rate"].apply(_parse_currency)
    normalized["up_to_miles"] = normalized["up_to_miles"].apply(_parse_currency)
    normalized = normalized[normalized["zone"].notna()]
    normalized["rate_set"] = normalized.get("rate_set", DEFAULT_RATE_SET)
    normalized["rate_set"] = normalized["rate_set"].apply(normalize_rate_set)
    return [BeyondRate(**row) for row in normalized.to_dict(orient="records")]


def load_accessorials(df: pd.DataFrame) -> List[Accessorial]:
    """Normalize and convert an accessorials DataFrame to model objects.

    The source CSV may list accessorial names either as column headers with a
    single row of values (row oriented) or in the first column with values in
    subsequent columns (column oriented). When the first column header is
    ``"accessorial"`` or ``"name"``, the DataFrame is transposed so that the
    existing row-oriented parsing logic can be reused. Currency-formatted
    amounts such as ``"$95.00"`` are parsed using :func:`_parse_currency`.
    Rows lacking a name or amount are ignored to avoid inserting invalid
    database entries.

    Args:
        df: Raw DataFrame parsed from ``accessorial_cost.csv``.

    Returns:
        A list of :class:`db.Accessorial` instances.
    """

    df = df.rename(columns=str.strip)
    if len(df.columns) > 0 and df.columns[0].lower() in {"accessorial", "name"}:
        name_col = df.columns[0]
        amount_col = df.columns[1] if len(df.columns) > 1 else None
        df = df.rename(columns={name_col: "name"})
        if amount_col is not None:
            df = df.rename(columns={amount_col: "amount"})
        else:
            df = df.set_index("name").T
            df = df.rename_axis(None, axis=1)
            df = pd.DataFrame(
                [{"name": name, "amount": value} for name, value in df.iloc[0].items()]
            )
    else:
        df = pd.DataFrame(
            [{"name": name, "amount": value} for name, value in df.iloc[0].items()]
        )

    df = df.dropna(subset=["name", "amount"])
    df["name"] = df["name"].astype(str).str.strip()
    df = df[df["name"] != ""]

    records: List[Accessorial] = []
    for _, row in df.iterrows():
        value = row["amount"]
        name = row["name"]
        if isinstance(value, str) and "multiply total by" in value:
            try:
                multiplier = float(value.split("by")[1].strip())
                amount = multiplier - 1.0
                records.append(
                    Accessorial(name=name, amount=amount, is_percentage=True)
                )
            except ValueError:
                continue
        else:
            amount = _parse_currency(value)
            if amount is None:
                continue
            records.append(Accessorial(name=name, amount=amount, is_percentage=False))
    return records


def import_csvs(directory: Path, session: Optional[SASession] = None) -> None:
    """Load rate data from CSV files and bulk insert into the database.

    The function looks for ``Hotshot_Rates.csv``, ``beyond_price.csv``, and
    ``accessorial_cost.csv`` within ``directory`` and loads each file if
    present. When ``session`` is ``None``, a temporary :class:`Session` is used
    so the function can operate both inside and outside a Flask application
    context.

    Args:
        directory: Folder containing the CSV files.
        session: Optional SQLAlchemy session for database operations.
    """

    close_session = False
    if session is None:
        session = Session()
        close_session = True
    try:
        hotshot_file = directory / "Hotshot_Rates.csv"
        if hotshot_file.exists():
            hotshot_df = pd.read_csv(hotshot_file)
            session.bulk_save_objects(load_hotshot_rates(hotshot_df))

        beyond_file = directory / "beyond_price.csv"
        if beyond_file.exists():
            beyond_df = pd.read_csv(beyond_file)
            session.bulk_save_objects(load_beyond_rates(beyond_df))

        access_file = directory / "accessorial_cost.csv"
        if access_file.exists():
            access_df = pd.read_csv(access_file)
            session.bulk_save_objects(load_accessorials(access_df))

        session.commit()
    finally:
        if close_session:
            Session.remove()


def _round(value: Optional[float]) -> Optional[float]:
    """Round numeric values for comparison while preserving ``None``."""

    return round(value, 4) if value is not None else None


def verify_csvs(directory: Path, session: Optional[SASession] = None) -> bool:
    """Compare CSV rate tables against database rows.

    Each CSV file in ``directory`` is loaded using the same normalization logic
    as :func:`import_csvs` and compared against the existing database rows.

    Args:
        directory: Folder containing the CSV files.
        session: Optional SQLAlchemy session for database operations.

    Returns:
        ``True`` if all rate tables match their CSV sources, ``False`` otherwise.
    """

    close_session = False
    if session is None:
        session = Session()
        close_session = True
    try:
        all_ok = True

        hotshot_file = directory / "Hotshot_Rates.csv"
        if hotshot_file.exists():
            expected = load_hotshot_rates(pd.read_csv(hotshot_file))

            def hotshot_key(rate: HotshotRate) -> Tuple:
                return (
                    rate.rate_set,
                    rate.miles,
                    rate.zone,
                    _round(rate.per_lb),
                    _round(rate.per_mile),
                    _round(rate.min_charge),
                    _round(rate.weight_break),
                    _round(rate.fuel_pct),
                )

            db_rows = session.query(HotshotRate).all()
            if {hotshot_key(r) for r in expected} != {hotshot_key(r) for r in db_rows}:
                print("❌ Hotshot Rates mismatch.")
                all_ok = False

        beyond_file = directory / "beyond_price.csv"
        if beyond_file.exists():
            expected = load_beyond_rates(pd.read_csv(beyond_file))

            def beyond_key(rate: BeyondRate) -> Tuple:
                return (
                    rate.rate_set,
                    rate.zone,
                    _round(rate.rate),
                    _round(rate.up_to_miles),
                )

            db_rows = session.query(BeyondRate).all()
            if {beyond_key(r) for r in expected} != {beyond_key(r) for r in db_rows}:
                print("❌ Beyond Rates mismatch.")
                all_ok = False

        access_file = directory / "accessorial_cost.csv"
        if access_file.exists():
            expected = load_accessorials(pd.read_csv(access_file))

            def access_key(acc: Accessorial) -> Tuple:
                return (acc.name, _round(acc.amount), acc.is_percentage)

            db_rows = session.query(Accessorial).all()
            if {access_key(r) for r in expected} != {access_key(r) for r in db_rows}:
                print("❌ Accessorials mismatch.")
                all_ok = False

        return all_ok
    finally:
        if close_session:
            Session.remove()


if __name__ == "__main__":
    parser = ArgumentParser(description="Import Hotshot rate tables from CSV files")
    parser.add_argument(
        "directory", type=Path, help="Directory containing CSV rate tables"
    )
    args = parser.parse_args()
    import_csvs(args.directory)
    if verify_csvs(args.directory):
        print("✅ Import verified.")
    else:
        print("❌ Verification failed.")
