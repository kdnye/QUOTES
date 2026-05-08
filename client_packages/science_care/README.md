# FSI Quote Tool — Science Care Integration

Reads the **FREIGHT SERVICES SHIPPING AND HANDLING QUOTE SUMMARY** workbook and calls the FSI Quote API for **Air and Hotshot simultaneously**, writing both results back to the sheet in a single button press.

---

## Multi-sheet workbook support

The workbook has one sheet per Science Care lab. The macros always operate on the **currently focused sheet** — click the lab tab you want to quote, then press the button. No configuration change is needed between labs.

---

## Which file to use

| Environment | File |
|---|---|
| Excel (.xlsm) | `FSI_ScienceCare_VBA.bas` |
| Google Sheets | `FSI_ScienceCare_AppsScript.gs` |

> **Parallelism note:** The VBA version sends Air then Hotshot back-to-back (VBA is single-threaded). The Apps Script version uses `UrlFetchApp.fetchAll()` to fire both requests at the exact same time.

---

## Excel — VBA setup

1. Save the workbook as `.xlsm` if not already.
2. **Alt+F11 > Insert > Module** — paste `FSI_ScienceCare_VBA.bas`.
3. Set `API_KEY` in **SECTION 1**.
4. Verify cell addresses in **SECTION 2** against your actual layout.
5. Fill in the `LabToZip` function in **SECTION 3** with your lab codes and confirmed ZIP codes.
6. **Alt+Q** to close. Add a button: **Insert > Shapes**, draw it, right-click > **Assign Macro > RunScienceCareQuote**.

---

## Google Sheets — Apps Script setup

1. Open your Google Sheets copy.
2. **Extensions > Apps Script** — paste `FSI_ScienceCare_AppsScript.gs`.
3. Save and refresh the sheet.
4. **FSI Quotes (Science Care) > Set API key** — paste your key.
5. Verify row/column numbers in `CONFIG`.
6. Fill in the `LAB_ZIP` object with your confirmed lab codes and ZIPs.

---

## Inputs read from the sheet

| Field | Cell (default) | Notes |
|---|---|---|
| SC Lab code | B3 | Looked up in LabToZip / LAB_ZIP |
| US Zip Code | B5 | Destination ZIP |
| Accessorial Y markers | H2–H9 | "Y" in the cell activates that service |
| Box quantities | A38–A44 | One row per box type |
| Total Shipment Weight | M47 | Lbs — used as actual weight |
| Total Boxes | A45 | Used as piece count |

---

## Dimensional weight

The macro calculates a combined dim weight from all box types that have a quantity:

```
dim_weight = sum of (L × H × W × qty) / 139  for each box type
```

Dimensions (Length × Height × Width, inches) are hard-coded from the form header:

| Box type | L | H | W |
|---|---|---|---|
| Medium | 20 | 15 | 18 |
| Large | 32 | 18 | 20 |
| X-Large | 52 | 20 | 15 |
| Small Airtray | 60 | 21 | 12 |
| Airtray | 79 | 24 | 15 |
| Wide Airtray | — | — | — | *(dims not on form; excluded from calc)* |
| Wide Airtray Small | 60 | 31 | 19 |

The API compares this dim weight against actual weight and uses whichever is higher. `weight_method` in the response confirms which was used.

---

## Accessorial mapping

| Form label | API string sent |
|---|---|
| 4 Hour Delivery/Pick-Up Window | `4 Hour Window` |
| Afterhours Delivery | `Afterhours Delivery` |
| Weekend Delivery | `Weekend Delivery` |
| Special Pickup or Delivery Time | `Special Delivery Time` |
| Afterhours Pickup (Returns Only) | `Afterhours Pickup` |
| Weekend Pickup (Returns Only) | `Weekend Pickup` |
| Two-Man Team Required | `Two-Man Team` |
| Liftgate Required | `Liftgate` |

Confirm accepted names with your FSI representative — the API silently ignores names it does not recognise.

---

## Outputs written to the sheet

| Cell (default) | Content |
|---|---|
| B53 | FS by Air — total price ($) |
| C53 | Air status: `Success` or error detail |
| B54 | FS by Hot Shot — total price ($) |
| G54 | Hot Shot Miles |
| C54 | Hotshot status: `Success` or error detail |

---

## Lab-to-ZIP table

The `LabToZip` / `LAB_ZIP` entries are **examples only** — verify every ZIP before use. Add new lab codes as needed; unknown codes abort the run with a descriptive message so no silent failures occur.

---

## Adjusting cell references

All cell references are defined in a single `CONFIGURATION` block near the top of each file. If Science Care updates the form layout, update only that block — no hunting through the code.
