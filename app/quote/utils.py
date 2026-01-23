"""Utility helpers for quote calculation and spreadsheet normalization.

This module contains small, focused functions used throughout the quoting
tool. They primarily operate on :class:`pandas.DataFrame` objects that are
extracted from uploaded spreadsheets. The functions favor flexibility and
graceful fallbacks so that slightly malformed or unlabelled workbooks can
still be processed.
"""

from __future__ import annotations

import pandas as pd


def normalize_workbook(workbook: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """Strip whitespace from column names in every sheet of a workbook."""

    for sheet_name, df in workbook.items():
        # ``DataFrame.columns`` can be any sequence-like object. ``str.strip``
        # is vectorised over the column index to remove stray whitespace.
        df.columns = df.columns.str.strip()
        workbook[sheet_name] = df

    return workbook


def _first_numeric_in_column(series: pd.Series) -> float:
    """Return the first numeric value in ``series``.

    Values may include dollar signs, commas or trailing percent symbols. Any
    instructional text (e.g. containing ``"multiply"``) or percentage values
    are ignored. If no numeric value is found, ``0.0`` is returned.
    """

    for val in series.tolist():
        s = str(val).strip()
        if not s or "multiply" in s.lower():
            continue
        s = s.replace("$", "").replace(",", "")
        if s.endswith("%"):
            continue
        try:
            return float(s)
        except Exception:
            continue
    return 0.0


def calculate_accessorials(accessorials_df: pd.DataFrame, selected: list[str]) -> float:
    """Sum the first numeric value under each selected accessorial column.

    Parameters
    ----------
    accessorials_df:
        Table containing potential accessorial charges.
    selected:
        Accessorial names to include in the total. Whitespace surrounding
        column names is ignored for robustness.

    Returns
    -------
    float
        The total of the first numeric values found in each selected column.
    """

    if accessorials_df is None or not selected:
        return 0.0

    df = accessorials_df.copy()
    df.columns = [str(c).strip() for c in df.columns]

    total = 0.0
    for acc in selected:
        col = str(acc).strip()
        if col in df.columns:
            total += _first_numeric_in_column(df[col])

    return total
