import re
import time
from datetime import datetime

import gspread
import pandas as pd
import streamlit as st
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# Colours
DARK_GREEN  = {"red": 0.063, "green": 0.176, "blue": 0.071}
WHITE       = {"red": 1.0,   "green": 1.0,   "blue": 1.0}
MID_GREY    = {"red": 0.95,  "green": 0.95,  "blue": 0.95}

# Cover row colours by Issue Type
TYPE_COLORS = {
    "issue":       {"red": 1.0,  "green": 0.87, "blue": 0.87},   # soft red
    "warning":     {"red": 1.0,  "green": 0.95, "blue": 0.80},   # soft amber
    "opportunity": {"red": 0.87, "green": 0.92, "blue": 1.0},    # soft blue
}

MAX_CELL_LEN = 1000


# ── Auth ─────────────────────────────────────────────────────────────────────

def get_credentials() -> Credentials:
    info = dict(st.secrets["gcp_service_account"])
    return Credentials.from_service_account_info(info, scopes=SCOPES)


def get_client() -> gspread.Client:
    return gspread.authorize(get_credentials())


# ── Main export entry point ───────────────────────────────────────────────────

def create_sheet(sheet_name: str, dataframes: dict, sheet_id: str, progress_cb=None) -> str:
    """
    Write issue DataFrames into an existing user-owned Google Sheet.
    Clears all existing worksheets and rebuilds from scratch.
    Returns the spreadsheet URL.
    """
    client = get_client()

    if progress_cb:
        progress_cb("📊 Opening Google Sheet…")

    sh = client.open_by_key(sheet_id)
    time.sleep(0.5)

    # Keep at least one sheet while clearing — add temp placeholder
    temp = sh.add_worksheet(title="_temp", rows="1", cols="1")
    for ws in sh.worksheets():
        if ws.id != temp.id:
            try:
                sh.del_worksheet(ws)
            except Exception:
                pass
    time.sleep(0.5)

    # Extract Issues Overview df if present — used to enrich cover page
    overview_df = dataframes.get("Issues Overview")

    tab_info = []   # [(tab_name, row_count, gid)]
    skipped  = []

    try:
        for display_name, df in dataframes.items():
            if progress_cb:
                progress_cb(f"📋 Writing: {display_name} ({len(df):,} rows)…")

            tab_name = display_name[:99]

            try:
                try:
                    ws = sh.add_worksheet(
                        title=tab_name,
                        rows=str(len(df) + 5),
                        cols=str(max(len(df.columns), 5)),
                    )
                except gspread.exceptions.APIError:
                    ws = sh.worksheet(tab_name)
                    ws.clear()

                headers = df.columns.tolist()
                rows = [headers] + _df_to_rows(df)

                # Write in chunks with RAW mode (keeps data as-is, no formula parsing)
                chunk_size = 5000
                for i in range(0, len(rows), chunk_size):
                    ws.update(rows[i:i + chunk_size], f"A{i + 1}", value_input_option="RAW")
                    if len(rows) > chunk_size:
                        time.sleep(0.5)

                _format_header(sh, ws, len(headers))
                tab_info.append((tab_name, len(df), ws.id))

            except Exception as e:
                skipped.append(f"{tab_name} ({e})")

            time.sleep(1.2)

        # Cover page — always runs even if some tabs failed
        if progress_cb:
            progress_cb("🏠 Building cover page…")
        _create_cover(sh, sheet_name, tab_info, overview_df=overview_df)

    finally:
        # Always remove temp sheet
        try:
            sh.del_worksheet(sh.worksheet("_temp"))
        except Exception:
            pass

    if skipped and progress_cb:
        progress_cb(f"⚠️ Skipped {len(skipped)} tab(s): {', '.join(skipped)}")

    if not tab_info:
        raise RuntimeError("No tabs were created successfully.")

    return f"https://docs.google.com/spreadsheets/d/{sh.id}"


# ── Cover page ────────────────────────────────────────────────────────────────

def _create_cover(sh: gspread.Spreadsheet, sheet_name: str, tab_info: list, overview_df=None):
    cover = sh.add_worksheet(title="Dashboard", rows="300", cols="10")

    try:
        all_ws = sh.worksheets()
        sh.reorder_worksheets([cover] + [w for w in all_ws if w.id != cover.id])
    except Exception:
        pass

    # Build metadata lookup: tab_name → {type, priority, pct}
    tab_meta = _build_tab_meta(tab_info, overview_df)

    timestamp  = datetime.now().strftime("%d %b %Y at %H:%M")
    total_urls = sum(c for _, c, _ in tab_info)

    rows = [
        [sheet_name, "", "", "", "", ""],
        [f"Exported {timestamp}  ·  {len(tab_info)} issue types  ·  {total_urls:,} affected URLs", "", "", "", "", ""],
        ["", "", "", "", "", ""],
        ["Issue", "Type", "Priority", "Affected URLs", "% of URLs", "Link"],
    ]

    row_types = []

    for tab_name, count, gid in tab_info:
        meta       = tab_meta.get(tab_name, {})
        issue_type = meta.get("type", "")
        priority   = meta.get("priority", "")
        pct        = meta.get("pct", "")

        try:
            pct_display = f"{float(pct):.1f}%" if pct and pct not in ("", "nan") else ""
        except (ValueError, TypeError):
            pct_display = pct

        # USER_ENTERED mode is required for HYPERLINK formula to work
        link = f'=HYPERLINK("#gid={gid}","→ View")'
        rows.append([tab_name, issue_type, priority, count, pct_display, link])
        row_types.append(issue_type.lower())

    # USER_ENTERED so the HYPERLINK formula is parsed correctly
    cover.update(rows, "A1", value_input_option="USER_ENTERED")

    # Build colour requests per data row
    colour_requests = []
    for i, issue_type in enumerate(row_types):
        colour = TYPE_COLORS.get(issue_type, MID_GREY)
        colour_requests.append({
            "repeatCell": {
                "range": {
                    "sheetId": cover.id,
                    "startRowIndex": 4 + i,
                    "endRowIndex":   5 + i,
                    "startColumnIndex": 0,
                    "endColumnIndex":   6,
                },
                "cell": {"userEnteredFormat": {"backgroundColor": colour}},
                "fields": "userEnteredFormat.backgroundColor",
            }
        })

    try:
        sh.batch_update({"requests": [
            # Title
            {"repeatCell": {
                "range": {"sheetId": cover.id, "startRowIndex": 0, "endRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": 6},
                "cell": {"userEnteredFormat": {"textFormat": {"fontSize": 18, "bold": True, "foregroundColor": DARK_GREEN}}},
                "fields": "userEnteredFormat.textFormat",
            }},
            # Subtitle
            {"repeatCell": {
                "range": {"sheetId": cover.id, "startRowIndex": 1, "endRowIndex": 2, "startColumnIndex": 0, "endColumnIndex": 6},
                "cell": {"userEnteredFormat": {"textFormat": {"fontSize": 10, "foregroundColor": {"red": 0.4, "green": 0.4, "blue": 0.4}}}},
                "fields": "userEnteredFormat.textFormat",
            }},
            # Header row
            {"repeatCell": {
                "range": {"sheetId": cover.id, "startRowIndex": 3, "endRowIndex": 4, "startColumnIndex": 0, "endColumnIndex": 6},
                "cell": {"userEnteredFormat": {"backgroundColor": DARK_GREEN, "textFormat": {"bold": True, "foregroundColor": WHITE}}},
                "fields": "userEnteredFormat(backgroundColor,textFormat)",
            }},
            # Column widths
            {"updateDimensionProperties": {"range": {"sheetId": cover.id, "dimension": "COLUMNS", "startIndex": 0, "endIndex": 1}, "properties": {"pixelSize": 300}, "fields": "pixelSize"}},
            {"updateDimensionProperties": {"range": {"sheetId": cover.id, "dimension": "COLUMNS", "startIndex": 1, "endIndex": 2}, "properties": {"pixelSize": 100}, "fields": "pixelSize"}},
            {"updateDimensionProperties": {"range": {"sheetId": cover.id, "dimension": "COLUMNS", "startIndex": 2, "endIndex": 3}, "properties": {"pixelSize": 95},  "fields": "pixelSize"}},
            {"updateDimensionProperties": {"range": {"sheetId": cover.id, "dimension": "COLUMNS", "startIndex": 3, "endIndex": 4}, "properties": {"pixelSize": 120}, "fields": "pixelSize"}},
            {"updateDimensionProperties": {"range": {"sheetId": cover.id, "dimension": "COLUMNS", "startIndex": 4, "endIndex": 5}, "properties": {"pixelSize": 100}, "fields": "pixelSize"}},
            {"updateDimensionProperties": {"range": {"sheetId": cover.id, "dimension": "COLUMNS", "startIndex": 5, "endIndex": 6}, "properties": {"pixelSize": 80},  "fields": "pixelSize"}},
        ] + colour_requests})
    except Exception:
        pass


def _build_tab_meta(tab_info: list, overview_df) -> dict:
    """
    Match each tab to a row in the Issues Overview to get Type, Priority, and %.
    Returns dict of tab_name → {type, priority, pct}.
    """
    if overview_df is None:
        return {}

    col_map  = {c.lower().strip(): c for c in overview_df.columns}
    name_col = col_map.get("issue name")
    type_col = col_map.get("issue type")
    pri_col  = col_map.get("issue priority")
    pct_col  = col_map.get("% of total")

    if not name_col:
        return {}

    tab_names = [t[0] for t in tab_info]
    result    = {}

    for _, row in overview_df.iterrows():
        issue_name = str(row.get(name_col, ""))
        match      = _fuzzy_match(issue_name, tab_names)
        if match:
            result[match] = {
                "type":     str(row.get(type_col, "")) if type_col else "",
                "priority": str(row.get(pri_col,  "")) if pri_col  else "",
                "pct":      str(row.get(pct_col,  "")) if pct_col  else "",
            }

    return result


def _fuzzy_match(issue_name: str, tab_names: list) -> str | None:
    """Find the best matching tab name for an SF issue name using word overlap."""
    def words(s):
        s = s.lower()
        s = re.sub(r"[^a-z0-9]", " ", s)
        return set(w for w in s.split() if len(w) > 1)

    issue_words = words(issue_name)
    best, best_score = None, 1  # require at least 2 matching words

    for tab in tab_names:
        score = len(issue_words & words(tab))
        if score > best_score:
            best_score = score
            best = tab

    return best


# ── Tab formatting ────────────────────────────────────────────────────────────

def _format_header(sh: gspread.Spreadsheet, ws: gspread.Worksheet, num_cols: int):
    try:
        sh.batch_update({"requests": [
            {"repeatCell": {
                "range": {"sheetId": ws.id, "startRowIndex": 0, "endRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": num_cols},
                "cell": {"userEnteredFormat": {"backgroundColor": DARK_GREEN, "textFormat": {"foregroundColor": WHITE, "bold": True}}},
                "fields": "userEnteredFormat(backgroundColor,textFormat)",
            }},
            {"updateSheetProperties": {
                "properties": {"sheetId": ws.id, "gridProperties": {"frozenRowCount": 1}},
                "fields": "gridProperties.frozenRowCount",
            }},
        ]})
    except Exception:
        pass
    time.sleep(0.3)


# ── Data helpers ──────────────────────────────────────────────────────────────

def _df_to_rows(df: pd.DataFrame) -> list:
    rows = []
    for _, row in df.iterrows():
        clean = []
        for v in row.tolist():
            if v is None or (isinstance(v, float) and pd.isna(v)):
                clean.append("")
            else:
                s = str(v)
                clean.append(s[:MAX_CELL_LEN] + "…" if len(s) > MAX_CELL_LEN else s)
        rows.append(clean)
    return rows


def _col_letter(n: int) -> str:
    result = ""
    while n:
        n, r = divmod(n - 1, 26)
        result = chr(65 + r) + result
    return result
