"""SEC EDGAR lookup logic hosted behind the MCP boundary."""

from __future__ import annotations

import json
import logging
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "VeeamRevenueIntelligence/1.0 (contact: revops@veeam.com)",
    "Accept": "application/json",
}
LOOKBACK_DAYS = 90
MAX_FILINGS = 5
FILING_TYPES = {"10-K", "10-Q", "8-K"}

_KNOWN_CIKS: dict[str, str | None] = {
    "ford motor company": "0000037996",
    "ford motor co": "0000037996",
    "general motors company": "0001467858",
    "general motors co": "0001467858",
    "general motors corp": "0001467858",
    "steel dynamics inc": "0001022671",
    "steel dynamics": "0001022671",
    "cintas corporation": "0000723254",
    "cintas corp": "0000723254",
    "masco corporation": "0000062996",
    "masco corp": "0000062996",
    "thor industries inc": "0000098472",
    "thor industries": "0000098472",
    "owens corning": "0001370946",
    "owens-illinois inc": "0000075473",
    "owens illinois inc": "0000075473",
    "cincinnati financial": "0000020286",
    "cincinnati financial corp": "0000020286",
    "berkshire hathaway inc": "0001067983",
    "berkshire hathaway": "0001067983",
    "booking holdings inc": "0001075531",
    "booking holdings": "0001075531",
    "bridgestone corporation": "0000927355",
    "bridgestone corp": "0000927355",
    "dayton freight lines inc": "0000026890",
    "dayton freight lines": "0000026890",
    "j sainsbury plc": "0000086312",
    "sainsbury plc": "0000086312",
    "compass group plc": "0001056943",
    "compass group": "0001056943",
    "legal general group plc": "0000060435",
    "experian plc": "0001372514",
    "airbus se": None,
    "cambia health solutions inc": None,
    "the auto club group": None,
}

_SUFFIX_PATTERN = re.compile(
    r"[,\s]+(inc\.?|corp\.?|corporation|co\.?|company|llc|l\.l\.c\.|"
    r"ltd\.?|plc|l\.p\.?|lp|llp|group|holdings|associates|enterprises|"
    r"international|worldwide|technologies|solutions|services|systems)\.?$",
    re.IGNORECASE,
)

_last_request_time = 0.0


def _normalize(name: str) -> str:
    value = name.strip()
    for _ in range(2):
        value = _SUFFIX_PATTERN.sub("", value).strip().rstrip(",").strip()
    return value.lower()


def _cache_lookup(company_name: str) -> tuple[str | None, bool]:
    key = _normalize(company_name)
    if key in _KNOWN_CIKS:
        return _KNOWN_CIKS[key], True
    raw_key = company_name.lower()
    if raw_key in _KNOWN_CIKS:
        return _KNOWN_CIKS[raw_key], True
    return None, False


def _rate_limit() -> None:
    global _last_request_time
    elapsed = time.time() - _last_request_time
    if elapsed < 0.2:
        time.sleep(0.2 - elapsed)


def get_json(url: str) -> dict[str, Any] | list[Any] | None:
    global _last_request_time
    _rate_limit()
    try:
        request = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(request, timeout=10) as response:
            _last_request_time = time.time()
            return json.loads(response.read().decode())
    except Exception:
        logger.exception("EDGAR JSON request failed for %s", url)
        return None


def get_text(url: str) -> str | None:
    global _last_request_time
    _rate_limit()
    try:
        request = urllib.request.Request(
            url,
            headers={**HEADERS, "Accept": "application/atom+xml,text/html"},
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            _last_request_time = time.time()
            return response.read().decode("utf-8", errors="replace")
    except Exception:
        logger.exception("EDGAR text request failed for %s", url)
        return None


def _edgar_atom_search(search_term: str) -> tuple[str | None, str | None]:
    encoded = urllib.parse.quote(search_term)
    url = (
        "https://www.sec.gov/cgi-bin/browse-edgar"
        f"?company={encoded}&CIK=&type=10-K&dateb=&owner=include"
        "&count=5&search_text=&action=getcompany&output=atom"
    )
    text = get_text(url)
    if not text:
        return None, None

    cik_matches = re.findall(r"company:cik:(\d+)", text)
    name_matches = re.findall(r"<company-name>(.*?)</company-name>", text)
    if not cik_matches:
        cik_matches = re.findall(r"CIK=(\d{10})", text)
    if not cik_matches:
        return None, None

    cik = cik_matches[0].zfill(10)
    entity_name = name_matches[0] if name_matches else search_term
    return cik, entity_name


def find_cik_via_company_search(company_name: str) -> tuple[str | None, str | None]:
    cached_cik, in_cache = _cache_lookup(company_name)
    if in_cache:
        if cached_cik is None:
            logger.info("EDGAR cache marks %s as non-EDGAR.", company_name)
            return None, None
        return cached_cik, company_name

    cik, entity_name = _edgar_atom_search(company_name)
    if cik:
        return cik, entity_name

    stripped = _normalize(company_name).title()
    if stripped.lower() != company_name.lower():
        return _edgar_atom_search(stripped)
    return None, None


def fetch_submissions(cik: str) -> dict[str, Any] | None:
    cik_padded = str(cik).lstrip("0").zfill(10)
    url = f"https://data.sec.gov/submissions/CIK{cik_padded}.json"
    payload = get_json(url)
    return payload if isinstance(payload, dict) else None


def get_recent_filings(
    submissions: dict[str, Any],
    *,
    lookback_days: int = LOOKBACK_DAYS,
    max_count: int = MAX_FILINGS,
) -> list[dict[str, str]]:
    cutoff = datetime.now() - timedelta(days=lookback_days)
    recent = submissions.get("filings", {}).get("recent", {})

    form_types = recent.get("form", [])
    filed_dates = recent.get("filingDate", [])
    accession_numbers = recent.get("accessionNumber", [])
    primary_docs = recent.get("primaryDocument", [])
    descriptions = recent.get("primaryDocDescription", [])

    results: list[dict[str, str]] = []
    cik_num = str(submissions.get("cik", ""))
    for form, date_str, accession, document, description in zip(
        form_types,
        filed_dates,
        accession_numbers,
        primary_docs,
        descriptions,
    ):
        if form not in FILING_TYPES:
            continue
        try:
            filed = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            continue
        if filed < cutoff:
            continue

        accession_clean = accession.replace("-", "")
        filing_url = f"https://www.sec.gov/Archives/edgar/data/{cik_num}/{accession_clean}/{document}"
        results.append(
            {
                "form": form,
                "filed": date_str,
                "accession": accession,
                "description": description or "",
                "url": filing_url,
            }
        )
        if len(results) >= max_count:
            break
    return results


def edgar_lookup(company_name: str) -> dict[str, Any]:
    logger.info("Running EDGAR lookup for %s", company_name)
    cik, edgar_entity_name = find_cik_via_company_search(company_name)
    if not cik:
        return {
            "company": company_name,
            "public": False,
            "reason": "No match in EDGAR - private or not a US-listed entity",
            "filings": [],
        }

    submissions = fetch_submissions(cik)
    if not submissions:
        return {
            "company": company_name,
            "public": False,
            "reason": "EDGAR API error fetching submissions",
            "filings": [],
        }

    entity_name = str(submissions.get("name") or edgar_entity_name or company_name)
    return {
        "company": company_name,
        "edgar_name": entity_name,
        "cik": cik,
        "public": True,
        "filings": get_recent_filings(submissions),
    }
