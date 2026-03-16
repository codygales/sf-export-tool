import re
from datetime import datetime
from io import StringIO

import pandas as pd
import streamlit as st

from sheets import create_sheet


# ── Helpers (must be defined before use) ─────────────────────────────────────

def _extract_sheet_id(value: str) -> str | None:
    """Extract Sheet ID from a full Google Sheets URL or return as-is if already an ID."""
    import re
    # Match /d/SHEET_ID/ in a URL
    match = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", value)
    if match:
        return match.group(1)
    # If it looks like a raw ID (no spaces, alphanumeric + dashes)
    if re.match(r"^[a-zA-Z0-9_-]{20,}$", value):
        return value
    return None


def _filename_to_tab(filename: str) -> str:
    """Convert an SF export filename to a readable GSheet tab name."""

    known = {
        # Response codes — internal
        "internal_client_error": "Internal 4xx Errors",
        "internal_client_error_(4xx)_inlinks": "Internal 4xx — Inlinks",
        "internal_server_error": "Internal 5xx Errors",
        "internal_redirection_(3xx)_inlinks": "Internal 3xx — Inlinks",
        "internal_redirection": "Internal 3xx Redirects",
        "internal_redirect_chain_inlinks": "Redirect Chains — Inlinks",
        "internal_redirect_chain": "Redirect Chains",
        # Response codes — external
        "external_client_error_(4xx)_inlinks": "External 4xx — Inlinks",
        "external_client_error": "External 4xx Errors",
        "external_server_error_(5xx)_inlinks": "External 5xx — Inlinks",
        "external_server_error": "External 5xx Errors",
        "external_no_response_inlinks": "External No Response — Inlinks",
        "external_no_response": "External No Response",
        # Blocked
        "blocked_by_robots": "Blocked by Robots.txt",
        # Page titles
        "page_titles_missing": "Page Titles — Missing",
        "page_titles_duplicate": "Page Titles — Duplicate",
        "page_titles_multiple": "Page Titles — Multiple",
        "page_titles_over_561_pixels": "Page Titles — Over 561px",
        "page_titles_over_60": "Page Titles — Over 60 Chars",
        "page_titles_below_200_pixels": "Page Titles — Below 200px",
        "page_titles_below_30": "Page Titles — Below 30 Chars",
        "page_titles_same_as_h1": "Page Titles — Same as H1",
        "page_titles_outside_head": "Page Titles — Outside Head",
        # Meta description
        "meta_description_missing": "Meta Desc — Missing",
        "meta_description_duplicate": "Meta Desc — Duplicate",
        "meta_description_multiple": "Meta Desc — Multiple",
        "meta_description_over_985_pixels": "Meta Desc — Over 985px",
        "meta_description_over_155": "Meta Desc — Over 155 Chars",
        "meta_description_below_400_pixels": "Meta Desc — Below 400px",
        "meta_description_below_70": "Meta Desc — Below 70 Chars",
        "meta_description_outside_head": "Meta Desc — Outside Head",
        # H1
        "h1_missing": "H1 — Missing",
        "h1_duplicate": "H1 — Duplicate",
        "h1_multiple": "H1 — Multiple",
        "h1_nonsequential": "H1 — Non-Sequential",
        "h1_over_70": "H1 — Over 70 Chars",
        # H2
        "h2_missing": "H2 — Missing",
        "h2_duplicate": "H2 — Duplicate",
        "h2_multiple": "H2 — Multiple",
        "h2_nonsequential": "H2 — Non-Sequential",
        "h2_over_70": "H2 — Over 70 Chars",
        # Images
        "images_missing_alt_text_inlinks": "Images No Alt — Inlinks",
        "images_missing_alt_text": "Images — Missing Alt Text",
        "images_alt_text_over_100": "Images — Alt Over 100 Chars",
        "images_with_alt_text_over": "Images — Alt Over X Chars",
        # Canonicals
        "canonicals_canonicalised_inlinks": "Canonicalised — Inlinks",
        "canonicals_canonicalised": "Canonicalised URLs",
        "canonicals_missing": "Canonicals — Missing",
        "non_indexable_canonical": "Canonicals — Non-Indexable",
        # Directives
        "directives_noindex_inlinks": "Noindex — Inlinks",
        "directives_noindex": "Noindex Pages",
        "directives_nofollow": "Nofollow Pages",
        # Content
        "content_low_content_pages": "Low Content Pages",
        "content_soft_404_inlinks": "Soft 404 — Inlinks",
        "content_soft_404": "Soft 404 Pages",
        # Links
        "links_internal_outlinks_with_no_anchor_text": "Links — No Anchor Text",
        "links_nondescriptive_anchor_text": "Links — Non-Descriptive Anchor",
        "links_pages_with_high_crawl_depth": "Links — High Crawl Depth",
        "links_pages_with_high_external_outlinks": "Links — High External Outlinks",
        # Pagination
        "pagination_nonindexable": "Pagination — Non-Indexable",
        "pagination_url_not_in_anchor_tag": "Pagination — URL Not in Anchor",
        # Security
        "security_missing_contentsecuritypolicy": "Security — Missing CSP Header",
        "security_missing_hsts": "Security — Missing HSTS Header",
        "security_missing_secure_referrerpolicy": "Security — Missing Referrer Policy",
        "security_missing_xcontenttypeoptions": "Security — Missing X-Content-Type",
        "security_missing_xframeoptions": "Security — Missing X-Frame-Options",
        "security_protocolrelative": "Security — Protocol-Relative Links",
        "security_unsafe_crossorigin": "Security — Unsafe Cross-Origin Links",
        # URLs
        "url_over_115": "URLs — Over 115 Characters",
        "url_parameters": "URLs — Parameters",
        "url_underscores": "URLs — Underscores",
        "url_uppercase": "URLs — Uppercase",
        # Validation
        "validation_multiple_body": "Validation — Multiple Body Tags",
        "validation_multiple_head": "Validation — Multiple Head Tags",
        # Issues overview
        "issues_overview": "Issues Overview",
        "issues": "Issues Overview",
    }

    stem = filename.lower().replace(".csv", "").strip()
    stem = re.sub(r"^\d+[-_]", "", stem)  # strip leading numbers

    # Match longest key first to avoid partial matches
    for key in sorted(known, key=len, reverse=True):
        if key in stem:
            return known[key]

    # Fallback: clean up the raw filename
    name = stem.replace("_", " ").replace("-", " ")
    words = []
    for w in name.split():
        if w in {"h1", "h2", "h3", "url", "urls", "4xx", "5xx", "3xx", "csp", "hsts"}:
            words.append(w.upper())
        else:
            words.append(w.capitalize())
    return " ".join(words)[:99]


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

with st.expander("ℹ️ How to use this tool", expanded=False):
    st.markdown("""
### First-time setup (once per client sheet)
1. Go to [Google Sheets](https://sheets.google.com) and create a **blank spreadsheet**
2. Click **Share** and add `gsheets@gsheets-490414.iam.gserviceaccount.com` as an **Editor**
3. Copy the Sheet ID from the URL — it's the long string between `/d/` and `/edit`
   - e.g. `https://docs.google.com/spreadsheets/d/`**`1w_Hm9yU...`**`/edit`
4. Paste that ID (or the full URL) into the **Google Sheet ID or URL** field below

---

### Exporting issues from Screaming Frog
**Bulk export (recommended — gets everything at once):**
1. Run your crawl in Screaming Frog
2. Go to **Reports → Bulk Export → Issues**
3. Export each issue type you want — save all CSVs to the same folder
4. Upload them all below at once

**Or export individual issues:**
1. Open the **Issues** panel in SF (left sidebar)
2. Click an issue type → **Export** button (bottom right) → CSV
3. Repeat for each issue, then upload all CSVs below together

---

### Each time you run an export
- The sheet is **fully rebuilt** each run — old tabs are replaced with fresh data
- Use a **separate sheet per client** (create one, share it, reuse the same ID every time)
- The **client/domain name** you enter becomes the cover page title
""")

st.divider()

# ── Inputs ────────────────────────────────────────────────────────────────────
col1, col2 = st.columns(2)

with col1:
    client_name = st.text_input(
        "Client / domain name",
        placeholder="e.g. example.com",
        help="Used as the cover page title and tab labels.",
    )

with col2:
    sheet_input = st.text_input(
        "Google Sheet ID or URL",
        placeholder="Paste the Sheet ID or full URL",
        help="Create a blank sheet in your Drive, share it with the service account as editor, then paste the ID or URL here.",
    )

uploaded_files = st.file_uploader(
    "Upload Screaming Frog CSV exports",
    type="csv",
    accept_multiple_files=True,
    help="Upload one or more CSV files exported from Screaming Frog.",
)

if uploaded_files:
    st.caption(f"**{len(uploaded_files)} file(s) ready:**")
    for f in uploaded_files:
        st.caption(f"— {f.name}")

run_btn = st.button(
    "▶ Build Google Sheet",
    type="primary",
    use_container_width=True,
    disabled=not uploaded_files,
)


# ── Run ───────────────────────────────────────────────────────────────────────
if run_btn:
    errors = []
    if not client_name.strip():
        errors.append("Please enter a client or domain name.")
    if not sheet_input.strip():
        errors.append("Please paste a Google Sheet ID or URL.")

    sheet_id = _extract_sheet_id(sheet_input.strip())
    if sheet_input.strip() and not sheet_id:
        errors.append("Could not read a Sheet ID from that input. Paste the full URL or just the ID from the URL.")

    if errors:
        for e in errors:
            st.error(e)
        st.stop()

    # Parse uploads
    dataframes = {}
    tab_stems  = {}   # tab_name → original filename stem (used for meta matching)
    parse_warnings = []

    for f in uploaded_files:
        try:
            df = pd.read_csv(StringIO(f.read().decode("utf-8", errors="replace")), low_memory=False)
            df = df.fillna("")

            if df.empty or len(df) == 0:
                parse_warnings.append(f"{f.name} — empty, skipped.")
                continue

            # Keep the raw stem so sheets.py can match it against Issues Overview names
            raw_stem = f.name.lower().replace(".csv", "").strip()
            raw_stem = re.sub(r"^\d+[-_]", "", raw_stem)

            tab_name = _filename_to_tab(f.name)

            # Deduplicate tab names
            base = tab_name
            i = 2
            while tab_name in dataframes:
                tab_name = f"{base} ({i})"
                i += 1

            dataframes[tab_name] = df
            tab_stems[tab_name]  = raw_stem

        except Exception as e:
            parse_warnings.append(f"{f.name} — could not read: {e}")

    if parse_warnings:
        with st.expander(f"⚠️ {len(parse_warnings)} file(s) skipped"):
            for w in parse_warnings:
                st.caption(w)

    if not dataframes:
        st.error("No valid CSV data found in the uploaded files.")
        st.stop()

    sheet_name = client_name.strip()

    log = st.empty()
    messages = []

    def progress_cb(msg: str):
        messages.append(msg)
        log.info("\n\n".join(messages))

    sheet_url = None
    error_msg = None

    with st.spinner("Exporting to Google Sheets…"):
        try:
            sheet_url = create_sheet(sheet_name, dataframes, sheet_id, progress_cb, tab_stems=tab_stems)
        except Exception as e:
            error_msg = str(e)

    log.empty()

    if error_msg:
        st.error(f"Export failed: {error_msg}")
    elif sheet_url:
        st.success(f"Sheet created: **{sheet_name}**")
        st.link_button("📊 Open Google Sheet", sheet_url, use_container_width=True)

        with st.expander(f"📋 {len(dataframes)} tabs exported"):
            for name, df in dataframes.items():
                st.write(f"- **{name}** — {len(df):,} rows")

    st.divider()
    if st.button("↩ Export another"):
        st.rerun()
