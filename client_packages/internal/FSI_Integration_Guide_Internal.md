# FSI Quote Tool — Spreadsheet Integration Guide (Internal)

> **Internal use only.** This guide covers the full API output including fuel surcharge, fuel rate, and VSC surcharge. The client-facing package (`client_packages/FSI_Integration_Guide.md`) omits those columns — use that version for anything distributed to external clients.

---

## What's different from the client package

The client-facing integration hides three pricing components that FSI treats as proprietary:

| Column | Field | Client package |
|---|---|---|
| P | Fuel Surcharge ($) | **Hidden** |
| Q | Fuel % | **Hidden** |
| R | VSC Surcharge ($) | **Hidden** |

Everything else — Quote ID, Total, Weight Method, Billable Weight, Base Rate, Accessorial Total, Zone, Miles, Status — is identical between the two packages.

### Why these are separated

Fuel surcharge and VSC are FSI's margin levers. Exposing the exact rates allows clients to benchmark our surcharges against competitors or reverse-engineer our cost structure. Keeping them internal lets FSI use the breakdown for:

- **Margin analysis** — base_rate + fuel_surcharge + vsc_surcharge + accessorial_total should sum to `total`. Any gap is a rounding artefact.
- **Fuel rate monitoring** — `fuel_pct` (column Q) shows the current fuel surcharge rate applied; track this over time to audit EIA sync jobs.
- **VSC auditing** — verify VSC surcharge is applied correctly for the zone and weight bracket.
- **Client quote reviews** — quickly see which cost component drove a high total without sharing the detail with the client.

---

## File inventory

Use the files in `client_packages/internal/` for internal workbooks:

| File | Use |
|---|---|
| `FSI_Quote_VBA_Internal.bas` | Excel macro — full breakdown |
| `FSI_Quote_PowerQuery_Internal.m` | Excel Power Query — full breakdown |
| `FSI_Quote_AppsScript_Internal.gs` | Google Sheets — full breakdown |

Setup steps are identical to the client package — see the corresponding sections below.

---

## Before you start

You need an **API key**. View it at **Help → API Reference** in the FSI Quote Tool.

---

## Which integration should I use?

| Your situation | Use this |
|---|---|
| Google Sheets | [Google Sheets — Apps Script](#google-sheets--apps-script) |
| Excel, macros allowed | [Excel — VBA](#excel--vba-macro) |
| Excel, macros blocked by IT | [Excel — Power Query](#excel--power-query) |

---

## Spreadsheet layout

### Input columns (A–J) — same as client package

| Col | Header | Notes |
|---|---|---|
| A | Quote Type | `Hotshot` or `Air` |
| B | Origin ZIP | 5-digit. Format as **Text** in Excel to preserve leading zeros |
| C | Destination ZIP | Same |
| D | Weight (lbs) | Actual shipment weight |
| E | Pieces | Blank = 1 |
| F | Accessorials | Comma-separated, e.g. `Liftgate, Residential Delivery` |
| G | Length (in) | Optional — for dim weight calc |
| H | Width (in) | Optional |
| I | Height (in) | Optional |
| J | Dim Weight (lbs) | Pre-calculated alternative to G/H/I |

### Output columns (K–V) — full internal breakdown

| Col | Header | Notes |
|---|---|---|
| K | Quote ID | |
| L | Total ($) | |
| M | Weight Method | `Actual` or `Dimensional` |
| N | Billable Weight | |
| O | Base Rate ($) | Base freight rate |
| **P** | **Fuel Surcharge ($)** | **Internal only** |
| **Q** | **Fuel %** | **Internal only** — e.g. `0.15` = 15% |
| **R** | **VSC Surcharge ($)** | **Internal only** |
| S | Accessorial Total ($) | Sum of all accessorial charges |
| T | Zone | Rate zone, e.g. `C` |
| U | Miles | Route distance |
| V | Status | `Success` or error detail |

**Cost breakdown check:** O + P + R + S = L (Total). Any small rounding discrepancy is expected.

---

## Google Sheets — Apps Script

### Setup

1. Open your internal Google Sheet.
2. **Extensions > Apps Script** — delete existing code and paste `FSI_Quote_AppsScript_Internal.gs`.
3. Save, refresh the sheet.
4. **FSI Quotes (Internal) > Set API key** — paste your key.
5. Authorise the script on first run (same Google prompt as client version).

### Using it

**Single row:** click a data cell → **FSI Quotes (Internal) > Get quote — current row**.

**All rows:** **FSI Quotes (Internal) > Process all rows**.

Results land in K–V. Fuel surcharge (P), fuel % (Q), and VSC (R) are populated alongside the standard fields.

---

## Excel — VBA Macro

### Setup

1. Save workbook as `.xlsm`.
2. **Alt+F11 > Insert > Module** — paste `FSI_Quote_VBA_Internal.bas`.
3. Set `API_KEY` near the top of the module.
4. **Alt+Q** to close. Add buttons if desired (assign `GenerateFSIQuote` / `BatchGenerateFSIQuotes`).

### Using it

Click **Enable Content** on open. Run `GenerateFSIQuote` for the active row or `BatchGenerateFSIQuotes` for all rows. Results write to K–V.

---

## Excel — Power Query

### Setup

Follow the same steps as the client Power Query setup but:

- Paste `FSI_Quote_PowerQuery_Internal.m` and name the query **`FSIQuoteInternal`** (not `FSIQuote`).
- In your Custom Column formula use `FSIQuoteInternal(...)` instead of `FSIQuote(...)`.
- When expanding the result record, also select `fuel_surcharge`, `fuel_pct`, and `vsc_surcharge` alongside the standard fields.

---

## Dimensional weight

Same behaviour as client package — see the client guide for details.

---

## Rate limits and batch tips

API allows **30 requests per minute**. Apps Script batch pauses automatically; VBA batch may need `Application.Wait` if you see HTTP 429s.

---

## Common errors

Same error codes as the client package. Additionally:

| Status column shows | Cause |
|---|---|
| `fuel_surcharge` / `fuel_pct` / `vsc_surcharge` cells empty | API `metadata.details` key missing — check API version |
| Fuel % shows `0` on every quote | EIA fuel rate sync may not have run — check admin sync logs |

---

## Email confirmation

Same feature as the client package — add `"send_email": true` to the JSON payload to send a quote summary to the email address on your API account. See the client integration guide for per-integration code snippets.

Internal note: the email uses the same `quote_copy` template and respects the same `MAIL_RATE_LIMIT_PER_USER_PER_HOUR` / `MAIL_RATE_LIMIT_PER_USER_PER_DAY` limits as the in-app "Email this quote to me" button. If a user hits the limit, `email_sent` in the API response will be `false`.

---

## Questions?

API key issues → FSI account administrator.
Server-side errors (HTTP 500) → FSI operations.
