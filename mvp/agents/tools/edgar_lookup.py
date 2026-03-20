"""
Account Pulse — Layer 3: SEC EDGAR API Lookup
Searches EDGAR for a company by name, retrieves the 5 most recent filings
within the last 90 days. Rate-limited to 5 req/sec per spec.
"""

import sys
import io
import json
import time
import re
import urllib.request
import urllib.parse
from datetime import datetime, timedelta

HEADERS = {
    "User-Agent": "VeeamRevenueIntelligence/1.0 (contact: revops@veeam.com)",
    "Accept": "application/json",
}
LOOKBACK_DAYS = 90
MAX_FILINGS = 5
FILING_TYPES = {"10-K", "10-Q", "8-K"}

# ---------------------------------------------------------------------------
# Fix 1: Known CIK cache
# Maps normalized company names (lowercase, no punctuation) to their EDGAR CIK.
# Prevents wasted API calls for companies whose registered EDGAR name differs
# from what appears in the territory export (e.g. "Ford Motor Company" vs
# "FORD MOTOR CO").  Add entries here as new accounts are onboarded.
# ---------------------------------------------------------------------------
_KNOWN_CIKS: dict[str, str] = {
    "ford motor company":          "0000037996",
    "ford motor co":               "0000037996",
    "general motors company":      "0001467858",
    "general motors co":           "0001467858",
    "general motors corp":         "0001467858",
    "steel dynamics inc":          "0001022671",
    "steel dynamics":              "0001022671",
    "cintas corporation":          "0000723254",
    "cintas corp":                 "0000723254",
    "masco corporation":           "0000062996",
    "masco corp":                  "0000062996",
    "thor industries inc":         "0000098472",
    "thor industries":             "0000098472",
    "owens corning":               "0001370946",
    "owens-illinois inc":          "0000075473",
    "owens illinois inc":          "0000075473",
    "cincinnati financial":        "0000020286",
    "cincinnati financial corp":   "0000020286",
    "berkshire hathaway inc":      "0001067983",
    "berkshire hathaway":          "0001067983",
    "booking holdings inc":        "0001075531",
    "booking holdings":            "0001075531",
    "bridgestone corporation":     "0000927355",
    "bridgestone corp":            "0000927355",
    "dayton freight lines inc":    "0000026890",
    "dayton freight lines":        "0000026890",
    "j sainsbury plc":             "0000086312",
    "sainsbury plc":               "0000086312",
    "compass group plc":           "0001056943",
    "compass group":               "0001056943",
    "legal general group plc":     "0000060435",
    "experian plc":                "0001372514",
    "airbus se":                   None,   # EU-listed only, no EDGAR
    "cambia health solutions inc":  None,  # private
    "the auto club group":         None,   # private
}

# ---------------------------------------------------------------------------
# Fix 2: Suffix-stripping normalization
# Used both for cache lookups and as a fallback search term when the full
# company name returns no EDGAR match.
# ---------------------------------------------------------------------------
_SUFFIX_PATTERN = re.compile(
    r"[,\s]+(inc\.?|corp\.?|corporation|co\.?|company|llc|l\.l\.c\.|"
    r"ltd\.?|plc|l\.p\.?|lp|llp|group|holdings|associates|enterprises|"
    r"international|worldwide|technologies|solutions|services|systems)\.?$",
    re.IGNORECASE,
)

def _normalize(name: str) -> str:
    """Lowercase, strip punctuation suffixes, collapse whitespace."""
    name = name.strip()
    # Strip up to two trailing legal suffixes (e.g. "Holdings, Inc.")
    for _ in range(2):
        name = _SUFFIX_PATTERN.sub("", name).strip().rstrip(",").strip()
    return name.lower()

def _cache_lookup(company_name: str) -> tuple[str | None, bool]:
    """
    Check the known-CIK cache. Returns (cik, found).
    cik may be None for companies explicitly marked as non-EDGAR.
    """
    key = _normalize(company_name)
    if key in _KNOWN_CIKS:
        return _KNOWN_CIKS[key], True
    # Also try the raw lowercase name in case it matches exactly
    if company_name.lower() in _KNOWN_CIKS:
        return _KNOWN_CIKS[company_name.lower()], True
    return None, False

# ---------------------------------------------------------------------------
# HTTP helper (rate-limited)
# ---------------------------------------------------------------------------
_last_request_time = 0.0

def get_json(url: str, debug: bool = False) -> dict | list | None:
    global _last_request_time
    elapsed = time.time() - _last_request_time
    if elapsed < 0.2:
        time.sleep(0.2 - elapsed)
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=10) as resp:
            _last_request_time = time.time()
            raw = resp.read().decode()
            if debug:
                print(f"  [RAW first 2000 chars]: {raw[:2000]}", file=sys.stderr)
            return json.loads(raw)
    except Exception as e:
        print(f"  [HTTP error] {url}: {e}", file=sys.stderr)
        return None

def get_text(url: str) -> str | None:
    global _last_request_time
    elapsed = time.time() - _last_request_time
    if elapsed < 0.2:
        time.sleep(0.2 - elapsed)
    try:
        req = urllib.request.Request(url, headers={**HEADERS, "Accept": "application/atom+xml,text/html"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            _last_request_time = time.time()
            return resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"  [HTTP error] {url}: {e}", file=sys.stderr)
        return None

# ---------------------------------------------------------------------------
# Step 1: Find CIK via EDGAR company search (Atom/XML endpoint)
# Returns (cik, entity_name) or (None, None)
# ---------------------------------------------------------------------------
def _edgar_atom_search(search_term: str) -> tuple[str | None, str | None]:
    """Single EDGAR company name search. Returns (cik, edgar_name) or (None, None)."""
    encoded = urllib.parse.quote(search_term)
    url = (
        f"https://www.sec.gov/cgi-bin/browse-edgar"
        f"?company={encoded}&CIK=&type=10-K&dateb=&owner=include"
        f"&count=5&search_text=&action=getcompany&output=atom"
    )
    print(f"  EDGAR search: {search_term!r}", file=sys.stderr)
    text = get_text(url)
    if not text:
        return None, None

    cik_matches  = re.findall(r'company:cik:(\d+)', text)
    name_matches = re.findall(r'<company-name>(.*?)</company-name>', text)

    if not cik_matches:
        cik_matches = re.findall(r'CIK=(\d{10})', text)

    if cik_matches:
        cik  = cik_matches[0].zfill(10)
        name = name_matches[0] if name_matches else search_term
        return cik, name

    return None, None


def find_cik_via_company_search(company_name: str) -> tuple[str | None, str | None]:
    """
    Find a company's EDGAR CIK.

    Search order:
      1. Known-CIK cache (instant, no API call)
      2. Full name search against EDGAR Atom endpoint
      3. Suffix-stripped name search (e.g. "Ford Motor" instead of "Ford Motor Company")
    """
    # --- Fix 1: Check cache first ---
    cached_cik, in_cache = _cache_lookup(company_name)
    if in_cache:
        if cached_cik is None:
            # Explicitly non-EDGAR company (EU/UK-listed, private)
            print(f"  [cache] {company_name!r} — non-EDGAR (EU/private)", file=sys.stderr)
            return None, None
        print(f"  [cache] {company_name!r} -> CIK {cached_cik}", file=sys.stderr)
        return cached_cik, company_name

    # --- Fix 2a: Full name search ---
    cik, name = _edgar_atom_search(company_name)
    if cik:
        return cik, name

    # --- Fix 2b: Suffix-stripped fallback ---
    stripped = _normalize(company_name).title()  # e.g. "Ford Motor"
    if stripped.lower() != company_name.lower():
        print(f"  Retrying with stripped name: {stripped!r}", file=sys.stderr)
        cik, name = _edgar_atom_search(stripped)
        if cik:
            return cik, name

    return None, None

# ---------------------------------------------------------------------------
# Step 2: Fetch submissions for a CIK
# ---------------------------------------------------------------------------
def fetch_submissions(cik: str) -> dict | None:
    cik_padded = str(cik).lstrip("0").zfill(10)
    url = f"https://data.sec.gov/submissions/CIK{cik_padded}.json"
    print(f"  Submissions URL: {url}", file=sys.stderr)
    return get_json(url)

# ---------------------------------------------------------------------------
# Step 3: Filter to recent target filings
# ---------------------------------------------------------------------------
def get_recent_filings(submissions: dict, lookback_days: int = LOOKBACK_DAYS, max_count: int = MAX_FILINGS) -> list[dict]:
    cutoff = datetime.now() - timedelta(days=lookback_days)
    recent = submissions.get("filings", {}).get("recent", {})

    form_types   = recent.get("form", [])
    filed_dates  = recent.get("filingDate", [])
    accession_ns = recent.get("accessionNumber", [])
    primary_docs = recent.get("primaryDocument", [])
    descriptions = recent.get("primaryDocDescription", [])

    results = []
    cik_num = submissions.get("cik", "")

    for form, date_str, accession, doc, desc in zip(
        form_types, filed_dates, accession_ns, primary_docs, descriptions
    ):
        if form not in FILING_TYPES:
            continue
        try:
            filed = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            continue
        if filed < cutoff:
            continue
        acc_clean = accession.replace("-", "")
        filing_url = f"https://www.sec.gov/Archives/edgar/data/{cik_num}/{acc_clean}/{doc}"
        results.append({
            "form":        form,
            "filed":       date_str,
            "accession":   accession,
            "description": desc or "",
            "url":         filing_url,
        })
        if len(results) >= max_count:
            break
    return results

# ---------------------------------------------------------------------------
# Main lookup function
# ---------------------------------------------------------------------------
def edgar_lookup(company_name: str) -> dict:
    print(f"  [EDGAR] {company_name}", file=sys.stderr)

    # Find CIK
    cik, edgar_entity_name = find_cik_via_company_search(company_name)

    if not cik:
        print(f"  [EDGAR] No CIK — treating as private", file=sys.stderr)
        return {
            "company": company_name,
            "public":  False,
            "reason":  "No match in EDGAR — private or not a US-listed entity",
            "filings": [],
        }

    print(f"  [EDGAR] CIK: {cik} | {edgar_entity_name}", file=sys.stderr)

    # Fetch submissions
    submissions = fetch_submissions(cik)
    if not submissions:
        return {
            "company": company_name,
            "public":  False,
            "reason":  "EDGAR API error fetching submissions",
            "filings": [],
        }

    entity_name = submissions.get("name", edgar_entity_name)

    # Get recent filings
    filings = get_recent_filings(submissions)
    print(f"  [EDGAR] {len(filings)} filings (last {LOOKBACK_DAYS} days)", file=sys.stderr)

    return {
        "company":    company_name,
        "edgar_name": entity_name,
        "cik":        cik,
        "public":     True,
        "filings":    filings,
    }

# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------
def display_result(result: dict):
    print(f"\n{'='*60}")
    print(f"Company:    {result['company']}")
    if result.get("edgar_name"):
        print(f"EDGAR name: {result['edgar_name']}")
    if result.get("cik"):
        print(f"CIK:        {result['cik']}")
    print(f"Public:     {'YES' if result['public'] else 'NO (private or not US-listed)'}")
    if not result["public"]:
        print(f"Note:       {result.get('reason', '')}")
    else:
        filings = result["filings"]
        if not filings:
            print(f"Filings:    None found in last {LOOKBACK_DAYS} days")
        else:
            print(f"Filings (last {LOOKBACK_DAYS} days):")
            for f in filings:
                desc = f"  {f['description']}" if f["description"] else ""
                print(f"  [{f['form']}]  {f['filed']}{desc}")
                print(f"         {f['url']}")
    print("="*60)

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import io as _io
    sys.stdout = _io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    test_cases = [
        # Cache hits (no API calls)
        "Ford Motor Company",       # cache -> 0000037996
        "General Motors Company",   # cache -> 0001467858
        "Cintas Corporation",       # cache -> 0000723254
        "Masco Corporation",        # cache -> 0000062996
        "Steel Dynamics Inc",       # cache -> 0001022671
        "Airbus SE",                # cache -> None (EU-listed)
        # Suffix-strip fallback
        "Berkshire Hathaway Inc",   # full name works already
        # Private / non-EDGAR
        "RSM UK GROUP LLP",
    ]

    for company in test_cases:
        result = edgar_lookup(company)
        display_result(result)
        print()
