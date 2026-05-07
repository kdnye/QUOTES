# Freight Services Inc. — Quote Tool API Quick-Start Guide

This guide covers everything you need to start generating freight quotes programmatically using the FSI Quote Tool API.

## Accuracy notes

This document is aligned with the current implementation in this repository:

- Uses the same endpoint paths and response fields as `app/api.py`.
- Matches the in-app API Reference semantics from `templates/help/api.html`.
- Corrects formatting and duplication issues from the draft text (for example, malformed base URLs and duplicated sections).

## Getting your API key

Your API key is issued by your FSI account administrator. Once issued, you can view it by logging into the FSI Quote Tool and opening **Help → API Reference**.

Keep your key confidential. If you believe it has been exposed, contact your FSI administrator immediately to have it regenerated.

## Base URL

All endpoints are relative to:

```text
https://quote.freightservices.net/api
```

Use the same host you use for the web tool.

## Authentication

Every request must include an `Authorization` header:

```text
Authorization: Bearer <your_api_key>
```

Behavior:

- Missing header → `401 Unauthorized`
- Malformed header → `401 Unauthorized`
- Invalid/disabled/unapproved key → `403 Forbidden`

## Rate limits

By default, the API allows **30 requests per minute**.

Response headers include:

```text
X-RateLimit-Limit: 30
X-RateLimit-Remaining: 27
X-RateLimit-Reset: 1746564720
```

If you exceed the limit, you receive `429 Too Many Requests`.

## Create a quote

`POST /quote`

Send a JSON body with shipment details. On success, returns `201 Created` with a quote record and unique `quote_id`.

### Required fields

| Field | Type | Description |
| --- | --- | --- |
| `quote_type` | string | `"Hotshot"` or `"Air"` |
| `origin` | string | 5-digit US ZIP code |
| `destination` | string | 5-digit US ZIP code |
| `weight` | number | Actual shipment weight in pounds |

### Optional fields

| Field | Type | Description |
| --- | --- | --- |
| `pieces` | integer | Number of units/pieces (default: `1`) |
| `length` | number | Package length in inches |
| `width` | number | Package width in inches |
| `height` | number | Package height in inches |
| `dim_weight` | number | Pre-calculated dimensional weight in lbs (instead of dimensions) |
| `accessorials` | array of strings | Extra services, e.g. `["Liftgate", "Residential Delivery"]` |
| `user_email` | string | Email to associate with the quote |

### Billable weight

Billable weight is always the greater of actual and dimensional weight.

- If you provide `dim_weight`, that value is used directly.
- If you provide dimensions, dimensional weight is computed by the quote service.
- The response includes `weight_method` (`"actual"` or `"dimensional"`).

> Note: the current backend service code calculates dimensional weight using its internal divisor logic. If your integration relies on an exact divisor, follow server output fields (`dim_weight`, `weight_method`) as source of truth.

### Example response

```json
{
  "quote_id": "Q-BCDFGHJ2",
  "quote_type": "Hotshot",
  "origin": "98001",
  "destination": "90210",
  "weight": 520.0,
  "weight_method": "actual",
  "actual_weight": 520.0,
  "dim_weight": 310.0,
  "pieces": 3,
  "total": 847.50,
  "metadata": {
    "zone": "C",
    "miles": 1142.3,
    "base_rate": 680.00,
    "fuel_surcharge": 102.00,
    "fuel_pct": 0.15,
    "vsc_surcharge": 65.50,
    "accessorials": { "Liftgate": 75.00 },
    "accessorial_total": 75.00
  }
}
```

## Retrieve a saved quote

`GET /quote/{quote_id}`

Example:

```text
GET /quote/Q-BCDFGHJ2
Authorization: Bearer <your_api_key>
```

Returns the same JSON structure as quote creation, or `404 Not Found` if the ID does not exist.

## Accessorials

Accessorials are optional services added to a shipment.

- Pass names in a JSON string array.
- Matching is case-insensitive.
- Unknown names are ignored.

Example:

```json
"accessorials": ["Liftgate", "Residential Delivery"]
```

## Error format

All API errors return:

```json
{
  "error": "Missing Authorization header.",
  "remediation": "Provide an Authorization header using 'Bearer <your_api_key>' and retry the request."
}
```

### Status codes

| Status | Meaning |
| --- | --- |
| `400 Bad Request` | Invalid field values (for example, unsupported `quote_type`) |
| `401 Unauthorized` | Missing or malformed authorization header |
| `403 Forbidden` | Invalid, disabled, or unapproved API key/token |
| `404 Not Found` | Quote ID does not exist |
| `429 Too Many Requests` | Rate limit exceeded |
| `500 Internal Server Error` | Server-side error |

## Examples

### curl — Hotshot quote

```bash
curl -X POST https://quote.freightservices.net/api/quote \
  -H "Authorization: Bearer <your_api_key>" \
  -H "Content-Type: application/json" \
  -d '{
    "quote_type": "Hotshot",
    "origin": "98001",
    "destination": "90210",
    "weight": 520,
    "pieces": 3
  }'
```

### curl — Air quote with dimensions and accessorials

```bash
curl -X POST https://quote.freightservices.net/api/quote \
  -H "Authorization: Bearer <your_api_key>" \
  -H "Content-Type: application/json" \
  -d '{
    "quote_type": "Air",
    "origin": "10001",
    "destination": "90210",
    "weight": 150,
    "length": 48,
    "width": 40,
    "height": 36,
    "accessorials": ["Liftgate", "Residential Delivery"]
  }'
```

### Python

```python
import requests

API_KEY = "<your_api_key>"
BASE_URL = "https://quote.freightservices.net/api"

response = requests.post(
    f"{BASE_URL}/quote",
    headers={"Authorization": f"Bearer {API_KEY}"},
    json={
        "quote_type": "Hotshot",
        "origin": "98001",
        "destination": "90210",
        "weight": 750,
        "pieces": 1,
        "accessorials": ["Liftgate"],
    },
)
response.raise_for_status()
quote = response.json()
print(f"Quote {quote['quote_id']}: ${quote['total']:.2f}")
```

## Spreadsheet integrations

### Google Sheets (Apps Script)

The API can be called directly via `UrlFetchApp.fetch` from Apps Script.

Use this pattern:

- Keep API keys in script properties (not directly in worksheet cells).
- POST to `https://quote.freightservices.net/api/quote`.
- Use `+` for string concatenation in Apps Script.
- Parse JSON and return either total or full breakdown fields.
- Display the `remediation` field from error responses to help users fix invalid inputs.

### Microsoft Excel (Power Query)

Power Query supports POST requests via `Web.Contents(..., [Content = body, ManualStatusHandling = {400, 403, 404, 429}])`.

> **Privacy level required**: Set the data source privacy level to **Organizational** or **Public** in Power Query options. The default "Private" level blocks cross-source data transmission to external hosts.

#### Power Query gotchas

**String concatenation uses `&`, not `+`.**
In Power Query's M language, `+` is strictly arithmetic. Use `&` to join text values:

```m
// Wrong — raises an error
let url = "https://quote.freightservices.net/api/quote" + "/quote"

// Correct
let url = "https://quote.freightservices.net/api/quote"
```

**Avoid naming function parameters the same as JSON field names.**
M can silently shadow a parameter name with a local binding, causing an "unbound name" compile error. Use a prefix on function parameters to prevent collisions:

```m
// Risky — "origin" parameter may shadow the JSON key "origin"
(origin as text, destination as text) => ...

// Safe — prefix parameters clearly
(pOrigin as text, pDestination as text) =>
  let
    body = Json.FromValue([
      quote_type = "Hotshot",
      origin     = pOrigin,
      destination = pDestination,
      weight      = 100
    ])
  in body
```

**Normalize `quote_type` with `Text.Proper`** to tolerate user input like `"hotshot"` or `"HOTSHOT"`:

```m
quote_type = Text.Proper(pQuoteType)  // "hotshot" → "Hotshot"
```

**Always wrap ZIP codes in `Text.From`** to prevent Excel from stripping leading zeros (e.g., ZIP `01001` becomes `1001` when treated as a number):

```m
origin = Text.From(pOrigin)
```

**Use `ManualStatusHandling` to surface API error details.**
Without it, Power Query raises a generic `400 Bad Request` and discards the response body. With it, you can read the `error` and `remediation` fields:

```m
let
  body = Text.ToBinary(Json.FromValue([
    quote_type  = Text.Proper(pQuoteType),
    origin      = Text.From(pOrigin),
    destination = Text.From(pDestination),
    weight      = pWeight
  ])),
  response = Web.Contents(
    "https://quote.freightservices.net/api/quote",
    [
      Headers        = [
        Authorization  = "Bearer " & pApiKey,
        #"Content-Type" = "application/json"
      ],
      Content             = body,
      ManualStatusHandling = {400, 403, 404, 429, 500}
    ]
  ),
  parsed = Json.Document(response),
  result = if Record.HasFields(parsed, "error")
           then error parsed[remediation]
           else parsed
in result
```

> Note: The `Authorization` header value must include `Bearer ` followed by a space before the token. Missing the space causes a `401 Unauthorized`.

For repeatable workflows, parameterize origin/destination with named ranges and refresh.

## Questions?

Contact your FSI account administrator for key issuance/access, and FSI operations for server-side API issues.
