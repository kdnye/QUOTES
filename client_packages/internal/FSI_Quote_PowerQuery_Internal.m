// FSI Quote Tool — Power Query (M) Integration (INTERNAL)
// -----------------------------------------------------------------------
// INTERNAL USE ONLY. This version returns fuel_surcharge, fuel_pct, and
// vsc_surcharge in the result record. The client-facing version
// (excel/FSI_Quote_PowerQuery.m) omits those fields — do not distribute
// this file externally.
// -----------------------------------------------------------------------
// Power Query is built into Excel 2016+ (Data tab > Get Data).
// No add-ins or admin rights required.
//
// SETUP: follow the same steps as the client package, but name this
// query "FSIQuoteInternal" to keep it separate. Call it from your
// Custom Column formula:
//
//   = FSIQuoteInternal(
//         "YOUR_API_KEY_HERE",
//         [#"Quote Type"], [#"Origin ZIP"], [#"Destination ZIP"],
//         [#"Weight (lbs)"], [Pieces], [Accessorials],
//         [#"Length (in)"], [#"Width (in)"], [#"Height (in)"],
//         [#"Dim Weight (lbs)"]
//     )
//
// Expand the result record to select any combination of:
//   quote_id, total, weight_method, billable_weight,
//   base_rate, fuel_surcharge, fuel_pct, vsc_surcharge,   <-- internal fields
//   accessorial_total, zone, miles, status
//
// Tip: base your internal workbook's query on this function and the
// client workbook's query on FSIQuote — same data source, different
// visible columns.
// -----------------------------------------------------------------------

let
    FSIQuoteInternal = (
        pApiKey       as text,
        pQuoteType    as any,
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
        quoteType   = if pQuoteType = null or Text.Trim(Text.From(pQuoteType)) = ""
                      then null
                      else Text.Proper(Text.Trim(Text.From(pQuoteType))),

        originText  = Text.End("00000" & Text.Trim(Text.From(pOrigin)), 5),
        destText    = Text.End("00000" & Text.Trim(Text.From(pDestination)), 5),
        piecesNum   = if pPieces = null then 1 else Number.Round(Number.From(pPieces), 0),

        baseRecord  = [
            quote_type  = quoteType,
            origin      = originText,
            destination = destText,
            weight      = pWeight,
            pieces      = piecesNum
        ],

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

        parsed  = if quoteType = null then null else Json.Document(response),
        meta    = if parsed = null or Record.HasFields(parsed, "error") then null
                  else if Record.HasFields(parsed, "metadata") then parsed[metadata]
                  else null,
        details = if meta = null or not Record.HasFields(meta, "details") then null
                  else meta[details],

        result = if quoteType = null
                 then [
                     quote_id = null, total = null, weight_method = null,
                     billable_weight = null, zone = null,
                     base_rate = null, fuel_surcharge = null, fuel_pct = null,
                     vsc_surcharge = null, accessorial_total = null, miles = null,
                     status = "Error: Quote Type is required."
                 ]
                 else if Record.HasFields(parsed, "error")
                      then [
                          quote_id = null, total = null, weight_method = null,
                          billable_weight = null, zone = null,
                          base_rate = null, fuel_surcharge = null, fuel_pct = null,
                          vsc_surcharge = null, accessorial_total = null, miles = null,
                          status = parsed[remediation]
                      ]
                      else [
                          quote_id          = parsed[quote_id],
                          total             = parsed[total],
                          weight_method     = parsed[weight_method],
                          billable_weight   = parsed[weight],
                          zone              = parsed[zone],
                          base_rate         = if details = null then null else details[base_rate],
                          fuel_surcharge    = if details = null then null else details[fuel_surcharge],
                          fuel_pct          = if details = null then null else details[fuel_pct],
                          vsc_surcharge     = if details = null then null else details[vsc_surcharge],
                          accessorial_total = if meta = null then null else meta[accessorial_total],
                          miles             = if meta = null then null else meta[miles],
                          status            = "Success"
                      ]
    in
        result
in
    FSIQuoteInternal
