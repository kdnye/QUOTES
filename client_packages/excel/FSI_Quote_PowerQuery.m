// FSI Quote Tool — Power Query (M) Integration
// -----------------------------------------------------------------------
// Use this when macros are disabled in your organisation.
// Power Query is built into Excel 2016+ (Data tab > Get Data).
// No add-ins or admin rights required.
//
// SETUP (one-time, ~10 minutes):
//   1. Data tab > Get Data > Launch Power Query Editor.
//   2. Home > New Source > Blank Query.
//   3. Home > Advanced Editor — replace ALL existing text with this file.
//   4. Click Done. Name the query "FSIQuote" in the Queries panel on the left.
//   5. IMPORTANT — privacy level: File > Options > Data Source Settings >
//      select the freightservices.net entry > Edit Permissions >
//      set Privacy Level to "Organizational" (or "Public").
//      Without this step Power Query blocks the outbound request.
//   6. To call from a table: add a Custom Column using the formula:
//
//         = FSIQuote(
//               [API Key], [Quote Type], [Origin ZIP], [Destination ZIP],
//               [Weight lbs], [Pieces], [Accessorials],
//               [Length in], [Width in], [Height in], [Dim Weight lbs]
//           )
//
//      Expand the resulting record column to get individual output fields.
//
// Parameter notes:
//   pApiKey       Text    — your FSI API key
//   pQuoteType    any     — "Hotshot" or "Air" (case-insensitive; null → error record)
//   pOrigin       any     — 5-digit origin ZIP (leading zeros preserved)
//   pDestination  any     — 5-digit destination ZIP
//   pWeight       number  — actual shipment weight in lbs
//   pPieces       any     — number of pieces; null defaults to 1
//   pAccessorials any     — comma-separated string, e.g. "Liftgate, Residential Delivery"; null = none
//   pLength       any     — package length in inches (optional)
//   pWidth        any     — package width in inches (optional)
//   pHeight       any     — package height in inches (optional)
//                           Provide all three for the API to calculate dim weight,
//                           or leave all three null to use actual weight only.
//   pDimWeight    any     — pre-calculated dim weight in lbs (optional).
//                           Supply pDimWeight OR pLength+pWidth+pHeight, not both.
//                           pDimWeight takes priority if both are provided.
//
// The function returns a record with these fields:
//   quote_id          — e.g. "Q-BCDFGHJ2"
//   total             — total price, e.g. 847.50
//   weight_method     — "actual" or "dimensional"
//   billable_weight   — weight used for pricing (greater of actual and dim)
//   base_rate         — base freight rate ($)
//   fuel_surcharge    — fuel surcharge amount ($)
//   fuel_pct          — fuel surcharge rate, e.g. 0.15 for 15%
//   vsc_surcharge     — VSC surcharge ($)
//   accessorial_total — total accessorial charges ($)
//   zone              — rate zone, e.g. "C"
//   miles             — route distance in miles
//   status            — "Success" or the API remediation message
// -----------------------------------------------------------------------

let
    FSIQuote = (
        pApiKey       as text,
        pQuoteType    as any,       // typed as any so an empty cell passes null rather than crashing
        pOrigin       as any,
        pDestination  as any,
        pWeight       as number,
        pPieces       as any,
        pAccessorials as any,
        optional pLength    as any,
        optional pWidth     as any,
        optional pHeight    as any,
        optional pDimWeight as any
    ) as record =>

    let
        // Guard against null/empty Quote Type up-front so the error lands in the
        // status field rather than crashing the whole query.
        quoteType   = if pQuoteType = null or Text.Trim(Text.From(pQuoteType)) = ""
                      then null
                      else Text.Proper(Text.Trim(Text.From(pQuoteType))),

        originText  = Text.End("00000" & Text.Trim(Text.From(pOrigin)), 5),
        destText    = Text.End("00000" & Text.Trim(Text.From(pDestination)), 5),
        piecesNum   = if pPieces = null then 1 else Number.Round(Number.From(pPieces), 0),

        // Build the base record (required fields)
        baseRecord  = [
            quote_type  = quoteType,
            origin      = originText,
            destination = destText,
            weight      = pWeight,
            pieces      = piecesNum
        ],

        // Append accessorials when supplied
        withAcc = if pAccessorials = null or Text.Trim(Text.From(pAccessorials)) = ""
                  then baseRecord
                  else Record.AddField(
                           baseRecord,
                           "accessorials",
                           List.Transform(
                               Text.Split(Text.From(pAccessorials), ","),
                               Text.Trim
                           )
                       ),

        // Append dimensions.
        // pDimWeight takes priority; fall back to L/W/H only when all three are present.
        hasDimWt  = pDimWeight <> null and Text.Trim(Text.From(pDimWeight)) <> "",
        hasLWH    = pLength <> null and pWidth <> null and pHeight <> null
                    and Text.Trim(Text.From(pLength)) <> ""
                    and Text.Trim(Text.From(pWidth))  <> ""
                    and Text.Trim(Text.From(pHeight))  <> "",

        withDims  = if hasDimWt
                    then Record.AddField(withAcc, "dim_weight", Number.From(pDimWeight))
                    else if hasLWH
                         then Record.AddField(
                                  Record.AddField(
                                      Record.AddField(withAcc, "length", Number.From(pLength)),
                                      "width", Number.From(pWidth)
                                  ),
                                  "height", Number.From(pHeight)
                              )
                         else withAcc,

        requestBody = Text.ToBinary(Json.FromValue(withDims), TextEncoding.Utf8),

        // POST to the API.
        // ManualStatusHandling lets us read "remediation" from error responses
        // instead of Power Query throwing a generic error.
        response = Web.Contents(
            "https://quote.freightservices.net/api/quote",
            [
                Headers = [
                    Authorization   = "Bearer " & pApiKey,
                    #"Content-Type" = "application/json"
                ],
                Content              = requestBody,
                ManualStatusHandling = {400, 401, 403, 404, 429, 500}
            ]
        ),

        parsed = if quoteType = null
                 then null
                 else Json.Document(response),

        meta   = if parsed = null or Record.HasFields(parsed, "error")
                 then null
                 else if Record.HasFields(parsed, "metadata")
                      then parsed[metadata]
                      else null,

        result = if quoteType = null
                 then [
                     quote_id = null, total = null, weight_method = null,
                     billable_weight = null, base_rate = null, fuel_surcharge = null,
                     fuel_pct = null, vsc_surcharge = null, accessorial_total = null,
                     zone = null, miles = null,
                     status = "Error: Quote Type is required."
                 ]
                 else if Record.HasFields(parsed, "error")
                      then [
                          quote_id = null, total = null, weight_method = null,
                          billable_weight = null, base_rate = null, fuel_surcharge = null,
                          fuel_pct = null, vsc_surcharge = null, accessorial_total = null,
                          zone = null, miles = null,
                          status = parsed[remediation]
                      ]
                      else [
                          quote_id          = parsed[quote_id],
                          total             = parsed[total],
                          weight_method     = parsed[weight_method],
                          billable_weight   = parsed[weight],
                          base_rate         = if meta = null then null else meta[base_rate],
                          fuel_surcharge    = if meta = null then null else meta[fuel_surcharge],
                          fuel_pct          = if meta = null then null else meta[fuel_pct],
                          vsc_surcharge     = if meta = null then null else meta[vsc_surcharge],
                          accessorial_total = if meta = null then null else meta[accessorial_total],
                          zone              = if meta = null then null else meta[zone],
                          miles             = if meta = null then null else meta[miles],
                          status            = "Success"
                      ]
    in
        result
in
    FSIQuote
