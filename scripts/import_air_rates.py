"""Import Air-related rate tables from CSV files."""

from __future__ import annotations

import sys
from pathlib import Path

# Allow running this script directly by ensuring project root is on ``sys.path``
sys.path.append(str(Path(__file__).resolve().parents[1]))

from argparse import ArgumentParser
from typing import Iterable, List, Sequence, Tuple, Type, TypeVar

import logging
import pandas as pd
from sqlalchemy.orm import Session as SASession

from app import create_app
from app.models import db, AirCostZone, ZipZone, CostZone, BeyondRate
from app.services.rate_sets import DEFAULT_RATE_SET, normalize_rate_set


logger = logging.getLogger(__name__)

T = TypeVar("T")


def load_zip_zones(df: pd.DataFrame) -> List[ZipZone]:
    """Normalize and convert a ZIP zone DataFrame to ``ZipZone`` objects.

    The input must include the columns ``Zipcode``, ``Dest Zone`` and
    ``BEYOND`` in that order. If the sheet is transposed (the first row
    contains these headers and the column labels are numeric), the data is
    automatically transposed before processing. Missing headers raise a
    ``ValueError`` to surface misformatted uploads.

    ``NaN`` ZIP codes are skipped and numeric ZIPs are coerced to their
    zero-padded 5-digit string representation so lookups function as expected.

    Args:
        df: Source data with ZIP and destination zone columns.

    Returns:
        A list of ``app.models.ZipZone`` instances ready for bulk insertion.
    """

    rename_map = {
        "ZIPCODE": "zipcode",
        "DEST ZONE": "dest_zone",
        "BEYOND": "beyond",
    }
    required_headers = set(rename_map.keys())

    original_columns = list(df.columns)
    df.columns = pd.Index([str(c).strip().upper() for c in df.columns])

    if not required_headers.issubset(df.columns):
        first_col_label = original_columns[0]
        if isinstance(first_col_label, (int, float)):
            header_row = df.iloc[0].astype(str).str.strip().str.upper()
            if required_headers.issubset(set(header_row)):
                df = df.iloc[1:].reset_index(drop=True)
                df.columns = header_row
                df.columns = df.columns.str.strip().str.upper()
            else:
                missing = required_headers.difference(df.columns)
                raise ValueError(
                    "Missing expected columns: "
                    + ", ".join(sorted(missing))
                    + ". Check for missing or transposed headers."
                )
        else:
            missing = required_headers.difference(df.columns)
            raise ValueError(
                "Missing expected columns: "
                + ", ".join(sorted(missing))
                + ". Check for missing or transposed headers."
            )

    # Uppercase and trim incoming headers to ensure consistent renaming
    df.columns = df.columns.str.strip().str.upper()
    normalized = df.rename(columns=rename_map)[list(rename_map.values())]
    normalized["rate_set"] = _normalize_rate_sets(df)

    # Excel often stores ZIP codes as floats which become strings like
    # ``"85001.0"``. Convert to integers first so we keep the expected
    # 5-digit representation and drop rows with missing ZIPs.
    normalized["zipcode"] = pd.to_numeric(normalized["zipcode"], errors="coerce")
    normalized = normalized.dropna(subset=["zipcode"])
    normalized["zipcode"] = normalized["zipcode"].astype(int).astype(str).str.zfill(5)

    normalized = normalized.drop_duplicates(subset=["zipcode", "rate_set"])
    return [ZipZone(**row) for row in normalized.to_dict(orient="records")]


def load_cost_zones(df: pd.DataFrame) -> List[CostZone]:
    """Normalize and convert a cost zone table to ``CostZone`` objects.

    The input must contain ``CONCATENATE`` and ``COST ZONE`` columns. Numeric
    "concat" values are coerced to integers to remove any decimal artifacts from
    CSV parsing (e.g. ``41.0`` becomes ``"41"``). Rows missing either field are
    dropped.

    Args:
        df: Source data with cost zone fields.

    Returns:
        A list of ``app.models.CostZone`` instances ready for bulk insertion.
    """

    df.columns = df.columns.str.strip().str.upper()
    rename_map = {"CONCATENATE": "concat", "COST ZONE": "cost_zone"}
    normalized = df.rename(columns=rename_map)[list(rename_map.values())]
    normalized["rate_set"] = _normalize_rate_sets(df)
    normalized["concat"] = pd.to_numeric(normalized["concat"], errors="coerce")
    normalized = normalized.dropna(subset=["concat", "cost_zone"])
    normalized["concat"] = normalized["concat"].astype(int).astype(str)
    normalized["cost_zone"] = normalized["cost_zone"].astype(str).str.strip()
    return [CostZone(**row) for row in normalized.to_dict(orient="records")]


def load_air_cost_zones(df: pd.DataFrame) -> List[AirCostZone]:
    """Normalize and convert an air cost zone table to ``AirCostZone`` objects.

    Args:
        df: Source data containing zone pricing fields.

    Returns:
        A list of ``app.models.AirCostZone`` instances ready for bulk insertion.

    Rows missing a zone or any pricing values are ignored to avoid inserting
    incomplete records.
    """

    df.columns = df.columns.str.strip().str.upper()
    rename_map = {
        "ZONE": "zone",
        "MIN": "min_charge",
        "PER LB": "per_lb",
        "WEIGHT BREAK": "weight_break",
    }
    normalized = df.rename(columns=rename_map)[list(rename_map.values())]
    normalized["rate_set"] = _normalize_rate_sets(df)
    normalized = normalized.dropna(subset=["zone"])
    normalized["zone"] = normalized["zone"].astype(str).str.strip()

    for col in ["min_charge", "per_lb", "weight_break"]:
        normalized[col] = (
            normalized[col]
            .astype(str)
            .str.replace("$", "", regex=False)
            .str.replace(",", "", regex=False)
            .str.strip()
        )
        normalized[col] = pd.to_numeric(normalized[col], errors="coerce")

    normalized = normalized.dropna()
    return [AirCostZone(**row) for row in normalized.to_dict(orient="records")]


def load_beyond_rates(df: pd.DataFrame) -> List[BeyondRate]:
    """Normalize and convert a beyond price table to ``BeyondRate`` objects.

    Args:
        df: Source data with beyond pricing by zone.

    Returns:
        A list of ``app.models.BeyondRate`` instances ready for bulk insertion.
    """

    df.columns = df.columns.str.strip().str.upper()
    rename_map = {"ZONE": "zone", "RATE": "rate", "UP TO MILES": "up_to_miles"}
    normalized = df.rename(columns=rename_map)[list(rename_map.values())]
    normalized["rate_set"] = _normalize_rate_sets(df)
    normalized = normalized.dropna(subset=["zone"])
    return [BeyondRate(**row) for row in normalized.to_dict(orient="records")]


def _normalize_rate_sets(df: pd.DataFrame) -> pd.Series:
    """Return a normalised ``rate_set`` column for the provided data frame.

    Args:
        df: Source CSV data for a particular rate table.

    Returns:
        ``pd.Series`` containing a lowercased rate set for each row, defaulting
        to :data:`services.rate_sets.DEFAULT_RATE_SET` when the column is
        missing or empty.
    """

    if "RATE_SET" not in df.columns:
        return pd.Series([DEFAULT_RATE_SET] * len(df))

    return (
        df["RATE_SET"]
        .fillna(DEFAULT_RATE_SET)
        .astype(str)
        .str.strip()
        .apply(normalize_rate_set)
    )


def save_unique(
    session: SASession,
    model: Type[T],
    objects: Iterable[T],
    unique_attr: str | Sequence[str],
) -> Tuple[int, int]:
    """Persist only new records for a given model.

    This optimized implementation loads all existing keys up front and keeps an
    in-memory set to check for duplicates. It avoids issuing a ``SELECT`` query
    for every row, dramatically speeding up bulk imports such as the 28k ZIP
    codes in the original rate workbook.

    Args:
        session: Active :class:`sqlalchemy.orm.Session` used for database I/O.
        model: SQLAlchemy model class to query for existing rows.
        objects: Iterable of model instances to insert.
        unique_attr: Model attribute that uniquely identifies a row.

    Returns:
        A tuple of ``(inserted, skipped)`` counts.
    """

    attrs = (unique_attr,) if isinstance(unique_attr, str) else tuple(unique_attr)
    objs = list(objects)
    query_columns = [getattr(model, attr) for attr in attrs]
    rows = session.query(*query_columns).all()
    existing_keys = {
        row if len(attrs) > 1 else row[0] for row in rows  # type: ignore[index]
    }
    to_insert: List[T] = []
    inserted = 0
    skipped = 0
    for obj in objs:
        values = tuple(getattr(obj, attr) for attr in attrs)
        key = values if len(attrs) > 1 else values[0]
        key_display = key[0] if isinstance(key, tuple) else key
        if key in existing_keys:
            logger.info("Skipped existing %s: %s", model.__name__, key_display)
            skipped += 1
        else:
            logger.info("Inserted %s: %s", model.__name__, key_display)
            existing_keys.add(key)
            to_insert.append(obj)
            inserted += 1
    if to_insert:
        session.bulk_save_objects(to_insert)
    return inserted, skipped


def import_csvs(directory: Path) -> None:
    """Load air-related rate tables from CSV files.

    Existing ``ZipZone``, ``CostZone``, and ``AirCostZone`` rows are preserved to
    avoid primary key conflicts. ``BeyondRate`` entries are inserted only if the
    table is empty. The function prints how many ``ZipZone`` rows were inserted
    and skipped, raising ``RuntimeError`` if no new ZIPs are loaded. Requires an
    active Flask application context so :data:`app.models.db.session` is
    available.

    Args:
        directory: Folder containing the CSV files.
    """

    session = db.session
    try:
        zip_file = directory / "Zipcode_Zones.csv"
        if zip_file.exists():
            zip_df = pd.read_csv(zip_file)
            inserted, skipped = save_unique(
                session,
                ZipZone,
                load_zip_zones(zip_df),
                ("zipcode", "rate_set"),
            )
            print(f"Inserted {inserted} ZipZone rows (skipped {skipped}).")
            if inserted == 0:
                raise RuntimeError("No ZipZone rows loaded from CSV")

        cost_file = directory / "cost_zone_table.csv"
        if cost_file.exists():
            cost_df = pd.read_csv(cost_file)
            save_unique(
                session, CostZone, load_cost_zones(cost_df), ("concat", "rate_set")
            )

        air_file = directory / "air_cost_zone.csv"
        if air_file.exists():
            air_df = pd.read_csv(air_file)
            save_unique(
                session,
                AirCostZone,
                load_air_cost_zones(air_df),
                ("zone", "rate_set"),
            )

        beyond_file = directory / "beyond_price.csv"
        if beyond_file.exists() and not session.query(BeyondRate).first():
            beyond_df = pd.read_csv(beyond_file)
            session.bulk_save_objects(load_beyond_rates(beyond_df))

        session.commit()
    finally:
        session.close()


if __name__ == "__main__":
    parser = ArgumentParser(description="Import Air rate tables from CSV files")
    parser.add_argument(
        "directory", type=Path, help="Directory containing CSV rate tables"
    )
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        db.create_all()
        import_csvs(args.directory)

    print("✅ Import complete.")
