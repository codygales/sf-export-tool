import re
from io import StringIO

import pandas as pd
import streamlit as st

from sheets import create_sheet

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="SF → Google Sheets | UKL",
    page_icon="🕷️",
    layout="centered",
)


# ── Auth gate ─────────────────────────────────────────────────────────────────
def login_screen():
    st.title("🕷️ SF Issues Exporter")
    st.caption("UKLinkology internal tool")
    with st.form("login_form"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Log in", use_container_width=True)
    if submitted:
        valid_user = st.secrets["auth"]["username"]
        valid_pass = st.secrets["auth"]["password"]
        if username == valid_user and password == valid_pass:
            st.session_state.authenticated = True
            st.rerun()
        else:
            st.error("Incorrect username or password.")


if not st.session_state.get("authenticated"):
    login_screen()
    st.stop()


# ── Main app ──────────────────────────────────────────────────────────────────
st.title("🕷️ SF Issues → Google Sheets")
st.caption("Upload your Screaming Frog issue CSVs and get a structured Google Sheet instantly.")

with st.expander("ℹ️ How to export from Screaming Frog", expanded=False):
    st.markdown("""
**Export all issue tabs at once (recommended):**
1. Run your crawl in Screaming Frog
2. Go to **Reports → Bulk Export → Issues → All Issues Export**
3. Save the CSV file(s) to your computer
4. Upload them below

**Or export individual issues:**
1. In SF, open the **Issues** tab (left panel)
2. Click any issue type (e.g. *4xx Client Errors*)
3. Click **Export** (bottom right) → CSV
4. Repeat for each issue you want
5. Upload all CSVs below at once
""")

st.divider()

# ── Inputs ────────────────────────────────────────────────────────────────────
col1, col2 = st.columns(2)

with col1:
    client_name = st.text_input(
        "Client / domain name",
        placeholder="e.g. example.com",
        help="Used as the Google Sheet name. Each run creates a new sheet.",
    )

with col2:
    share_email = st.text_input(
        "Share sheet to (email)",
        value="codygalesukl@gmail.com",
        help="The finished sheet will be shared to this Google account.",
    )

uploaded_files = st.file_uploader(
    "Upload Screaming Frog CSV exports",
    type="csv",
    accept_multiple_files=True,
    help="Upload one or more CSV files exported from Screaming Frog's Issues panel.",
)

# Show preview of uploaded files
if uploaded_files:
    st.caption(f"**{len(uploaded_files)} file(s) ready:**")
    for f in uploaded_files:
        st.caption(f"— {f.name}")

run_btn = st.button("▶ Build Google Sheet", type="primary", use_container_width=True, disabled=not uploaded_files)


# ── Run ───────────────────────────────────────────────────────────────────────
if run_btn:
    errors = []
    if not client_name.strip():
        errors.append("Please enter a client or domain name.")
    if not share_email.strip():
        errors.append("Please enter an email address to share the sheet to.")
    if not uploaded_files:
        errors.append("Please upload at least one CSV file.")

    if errors:
        for e in errors:
            st.error(e)
        st.stop()

    # ── Parse uploads ─────────────────────────────────────────────────────────
    dataframes = {}
    parse_warnings = []

    for f in uploaded_files:
        try:
            df = pd.read_csv(StringIO(f.read().decode("utf-8", errors="replace")), low_memory=False)
            df = df.fillna("")

            if df.empty or len(df) == 0:
                parse_warnings.append(f"⚠️ {f.name} — empty, skipped.")
                continue

            tab_name = _filename_to_tab(f.name)
            # Deduplicate tab names
            base = tab_name
            i = 2
            while tab_name in dataframes:
                tab_name = f"{base} ({i})"
                i += 1

            dataframes[tab_name] = df

        except Exception as e:
            parse_warnings.append(f"⚠️ {f.name} — could not read: {e}")

    for w in parse_warnings:
        st.warning(w)

    if not dataframes:
        st.error("No valid CSV data found in the uploaded files.")
        st.stop()

    # ── Sheet name (client + timestamp) ───────────────────────────────────────
    from datetime import datetime
    timestamp = datetime.now().strftime("%d %b %Y")
    sheet_name = f"{client_name.strip()} — SF Issues ({timestamp})"

    # ── Export to Sheets ───────────────────────────────────────────────────────
    log = st.empty()
    messages = []

    def progress_cb(msg: str):
        messages.append(msg)
        log.info("\n\n".join(messages))

    sheet_url = None
    error_msg = None

    with st.spinner("Exporting to Google Sheets…"):
        try:
            sheet_url = create_sheet(sheet_name, dataframes, share_email.strip(), progress_cb)
        except Exception as e:
            error_msg = str(e)

    log.empty()

    if error_msg:
        st.error(f"❌ Export failed: {error_msg}")
    elif sheet_url:
        st.success(f"✅ Sheet created: **{sheet_name}**")
        st.link_button("📊 Open Google Sheet", sheet_url, use_container_width=True)

        with st.expander("📋 What was exported"):
            for name, df in dataframes.items():
                st.write(f"- **{name}** — {len(df):,} rows")

    st.divider()
    if st.button("↩ Export another"):
        st.rerun()


# ── Helpers ───────────────────────────────────────────────────────────────────
def _filename_to_tab(filename: str) -> str:
    """
    Convert an SF export filename to a readable tab name.
    e.g. 'response_codes_client_error_(4xx).csv' → '4xx Client Errors'
    """
    # Known SF filename patterns → clean names
    known = {
        "client_error": "4xx Client Errors",
        "server_error": "5xx Server Errors",
        "redirection": "3xx Redirects",
        "blocked_by_robots": "Blocked by Robots.txt",
        "page_titles_missing": "Page Titles — Missing",
        "page_titles_duplicate": "Page Titles — Duplicate",
        "page_titles_over": "Page Titles — Over 60 Chars",
        "page_titles_below": "Page Titles — Below 30 Chars",
        "page_titles_same": "Page Titles — Same as H1",
        "meta_description_missing": "Meta Description — Missing",
        "meta_description_duplicate": "Meta Description — Duplicate",
        "meta_description_over": "Meta Description — Over 155 Chars",
        "meta_description_below": "Meta Description — Below 70 Chars",
        "h1_missing": "H1 — Missing",
        "h1_duplicate": "H1 — Duplicate",
        "h1_multiple": "H1 — Multiple",
        "h1_over": "H1 — Over 70 Chars",
        "h2_missing": "H2 — Missing",
        "h2_duplicate": "H2 — Duplicate",
        "h2_multiple": "H2 — Multiple",
        "missing_alt_text": "Images — Missing Alt Text",
        "canonicals_missing": "Canonicals — Missing",
        "non_indexable_canonical": "Canonicals — Non-Indexable",
        "noindex": "Noindex Pages",
        "nofollow": "Nofollow Pages",
        "http_urls": "HTTP URLs",
        "broken": "Broken Links",
        "issues": "Issues Overview",
    }

    stem = filename.lower().replace(".csv", "")
    # Strip leading numbers/dates
    stem = re.sub(r"^\d+[-_]", "", stem)

    for key, clean_name in known.items():
        if key in stem:
            return clean_name

    # Fallback: clean up the filename itself
    name = stem.replace("_", " ").replace("-", " ")
    words = []
    for w in name.split():
        if w in {"h1", "h2", "h3", "url", "urls", "4xx", "5xx", "3xx"}:
            words.append(w.upper())
        else:
            words.append(w.capitalize())

    return " ".join(words)[:99]
