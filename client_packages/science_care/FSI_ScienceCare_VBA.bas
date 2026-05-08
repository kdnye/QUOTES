Attribute VB_Name = "FSI_ScienceCare"
' FSI Quote Tool — Science Care Integration
' -----------------------------------------------------------------------
' Reads the "FREIGHT SERVICES SHIPPING AND HANDLING QUOTE SUMMARY" workbook
' and calls the FSI Quote API for both Air and Hotshot service types,
' writing the live results back to the FS by Air and FS by Hot Shot cells.
'
' SETUP (10 minutes):
'   1. Save the Science Care workbook as .xlsm if not already.
'   2. Alt+F11 > Insert > Module > paste this entire file.
'   3. Replace YOUR_API_KEY_HERE in SECTION 1 with your FSI API key.
'   4. Verify every cell address in SECTION 2 against your actual sheet.
'      (Click each target cell; the address shows in the Name Box top-left.)
'   5. Fill in the LabToZip function in SECTION 3 with your lab codes/ZIPs.
'   6. Alt+Q to close. Optional: Insert > Shapes > draw a button >
'      right-click > Assign Macro > RunScienceCareQuote.
'
' NOTE ON "PARALLEL" EXECUTION:
'   VBA is single-threaded. Air and Hotshot calls run back-to-back (usually
'   under two seconds combined). For true simultaneous dispatch use the
'   Google Sheets version (FSI_ScienceCare_AppsScript.gs), which uses
'   UrlFetchApp.fetchAll() to fire both requests at the same time.
' -----------------------------------------------------------------------

Option Explicit

' ==========================================================================
' SECTION 1 — API CREDENTIALS
' ==========================================================================
Private Const API_KEY As String = "YOUR_API_KEY_HERE"
Private Const API_URL As String = "https://quote.freightservices.net/api/quote"

' Standard US domestic dimensional weight divisor (lbs per cubic inch).
' Change only if FSI has confirmed a different divisor for your account.
Private Const DIM_DIVISOR As Long = 139

' ==========================================================================
' SECTION 2 — CELL REFERENCES
' Adjust each address to match your workbook. Click the target cell and
' read its address from the Name Box (top-left of Excel) to confirm.
' ==========================================================================

' Header inputs
Private Const CELL_SC_LAB   As String = "B3"   ' SC Lab code, e.g. "SCIL"
Private Const CELL_DEST_ZIP As String = "B5"   ' US Zip Code (destination)

' Accessorial Y/N markers — cell contains "Y" when that service is selected.
Private Const CELL_ACC_4HR_WINDOW    As String = "H2"   ' 4 Hour Delivery/Pick-Up Window ($50)
Private Const CELL_ACC_AFTERHOURS_DL As String = "H3"   ' Afterhours Delivery 4:31pm-7:59am (+$110)
Private Const CELL_ACC_WEEKEND_DL    As String = "H4"   ' Weekend Delivery (+$125)
Private Const CELL_ACC_SPECIAL_TIME  As String = "H5"   ' Special Pickup or Delivery Time (+$95)
Private Const CELL_ACC_AFTERHOURS_PU As String = "H6"   ' Afterhours Pickup — Returns Only (+$110)
Private Const CELL_ACC_WEEKEND_PU    As String = "H7"   ' Weekend Pickup — Returns Only (+$125)
Private Const CELL_ACC_TWO_MAN       As String = "H8"   ' Two-Man Team Required (+$125)
Private Const CELL_ACC_LIFTGATE      As String = "H9"   ' Liftgate Required (+$75)

' Box-type quantity cells in the TOTAL FEES section.
' These are the Qty values to the LEFT of each box-type label row.
Private Const CELL_QTY_MEDIUM       As String = "A38"  ' Medium          20"x15"x18" (L x H x W)
Private Const CELL_QTY_LARGE        As String = "A39"  ' Large           32"x18"x20"
Private Const CELL_QTY_XLARGE       As String = "A40"  ' X-Large         52"x20"x15"
Private Const CELL_QTY_SM_AIRTRAY   As String = "A41"  ' Small Airtray   60"x21"x12"
Private Const CELL_QTY_AIRTRAY      As String = "A42"  ' Airtray         79"x24"x15"
Private Const CELL_QTY_WIDE_AIRTRAY As String = "A43"  ' Wide Airtray    (dims not printed; excluded from dim-weight calc)
Private Const CELL_QTY_WIDE_SM      As String = "A44"  ' Wide Airtray Sm 60"x31"x19"

' Shipment totals (from the TOTAL FEES summary rows)
Private Const CELL_TOTAL_WEIGHT As String = "M47"  ' TOTAL SHIPMENT WEIGHT (lbs)
Private Const CELL_TOTAL_BOXES  As String = "A45"  ' Total Boxes count

' Output cells — results are written here after both API calls complete.
Private Const CELL_OUT_AIR_TOTAL  As String = "B53"  ' FS by Air: total price ($)
Private Const CELL_OUT_AIR_STATUS As String = "C53"  ' Air: "Success" or error detail
Private Const CELL_OUT_HOT_TOTAL  As String = "B54"  ' FS by Hot Shot: total price ($)
Private Const CELL_OUT_HOT_MILES  As String = "G54"  ' Hot Shot Miles
Private Const CELL_OUT_HOT_STATUS As String = "C54"  ' Hotshot: "Success" or error detail

' ==========================================================================
' SECTION 3 — LAB-TO-ZIP LOOKUP
' Add one Case per lab code. Right-hand value is the 5-digit origin ZIP.
' Verify every ZIP — the example values below may not match your facilities.
' ==========================================================================
Private Function LabToZip(labCode As String) As String
    Select Case UCase(Trim(labCode))
        Case "SCIL":  LabToZip = "92618"   ' Irvine, CA         — VERIFY
        Case "SCAZ":  LabToZip = "85040"   ' Phoenix, AZ        — VERIFY
        Case "SCFL":  LabToZip = "32256"   ' Jacksonville, FL   — VERIFY
        Case "SCGA":  LabToZip = "30349"   ' Atlanta, GA        — VERIFY
        Case "SCMD":  LabToZip = "21042"   ' Columbia, MD       — VERIFY
        Case "SCNJ":  LabToZip = "08816"   ' East Brunswick, NJ — VERIFY
        Case "SCTX":  LabToZip = "77032"   ' Houston, TX        — VERIFY
        Case "SCWA":  LabToZip = "98188"   ' Seattle, WA        — VERIFY
        Case Else:    LabToZip = ""        ' Unknown → abort with message
    End Select
End Function

' ==========================================================================
' SECTION 4 — ACCESSORIAL NAME MAPPING
' Maps each form label to the FSI API accessorial string. The API ignores
' names it does not recognise. Confirm accepted names with your FSI rep.
' ==========================================================================
Private Function AccName(cellAddr As String) As String
    Select Case cellAddr
        Case CELL_ACC_4HR_WINDOW:    AccName = "4 Hour Window"
        Case CELL_ACC_AFTERHOURS_DL: AccName = "Afterhours Delivery"
        Case CELL_ACC_WEEKEND_DL:    AccName = "Weekend Delivery"
        Case CELL_ACC_SPECIAL_TIME:  AccName = "Special Delivery Time"
        Case CELL_ACC_AFTERHOURS_PU: AccName = "Afterhours Pickup"
        Case CELL_ACC_WEEKEND_PU:    AccName = "Weekend Pickup"
        Case CELL_ACC_TWO_MAN:       AccName = "Two-Man Team"
        Case CELL_ACC_LIFTGATE:      AccName = "Liftgate"
        Case Else:                   AccName = ""
    End Select
End Function


' -----------------------------------------------------------------------
' PUBLIC ENTRY POINT — assign this macro to your quote button.
' -----------------------------------------------------------------------
Public Sub RunScienceCareQuote()
    If API_KEY = "YOUR_API_KEY_HERE" Or Len(Trim(API_KEY)) = 0 Then
        MsgBox "API key not configured. Edit SECTION 1 of the FSI_ScienceCare module.", _
               vbExclamation, "FSI Quote"
        Exit Sub
    End If

    Dim ws As Worksheet
    Set ws = ActiveSheet

    ' Clear previous outputs
    ws.Range(CELL_OUT_AIR_TOTAL).ClearContents
    ws.Range(CELL_OUT_AIR_STATUS).ClearContents
    ws.Range(CELL_OUT_HOT_TOTAL).ClearContents
    ws.Range(CELL_OUT_HOT_MILES).ClearContents
    ws.Range(CELL_OUT_HOT_STATUS).ClearContents

    ' --- Origin ZIP ---
    Dim labCode As String
    labCode = Trim(CStr(ws.Range(CELL_SC_LAB).Value))
    If Len(labCode) = 0 Then
        MsgBox "SC Lab cell (" & CELL_SC_LAB & ") is empty.", vbExclamation, "FSI Quote"
        Exit Sub
    End If
    Dim originZip As String
    originZip = LabToZip(labCode)
    If Len(originZip) = 0 Then
        MsgBox "No ZIP mapping found for lab code """ & labCode & """." & vbLf & _
               "Add it to the LabToZip function in SECTION 3.", _
               vbExclamation, "FSI Quote"
        Exit Sub
    End If

    ' --- Destination ZIP ---
    Dim destZip As String
    Dim destRaw As Variant
    destRaw = ws.Range(CELL_DEST_ZIP).Value
    If IsEmpty(destRaw) Or CStr(destRaw) = "" Then
        MsgBox "US Zip Code cell (" & CELL_DEST_ZIP & ") is empty.", _
               vbExclamation, "FSI Quote"
        Exit Sub
    End If
    destZip = FormatZip(destRaw)
    If Not destZip Like "#####" Then
        MsgBox "US Zip Code """ & destZip & """ is invalid — must be 5 digits.", _
               vbExclamation, "FSI Quote"
        Exit Sub
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
        MsgBox "Total Shipment Weight cell (" & CELL_TOTAL_WEIGHT & ") must be greater than 0.", _
               vbExclamation, "FSI Quote"
        Exit Sub
    End If

    ' --- Dimensional weight (sum across all box types with known dims) ---
    Dim dimWeight As Double
    dimWeight = CalcTotalDimWeight(ws)

    ' --- Accessorials ---
    Dim accJson As String
    accJson = BuildAccessorialsJson(ws)

    ' --- Shared payload fragment (everything except quote_type) ---
    Dim shared As String
    shared = """origin"": """ & originZip & """, " & _
             """destination"": """ & destZip & """, " & _
             """weight"": " & Trim(Str(totalWeight)) & ", " & _
             """pieces"": " & totalBoxes
    If dimWeight > 0 Then
        shared = shared & ", ""dim_weight"": " & Trim(Str(dimWeight))
    End If
    If Len(accJson) > 0 Then
        shared = shared & ", ""accessorials"": [" & accJson & "]"
    End If

    ' --- Air quote ---
    Dim airResp As String
    airResp = PostQuote("{""quote_type"": ""Air"", " & shared & "}")
    ParseAndWrite ws, airResp, "Air"

    ' --- Hotshot quote ---
    Dim hotResp As String
    hotResp = PostQuote("{""quote_type"": ""Hotshot"", " & shared & "}")
    ParseAndWrite ws, hotResp, "Hotshot"

    MsgBox "Done. Air and Hotshot quotes written to the sheet.", vbInformation, "FSI Quote"
    Exit Sub

InputError:
    MsgBox "Could not read Weight or Boxes. Check cells " & _
           CELL_TOTAL_WEIGHT & " and " & CELL_TOTAL_BOXES & ".", _
           vbExclamation, "FSI Quote"
End Sub


' -----------------------------------------------------------------------
' CalcTotalDimWeight — returns sum of (L × H × W × qty) / DIM_DIVISOR
' across every box type that has a quantity and known dimensions.
' Returns 0 when no boxes are filled in (API will use actual weight only).
' -----------------------------------------------------------------------
Private Function CalcTotalDimWeight(ws As Worksheet) As Double
    ' Box type data: qty cell, length, height, width (inches)
    ' Dimensions sourced from the Science Care form header.
    ' Wide Airtray dims are not printed on the form; it is excluded.
    Dim qtyCells(6) As String
    Dim L(6) As Double, H(6) As Double, W(6) As Double

    qtyCells(0) = CELL_QTY_MEDIUM:       L(0) = 20: H(0) = 15: W(0) = 18
    qtyCells(1) = CELL_QTY_LARGE:        L(1) = 32: H(1) = 18: W(1) = 20
    qtyCells(2) = CELL_QTY_XLARGE:       L(2) = 52: H(2) = 20: W(2) = 15
    qtyCells(3) = CELL_QTY_SM_AIRTRAY:   L(3) = 60: H(3) = 21: W(3) = 12
    qtyCells(4) = CELL_QTY_AIRTRAY:      L(4) = 79: H(4) = 24: W(4) = 15
    qtyCells(5) = CELL_QTY_WIDE_AIRTRAY: L(5) = 0:  H(5) = 0:  W(5) = 0   ' dims unknown
    qtyCells(6) = CELL_QTY_WIDE_SM:      L(6) = 60: H(6) = 31: W(6) = 19

    Dim total As Double
    total = 0
    Dim i As Long
    For i = 0 To 6
        If L(i) > 0 Then
            Dim v As Variant
            v = ws.Range(qtyCells(i)).Value
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
' BuildAccessorialsJson — return a JSON string-array body from Y markers.
' -----------------------------------------------------------------------
Private Function BuildAccessorialsJson(ws As Worksheet) As String
    Dim cells(7) As String
    cells(0) = CELL_ACC_4HR_WINDOW
    cells(1) = CELL_ACC_AFTERHOURS_DL
    cells(2) = CELL_ACC_WEEKEND_DL
    cells(3) = CELL_ACC_SPECIAL_TIME
    cells(4) = CELL_ACC_AFTERHOURS_PU
    cells(5) = CELL_ACC_WEEKEND_PU
    cells(6) = CELL_ACC_TWO_MAN
    cells(7) = CELL_ACC_LIFTGATE

    Dim result As String
    result = ""
    Dim i As Long
    For i = 0 To 7
        If UCase(Trim(CStr(ws.Range(cells(i)).Value))) = "Y" Then
            Dim name As String
            name = AccName(cells(i))
            If Len(name) > 0 Then
                If Len(result) > 0 Then result = result & ", "
                result = result & """" & EscapeJson(name) & """"
            End If
        End If
    Next i

    BuildAccessorialsJson = result
End Function


' -----------------------------------------------------------------------
' PostQuote — HTTP POST; returns responseText & "|~|" & HTTP status code.
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
' ParseAndWrite — extract fields from the API response and write to sheet.
' -----------------------------------------------------------------------
Private Sub ParseAndWrite(ws As Worksheet, rawResp As String, quoteType As String)
    Dim parts() As String
    parts = Split(rawResp, "|~|")

    Dim resp As String
    Dim httpCode As Long
    Dim connErrMsg As String

    Select Case UBound(parts)
        Case 1  ' Normal response: resp + status code
            resp = parts(0)
            If IsNumeric(parts(1)) Then
                httpCode = CLng(parts(1))
            Else
                connErrMsg = "Unexpected format"
            End If
        Case 2  ' Connection-level error: empty + "0" + description
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
        ' Extract total
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

        ' Hotshot: also extract miles from metadata
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
        Dim mE As Object
        Set mE = rx.Execute(resp)
        Dim errText As String
        If mE.Count > 0 Then
            errText = "Error: " & mE(0).SubMatches(0)
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
