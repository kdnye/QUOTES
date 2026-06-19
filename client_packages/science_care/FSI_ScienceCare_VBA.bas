' FSI Quote Tool - Science Care 3_MASTER Integration
' -----------------------------------------------------------------------
' Wires the SHIPMENT tabs in 3_MASTER_TOOL to the FSI Quote API.
' For each press, calls the API twice (Air, Hotshot) and writes the totals
' into the "FS by Air" and "FS by Hot Shot" cells. Origin ZIP is resolved
' from the lab code in B4 via the workbook's existing
' "Drop downs OTH - SC" lookup table.
'
' International shipments (B7 filled) are skipped for now - the API
' currently supports domestic Air and Hotshot only.
'
' SETUP:
'   1. Save the workbook as .xlsm.
'   2. Alt+F11 > Insert > Module > paste this entire file.
'   3. Replace YOUR_API_KEY_HERE in SECTION 1 with the FSI API key.
'   4. Confirm cell addresses in SECTION 2 match the SHIPMENT 1 layout
'      (SHIPMENT 1 is the source of truth; the other SHIPMENT tabs are
'      being brought into line with it).
'   5. Alt+Q to close.
'   6. Insert > Shapes > draw a button on each SHIPMENT tab,
'      right-click > Assign Macro > RunScienceCareQuote.
' -----------------------------------------------------------------------

Option Explicit

' ==========================================================================
' SECTION 1 - API CREDENTIALS
' ==========================================================================
Private Const API_KEY As String = "YOUR_API_KEY_HERE"
Private Const API_URL As String = "https://quote.freightservices.net/api/quote"

' Standard FSI dimensional weight divisor (cubic inches per lb).
Private Const DIM_DIVISOR As Long = 166

' Name of the hidden lookup sheet that maps SC Lab code (col A) -> ZIP (col B).
Private Const LAB_LOOKUP_SHEET As String = "Drop downs OTH - SC"
Private Const LAB_LOOKUP_RANGE As String = "A2:B100"

' ==========================================================================
' SECTION 2 - SHIPMENT TAB CELL REFERENCES
' Defaults match the SHIPMENT 1 layout in 3_MASTER_TOOL_2026. Click each
' target cell on your sheet and read its address in the Name Box (top-left)
' to verify before running.
' ==========================================================================

' Header inputs - defaults match the SHIPMENT 1 layout in 3_MASTER_TOOL.
Private Const CELL_LAB_CODE       As String = "B4"   ' SC Lab abbreviation (e.g. "SCCA"). VLOOKUP'd against "Drop downs OTH - SC" to get origin ZIP.
Private Const CELL_DEST_ZIP       As String = "B5"   ' US destination ZIP (5 digits)
Private Const CELL_INTL_COUNTRY   As String = "B7"   ' International country - if filled, macro skips (API is domestic-only)

' Accessorial Y/N markers - cell holds "Y" when that service is selected.
' Cells without an API equivalent (Weekend J6, VSC J9) are intentionally not
' sent: VSC is computed server-side from zone; Weekend has no API name.
Private Const CELL_ACC_4HR_WINDOW   As String = "J3"  ' 4 Hour Delivery/Pick-Up Window ($50)
Private Const CELL_ACC_SPECIAL_TIME As String = "J4"  ' Special Pickup or Delivery Time (+$95)
Private Const CELL_ACC_AFTERHOURS   As String = "J5"  ' Afterhours Delivery/Pickup (+$110)
Private Const CELL_ACC_TWO_MAN      As String = "J7"  ' Two-Man Team Required (+$125)
Private Const CELL_ACC_LIFTGATE     As String = "J8"  ' Liftgate Required (+$75)

' Box-type quantity cells (one row per box type).
Private Const CELL_QTY_MEDIUM   As String = "A26"  ' Medium    20"x15"x18"
Private Const CELL_QTY_LARGE    As String = "A27"  ' Large     32"x18"x20"
Private Const CELL_QTY_XLARGE   As String = "A28"  ' X-Large   52"x20"x15"
Private Const CELL_QTY_AIRTRAY  As String = "A29"  ' Airtray   79"x24"x15"

' Shipment totals
Private Const CELL_TOTAL_WEIGHT As String = "I37"  ' TOTAL SHIPMENT WEIGHT (lbs)
Private Const CELL_TOTAL_BOXES  As String = "A30"  ' Total Boxes count

' Output cells - results are written here after both API calls complete.
Private Const CELL_OUT_AIR_TOTAL  As String = "C40"  ' FS by Air: total price ($)
Private Const CELL_OUT_AIR_STATUS As String = "B40"  ' Air status: "Success" or error detail
Private Const CELL_OUT_HOT_TOTAL  As String = "C41"  ' FS by Hot Shot: total price ($)
Private Const CELL_OUT_HOT_MILES  As String = "H41"  ' Hot Shot Miles
Private Const CELL_OUT_HOT_STATUS As String = "B41"  ' Hotshot status: "Success" or error detail

' Number of SHIPMENT N tabs in the workbook - RunAllShipmentQuotes loops
' "SHIPMENT 1" through "SHIPMENT <this>". Tabs that don't exist are
' counted as "missing" in the summary; bump this if you add tabs.
Private Const SHIPMENT_TAB_COUNT As Long = 7

' "Total S&H" summary table - lives on SHIPMENT 1. Row N picks the
' cheapest of {Air, Hotshot, Established Lane} for SHIPMENT N; the grand
' total cell sums those rows.
'   SUMMARY_FIRST_ROW = 44   -> SHIPMENT 1 lands in C44, SHIPMENT 2 in C45, ...
'   SUMMARY_COL       = "C"
' The grand-total row is computed at runtime as
' SUMMARY_FIRST_ROW + SHIPMENT_TAB_COUNT, so bumping SHIPMENT_TAB_COUNT
' shifts the total down instead of overwriting the last shipment row.
Private Const SUMMARY_SHEET     As String = "SHIPMENT 1"
Private Const SUMMARY_FIRST_ROW As Long   = 44
Private Const SUMMARY_COL       As String = "C"

' Established-lane VLOOKUP cell on each SHIPMENT tab. Holds either the
' numeric lane price or the string "N/A". Folded into the cheapest-freight
' calc that drives the summary table.
Private Const CELL_ESTABLISHED_LANE As String = "C42"

' ==========================================================================
' SECTION 3 - ACCESSORIAL NAME MAPPING
' Maps each form label to the FSI API accessorial string. The API ignores
' names it does not recognise.
' ==========================================================================
Private Function AccName(cellAddr As String) As String
    Select Case cellAddr
        Case CELL_ACC_4HR_WINDOW:   AccName = "PickUp 4 Hour Window (e.g 10:00-14:00)"
        Case CELL_ACC_SPECIAL_TIME: AccName = "Specific PickUp Time (e.g. Deliver at 9:30am)"
        Case CELL_ACC_AFTERHOURS:   AccName = "Delivery After Hours (17:01-07:59)"
        Case CELL_ACC_TWO_MAN:      AccName = "Two Man Delivery"
        Case CELL_ACC_LIFTGATE:     AccName = "Liftgate Delivery"
        Case Else:                  AccName = ""
    End Select
End Function


' -----------------------------------------------------------------------
' PUBLIC ENTRY POINT - assign this macro to your per-tab quote button.
' Runs on whichever SHIPMENT sheet is currently active.
' -----------------------------------------------------------------------
Public Sub RunScienceCareQuote()
    If API_KEY = "YOUR_API_KEY_HERE" Or Len(Trim(API_KEY)) = 0 Then
        MsgBox "API key not configured. Edit SECTION 1 of the FSI_ScienceCare module.", _
               vbExclamation, "FSI Quote"
        Exit Sub
    End If

    Dim outcome As String
    outcome = QuoteShipment(ActiveSheet, False)
    UpdateSummaryTable
End Sub


' -----------------------------------------------------------------------
' PUBLIC ENTRY POINT - one-click batch over every SHIPMENT tab.
' VBA is single-threaded, so the calls are sequential, but with screen
' updates suppressed and one summary popup the whole pass typically
' finishes in a few seconds.
' Assign this macro to a button on any tab (or in the ribbon).
' -----------------------------------------------------------------------
Public Sub RunAllShipmentQuotes()
    If API_KEY = "YOUR_API_KEY_HERE" Or Len(Trim(API_KEY)) = 0 Then
        MsgBox "API key not configured. Edit SECTION 1 of the FSI_ScienceCare module.", _
               vbExclamation, "FSI Quote"
        Exit Sub
    End If

    ' DoEvents below lets Excel process clicks, so guard against a second
    ' click on the batch button mid-run starting a nested execution.
    Static isRunning As Boolean
    If isRunning Then Exit Sub
    isRunning = True

    Dim prevCalc As Long
    prevCalc = Application.Calculation

    ' Any unexpected error from here on jumps to CleanUp so the global
    ' Application state is restored before the macro returns.
    On Error GoTo CleanUp
    Application.ScreenUpdating = False
    Application.Calculation = xlCalculationManual
    Application.EnableEvents = False

    Dim succeeded As Long, skipped As Long, failed As Long, missing As Long
    Dim summary As String
    summary = ""

    Dim n As Long
    For n = 1 To SHIPMENT_TAB_COUNT
        Dim tabName As String
        tabName = "SHIPMENT " & n

        Dim ws As Worksheet
        Set ws = Nothing
        On Error Resume Next
        Set ws = ThisWorkbook.Sheets(tabName)
        On Error GoTo CleanUp

        If ws Is Nothing Then
            missing = missing + 1
            summary = summary & tabName & ": tab not found" & vbLf
        Else
            Dim outcome As String
            outcome = QuoteShipment(ws, True)
            Select Case True
                Case outcome = "Success"
                    succeeded = succeeded + 1
                Case Left(outcome, 7) = "Skipped"
                    skipped = skipped + 1
                Case Else
                    failed = failed + 1
            End Select
            summary = summary & tabName & ": " & outcome & vbLf
        End If

        DoEvents
    Next n

    UpdateSummaryTable

    Application.Calculation = prevCalc
    Application.EnableEvents = True
    Application.ScreenUpdating = True
    Application.Calculate

    MsgBox "Batch quote complete." & vbLf & vbLf & _
           "Succeeded: " & succeeded & vbLf & _
           "Skipped:   " & skipped & vbLf & _
           "Failed:    " & failed & _
           IIf(missing > 0, vbLf & "Missing tabs: " & missing, "") & vbLf & vbLf & _
           summary, vbInformation, "FSI Quote"
    isRunning = False
    Exit Sub

CleanUp:
    Dim errNum As Long, errDesc As String
    errNum = Err.Number
    errDesc = Err.Description
    Application.Calculation = prevCalc
    Application.EnableEvents = True
    Application.ScreenUpdating = True
    isRunning = False
    If errNum <> 0 Then
        MsgBox "Batch quote aborted: " & errDesc & " (Err " & errNum & ")", _
               vbCritical, "FSI Quote"
    End If
End Sub


' -----------------------------------------------------------------------
' QuoteShipment - run Air + Hotshot quotes for one SHIPMENT tab.
' Returns a short status string: "Success", "Skipped: ...", or "Error: ...".
' When silent is True, no MsgBox is shown - results still land in the
' sheet's status / total cells. The batch runner uses silent=True so a
' single summary popup replaces seven per-sheet popups.
' -----------------------------------------------------------------------
Private Function QuoteShipment(ws As Worksheet, silent As Boolean) As String
    ' Clear previous outputs
    ws.Range(CELL_OUT_AIR_TOTAL).ClearContents
    ws.Range(CELL_OUT_AIR_STATUS).ClearContents
    ws.Range(CELL_OUT_HOT_TOTAL).ClearContents
    ws.Range(CELL_OUT_HOT_MILES).ClearContents
    ws.Range(CELL_OUT_HOT_STATUS).ClearContents

    ' --- International guard: API is domestic-only for now ---
    Dim intlCountry As String
    Dim intlVal As Variant
    intlVal = ws.Range(CELL_INTL_COUNTRY).Value
    If IsError(intlVal) Then intlVal = ""
    intlCountry = Trim(CStr(intlVal))
    If Len(intlCountry) > 0 Then
        SetStatus ws, "Air", "Skipped: international (B7 = " & intlCountry & ")"
        SetStatus ws, "Hotshot", "Skipped: international"
        If Not silent Then
            MsgBox "International shipment detected in " & CELL_INTL_COUNTRY & " (" & intlCountry & ")." & vbLf & _
                   "The FSI Quote API currently supports domestic Air and Hotshot only.", _
                   vbInformation, "FSI Quote"
        End If
        QuoteShipment = "Skipped: international"
        Exit Function
    End If

    ' --- Origin ZIP: look up B4 (lab code) in "Drop downs OTH - SC" ---
    Dim labCode As String
    Dim labVal As Variant
    labVal = ws.Range(CELL_LAB_CODE).Value
    If IsError(labVal) Then labVal = ""
    labCode = Trim(CStr(labVal))
    If Len(labCode) = 0 Then
        SetStatus ws, "Air", "Error: SC Lab cell " & CELL_LAB_CODE & " is empty."
        SetStatus ws, "Hotshot", "Error: SC Lab cell " & CELL_LAB_CODE & " is empty."
        If Not silent Then MsgBox "SC Lab cell (" & CELL_LAB_CODE & ") is empty.", vbExclamation, "FSI Quote"
        QuoteShipment = "Error: SC Lab empty"
        Exit Function
    End If

    Dim originZip As String
    originZip = LookupLabZip(labCode)
    If Len(originZip) = 0 Then
        Dim msg As String
        msg = "Lab code """ & labCode & """ not found in '" & LAB_LOOKUP_SHEET & "'!" & LAB_LOOKUP_RANGE & "."
        SetStatus ws, "Air", "Error: " & msg
        SetStatus ws, "Hotshot", "Error: " & msg
        If Not silent Then MsgBox msg, vbExclamation, "FSI Quote"
        QuoteShipment = "Error: lab not found"
        Exit Function
    End If
    ' Reject "00000" too: FormatZip pads a blank/zero result up to 5 digits.
    If Not originZip Like "#####" Or originZip = "00000" Then
        Dim invalidMsg As String
        invalidMsg = "Resolved origin ZIP """ & originZip & """ for lab """ & labCode & """ is invalid."
        SetStatus ws, "Air", "Error: " & invalidMsg
        SetStatus ws, "Hotshot", "Error: " & invalidMsg
        If Not silent Then MsgBox invalidMsg, vbExclamation, "FSI Quote"
        QuoteShipment = "Error: invalid origin ZIP"
        Exit Function
    End If

    ' --- Destination ZIP ---
    Dim destZip As String
    Dim destRaw As Variant
    destRaw = ws.Range(CELL_DEST_ZIP).Value
    If IsError(destRaw) Then
        SetStatus ws, "Air", "Error: Destination ZIP " & CELL_DEST_ZIP & " contains a worksheet error."
        SetStatus ws, "Hotshot", "Error: Destination ZIP contains a worksheet error."
        If Not silent Then MsgBox "Destination ZIP cell (" & CELL_DEST_ZIP & ") contains a worksheet error.", vbExclamation, "FSI Quote"
        QuoteShipment = "Error: destination ZIP cell error"
        Exit Function
    End If
    If IsEmpty(destRaw) Or CStr(destRaw) = "" Then
        SetStatus ws, "Air", "Error: Destination ZIP " & CELL_DEST_ZIP & " is empty."
        SetStatus ws, "Hotshot", "Error: Destination ZIP empty."
        If Not silent Then MsgBox "Destination ZIP cell (" & CELL_DEST_ZIP & ") is empty.", vbExclamation, "FSI Quote"
        QuoteShipment = "Error: destination ZIP empty"
        Exit Function
    End If
    destZip = FormatZip(destRaw)
    If Not destZip Like "#####" Then
        SetStatus ws, "Air", "Error: Destination ZIP invalid (" & destZip & ")"
        SetStatus ws, "Hotshot", "Error: Destination ZIP invalid"
        If Not silent Then MsgBox "Destination ZIP """ & destZip & """ must be 5 digits.", vbExclamation, "FSI Quote"
        QuoteShipment = "Error: invalid destination ZIP"
        Exit Function
    End If

    ' --- Weight and pieces ---
    Dim totalWeight As Double
    Dim totalBoxes  As Long

    On Error GoTo InputError
    totalWeight = CDbl(ws.Range(CELL_TOTAL_WEIGHT).Value)
    Dim bVal As Variant
    bVal = ws.Range(CELL_TOTAL_BOXES).Value
    If IsEmpty(bVal) Or CStr(bVal) = "" Or Not IsNumeric(bVal) Then
        totalBoxes = 1
    Else
        totalBoxes = CLng(bVal)
        If totalBoxes < 1 Then totalBoxes = 1
    End If
    On Error GoTo 0

    If totalWeight <= 0 Then
        SetStatus ws, "Air", "Error: Total weight " & CELL_TOTAL_WEIGHT & " <= 0"
        SetStatus ws, "Hotshot", "Error: Total weight <= 0"
        If Not silent Then MsgBox "Total Shipment Weight cell (" & CELL_TOTAL_WEIGHT & ") must be greater than 0.", _
                                  vbExclamation, "FSI Quote"
        QuoteShipment = "Error: weight <= 0"
        Exit Function
    End If

    ' --- Dimensional weight (sum across all box types with quantity) ---
    Dim dimWeight As Double
    dimWeight = CalcTotalDimWeight(ws)

    ' --- Accessorials ---
    Dim accJson As String
    accJson = BuildAccessorialsJson(ws)

    ' --- Shared payload fragment ---
    ' Note: not named "shared" — that's a reserved word in VBA and triggers
    ' a compile-time syntax error on the Dim line.
    Dim payloadBase As String
    payloadBase = """origin"": """ & originZip & """, " & _
                  """destination"": """ & destZip & """, " & _
                  """weight"": " & Trim(Str(totalWeight)) & ", " & _
                  """pieces"": " & totalBoxes
    If dimWeight > 0 Then
        payloadBase = payloadBase & ", ""dim_weight"": " & Trim(Str(dimWeight))
    End If
    If Len(accJson) > 0 Then
        payloadBase = payloadBase & ", ""accessorials"": [" & accJson & "]"
    End If

    ' --- Air quote ---
    Dim airResp As String
    airResp = PostQuote("{""quote_type"": ""Air"", " & payloadBase & "}")
    ParseAndWrite ws, airResp, "Air"

    ' --- Hotshot quote ---
    Dim hotResp As String
    hotResp = PostQuote("{""quote_type"": ""Hotshot"", " & payloadBase & "}")
    ParseAndWrite ws, hotResp, "Hotshot"

    If Not silent Then
        MsgBox "Done. Air -> " & CELL_OUT_AIR_TOTAL & ", Hotshot -> " & CELL_OUT_HOT_TOTAL & ".", _
               vbInformation, "FSI Quote"
    End If
    QuoteShipment = "Success"
    Exit Function

InputError:
    SetStatus ws, "Air", "Error: could not read " & CELL_TOTAL_WEIGHT & " / " & CELL_TOTAL_BOXES
    SetStatus ws, "Hotshot", "Error: could not read weight / boxes"
    If Not silent Then
        MsgBox "Could not read Weight or Boxes. Check cells " & _
               CELL_TOTAL_WEIGHT & " and " & CELL_TOTAL_BOXES & ".", _
               vbExclamation, "FSI Quote"
    End If
    QuoteShipment = "Error: could not read weight / boxes"
End Function


' -----------------------------------------------------------------------
' LookupLabZip - resolves SC Lab code (B4) -> 5-digit origin ZIP using
' the workbook's existing "Drop downs OTH - SC" sheet so adding new labs
' is a sheet edit, never a VBA edit.
' Returns "" when the code is not found.
' -----------------------------------------------------------------------
Private Function LookupLabZip(labCode As String) As String
    Dim lookupSheet As Worksheet
    On Error Resume Next
    Set lookupSheet = ThisWorkbook.Sheets(LAB_LOOKUP_SHEET)
    On Error GoTo 0
    If lookupSheet Is Nothing Then
        LookupLabZip = ""
        Exit Function
    End If

    Dim result As Variant
    On Error Resume Next
    result = Application.WorksheetFunction.VLookup( _
                UCase(Trim(labCode)), _
                lookupSheet.Range(LAB_LOOKUP_RANGE), _
                2, False)
    If Err.Number <> 0 Then
        Err.Clear
        LookupLabZip = ""
        Exit Function
    End If
    On Error GoTo 0

    ' VLookup itself can succeed while returning an Error variant if the
    ' matched cell holds #N/A / #REF! etc. Don't pass that into FormatZip.
    If IsError(result) Then
        LookupLabZip = ""
    Else
        LookupLabZip = FormatZip(result)
    End If
End Function


' -----------------------------------------------------------------------
' CalcTotalDimWeight - sum of (L x H x W x qty) / DIM_DIVISOR across all
' box types with a positive quantity. Returns 0 when no boxes are filled
' in (API will then use actual weight only).
' -----------------------------------------------------------------------
Private Function CalcTotalDimWeight(ws As Worksheet) As Double
    Dim qtyCells(0 To 3) As String
    Dim L(0 To 3) As Double, H(0 To 3) As Double, W(0 To 3) As Double

    qtyCells(0) = CELL_QTY_MEDIUM:  L(0) = 20: H(0) = 15: W(0) = 18
    qtyCells(1) = CELL_QTY_LARGE:   L(1) = 32: H(1) = 18: W(1) = 20
    qtyCells(2) = CELL_QTY_XLARGE:  L(2) = 52: H(2) = 20: W(2) = 15
    qtyCells(3) = CELL_QTY_AIRTRAY: L(3) = 79: H(3) = 24: W(3) = 15

    Dim total As Double, i As Long
    total = 0
    For i = 0 To 3
        Dim v As Variant
        v = ws.Range(qtyCells(i)).Value
        If Not IsError(v) Then
            If Not (IsEmpty(v) Or CStr(v) = "") And IsNumeric(v) Then
                Dim qty As Long
                qty = CLng(v)
                If qty > 0 Then
                    total = total + (L(i) * H(i) * W(i) * qty) / DIM_DIVISOR
                End If
            End If
        End If
    Next i

    CalcTotalDimWeight = total
End Function


' -----------------------------------------------------------------------
' BuildAccessorialsJson - JSON string-array body from Y markers in J3-J8.
' -----------------------------------------------------------------------
Private Function BuildAccessorialsJson(ws As Worksheet) As String
    ' Note: not named "cells" — that shadows the Cells property on
    ' Worksheet/Range and trips VBA's "Ambiguous name" check on some setups.
    Dim accCells(0 To 4) As String
    accCells(0) = CELL_ACC_4HR_WINDOW
    accCells(1) = CELL_ACC_SPECIAL_TIME
    accCells(2) = CELL_ACC_AFTERHOURS
    accCells(3) = CELL_ACC_TWO_MAN
    accCells(4) = CELL_ACC_LIFTGATE

    Dim result As String
    result = ""
    Dim i As Long
    For i = 0 To 4
        Dim v As Variant
        v = ws.Range(accCells(i)).Value
        If Not IsError(v) Then
            If UCase(Trim(CStr(v))) = "Y" Then
                Dim accStr As String
                accStr = AccName(accCells(i))
                If Len(accStr) > 0 Then
                    If Len(result) > 0 Then result = result & ", "
                    result = result & """" & EscapeJson(accStr) & """"
                End If
            End If
        End If
    Next i

    BuildAccessorialsJson = result
End Function


' -----------------------------------------------------------------------
' PostQuote - HTTP POST; returns responseText & "|~|" & HTTP status code.
' -----------------------------------------------------------------------
Private Function PostQuote(payload As String) As String
    Dim http As Object
    Set http = CreateObject("MSXML2.XMLHTTP")

    On Error GoTo ConnErr
    http.Open "POST", API_URL, False
    http.setRequestHeader "Authorization", "Bearer " & API_KEY
    http.setRequestHeader "Content-Type", "application/json"
    http.send payload
    On Error GoTo 0

    PostQuote = http.responseText & "|~|" & CStr(http.Status)
    Exit Function

ConnErr:
    PostQuote = "|~|0|~|" & Err.Description
End Function


' -----------------------------------------------------------------------
' ParseAndWrite - extract fields from the API response and write to sheet.
' -----------------------------------------------------------------------
Private Sub ParseAndWrite(ws As Worksheet, rawResp As String, quoteType As String)
    Dim parts() As String
    parts = Split(rawResp, "|~|")

    Dim resp As String
    Dim httpCode As Long
    Dim connErrMsg As String

    Select Case UBound(parts)
        Case 1
            resp = parts(0)
            If IsNumeric(parts(1)) Then
                httpCode = CLng(parts(1))
            Else
                connErrMsg = "Unexpected response format"
            End If
        Case 2
            connErrMsg = "Connection failed: " & parts(2)
        Case Else
            connErrMsg = "Unexpected response"
    End Select

    If Len(connErrMsg) > 0 Then
        SetStatus ws, quoteType, connErrMsg
        Exit Sub
    End If

    Dim rx As Object
    Set rx = CreateObject("VBScript.RegExp")
    rx.Global = True

    If httpCode = 201 Then
        rx.Pattern = """total"":\s*([\d\.]+)"
        Dim mT As Object
        Set mT = rx.Execute(resp)
        If mT.Count > 0 Then
            Dim total As Double
            total = Val(mT(0).SubMatches(0))
            If quoteType = "Air" Then
                ws.Range(CELL_OUT_AIR_TOTAL).Value = total
            Else
                ws.Range(CELL_OUT_HOT_TOTAL).Value = total
            End If
        End If

        If quoteType = "Hotshot" Then
            rx.Pattern = """miles"":\s*([\d\.]+)"
            Dim mM As Object
            Set mM = rx.Execute(resp)
            If mM.Count > 0 Then
                ws.Range(CELL_OUT_HOT_MILES).Value = Val(mM(0).SubMatches(0))
            End If
        End If

        SetStatus ws, quoteType, "Success"
    Else
        rx.Pattern = """remediation"":\s*""([^""]+)"""
        Dim mErr As Object
        Set mErr = rx.Execute(resp)
        Dim errText As String
        If mErr.Count > 0 Then
            errText = "Error: " & mErr(0).SubMatches(0)
        Else
            errText = "HTTP " & httpCode
        End If
        SetStatus ws, quoteType, errText
    End If
End Sub


Private Sub SetStatus(ws As Worksheet, quoteType As String, msg As String)
    If quoteType = "Air" Then
        ws.Range(CELL_OUT_AIR_STATUS).Value = msg
    Else
        ws.Range(CELL_OUT_HOT_STATUS).Value = msg
    End If
End Sub


' -----------------------------------------------------------------------
' UpdateSummaryTable - rebuilds the SHIPMENT 1 "Total S&H" rollup.
' For each SHIPMENT N tab, writes the cheapest available freight cost
' (min of Air / Hotshot / Established Lane) into the matching row of
' the summary column, then writes the sum into the grand-total cell.
' Replaces the in-sheet formulas that broke when the legacy rate-chart
' tabs were removed (Domestic Charts - FS, Int'l Chart - FS, HOTSHOT
' Pricing). Cheap to call from both entry points so the summary stays
' in sync after any quote run.
' -----------------------------------------------------------------------
Private Sub UpdateSummaryTable()
    Dim summarySheet As Worksheet
    On Error Resume Next
    Set summarySheet = ThisWorkbook.Sheets(SUMMARY_SHEET)
    On Error GoTo 0
    If summarySheet Is Nothing Then Exit Sub

    ' Capture Application state and suppress redraws/events/auto-calc for
    ' the duration of the writes - matters most in the single-tab code
    ' path, where the parent macro hasn't already done so. CleanUp always
    ' restores, even if a runtime error fires mid-write (e.g. protected
    ' summary sheet).
    Dim prevScreen As Boolean, prevEvents As Boolean, prevCalc As Long
    prevScreen = Application.ScreenUpdating
    prevEvents = Application.EnableEvents
    prevCalc = Application.Calculation

    On Error GoTo CleanUp
    Application.ScreenUpdating = False
    Application.EnableEvents = False
    Application.Calculation = xlCalculationManual

    Dim grandTotal As Double
    grandTotal = 0

    Dim n As Long
    For n = 1 To SHIPMENT_TAB_COUNT
        Dim ws As Worksheet
        Set ws = Nothing
        On Error Resume Next
        Set ws = ThisWorkbook.Sheets("SHIPMENT " & n)
        On Error GoTo CleanUp

        Dim cheapest As Double
        cheapest = 0
        If Not ws Is Nothing Then cheapest = CheapestFreight(ws)

        summarySheet.Range(SUMMARY_COL & (SUMMARY_FIRST_ROW + n - 1)).Value = cheapest
        grandTotal = grandTotal + cheapest
    Next n

    ' Total row sits immediately after the last shipment row, so growing
    ' SHIPMENT_TAB_COUNT shifts it down rather than colliding with the
    ' last per-shipment row.
    summarySheet.Range(SUMMARY_COL & (SUMMARY_FIRST_ROW + SHIPMENT_TAB_COUNT)).Value = grandTotal

CleanUp:
    Application.ScreenUpdating = prevScreen
    Application.EnableEvents = prevEvents
    Application.Calculation = prevCalc
End Sub


' -----------------------------------------------------------------------
' CheapestFreight - returns the lowest non-zero freight cost found on
' a SHIPMENT tab across {Air C40, Hotshot C41, Established Lane C42}.
' Skips zero, blank, error-value, "N/A", and other non-numeric cells.
' Returns 0 when no quote is available (skipped international, etc.).
' -----------------------------------------------------------------------
Private Function CheapestFreight(ws As Worksheet) As Double
    Dim air As Double, hot As Double, est As Double
    air = SafeNum(ws.Range(CELL_OUT_AIR_TOTAL).Value)
    hot = SafeNum(ws.Range(CELL_OUT_HOT_TOTAL).Value)
    est = SafeNum(ws.Range(CELL_ESTABLISHED_LANE).Value)

    Dim cheapest As Double
    cheapest = 0
    If air > 0 Then cheapest = air
    If hot > 0 And (cheapest = 0 Or hot < cheapest) Then cheapest = hot
    If est > 0 And (cheapest = 0 Or est < cheapest) Then cheapest = est

    CheapestFreight = cheapest
End Function


' -----------------------------------------------------------------------
' SafeNum - returns CDbl(v) for a numeric Variant, 0 otherwise. Treats
' Empty, "", "N/A", #N/A / #VALUE! / #REF! and anything non-numeric as 0
' so the cheapest-of calculation can ignore them without a Type Mismatch.
' -----------------------------------------------------------------------
Private Function SafeNum(v As Variant) As Double
    If IsError(v) Then Exit Function
    If IsEmpty(v) Then Exit Function
    If Not IsNumeric(v) Then Exit Function
    SafeNum = CDbl(v)
End Function


' -----------------------------------------------------------------------
' Helpers
' -----------------------------------------------------------------------
Private Function FormatZip(v As Variant) As String
    Dim s As String
    s = CStr(v)
    Dim dot As Long
    dot = InStr(s, ".")
    If dot > 0 Then s = Left(s, dot - 1)
    Do While Len(s) < 5
        s = "0" & s
    Loop
    FormatZip = Left(s, 5)
End Function

Private Function EscapeJson(s As String) As String
    EscapeJson = Replace(Replace(s, "\", "\\"), """", "\""")
End Function
