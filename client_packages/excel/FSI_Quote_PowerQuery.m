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
//   6. To call from a table: add a Custom Column using the formula
//         = FSIQuote( [API Key], [Quote Type], [Origin ZIP],
//                     [Destination ZIP], [Weight lbs], [Pieces], [Accessorials] )
//      Each cell in [Accessorials] should contain a comma-separated list
//      such as:  Liftgate, Residential Delivery
//
// Column notes:
//   - [API Key]        Text — your FSI API key (can use a single cell referenced
//                      from a named range so you only enter it once)
//   - [Quote Type]     Text — "Hotshot" or "Air" (case-insensitive, normalised below)
//   - [Origin ZIP]     Text or Number — leading zeros preserved automatically
//   - [Destination ZIP] Text or Number — same
//   - [Weight lbs]     Number
//   - [Pieces]         Number or null  (null defaults to 1)
//   - [Accessorials]   Text or null    (null = no accessorials)
//
// The function returns a record with three fields:
//   quote_id  — e.g. "Q-BCDFGHJ2"
//   total     — e.g. 847.50
//   status    — "Success" or the API remediation message
// -----------------------------------------------------------------------

let
    FSIQuote = (
        pApiKey       as text,
        pQuoteType    as any,       // typed as any so an empty cell passes null rather than crashing
        pOrigin       as any,
        pDestination  as any,
        pWeight       as number,
        pPieces       as any,
        pAccessorials as any
    ) as record =>

    let
        // Normalize inputs; guard against null/empty Quote Type up-front so the
        // error lands in the status field rather than crashing the whole query.
        quoteType   = if pQuoteType = null or Text.Trim(Text.From(pQuoteType)) = ""
                      then null
                      else Text.Proper(Text.Trim(Text.From(pQuoteType))),
        originText  = Text.End("00000" & Text.Trim(Text.From(pOrigin)), 5),      // preserve leading zeros
        destText    = Text.End("00000" & Text.Trim(Text.From(pDestination)), 5),
        piecesNum   = if pPieces = null then 1 else Number.Round(Number.From(pPieces), 0),

        // Build the base record (required fields only)
        baseRecord  = [
            quote_type  = quoteType,
            origin      = originText,
            destination = destText,
            weight      = pWeight,
            pieces      = piecesNum
        ],

        // Append accessorials only when supplied
        fullRecord  = if pAccessorials = null or Text.Trim(Text.From(pAccessorials)) = ""
                      then baseRecord
                      else Record.AddField(
                               baseRecord,
                               "accessorials",
                               List.Transform(
                                   Text.Split(Text.From(pAccessorials), ","),
                                   Text.Trim
                               )
                           ),

        requestBody = Text.ToBinary(Json.FromValue(fullRecord), TextEncoding.Utf8),

        // POST to the API
        // ManualStatusHandling prevents Power Query from throwing on 4xx/5xx so we
        // can read the "remediation" field from the error body.
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

        result = if quoteType = null
                 then [ quote_id = null, total = null, status = "Error: Quote Type is required." ]
                 else if Record.HasFields(parsed, "error")
                      then [ quote_id = null, total = null, status = parsed[remediation] ]
                      else [ quote_id = parsed[quote_id], total = parsed[total], status = "Success" ]
    in
        result
in
    FSIQuote
