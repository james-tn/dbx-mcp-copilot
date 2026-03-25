from __future__ import annotations

from datetime import datetime, timedelta

from mcp_server import edgar_lookup


def test_edgar_lookup_returns_private_when_company_is_not_found(monkeypatch) -> None:
    monkeypatch.setattr(edgar_lookup, "find_cik_via_company_search", lambda company_name: (None, None))

    payload = edgar_lookup.edgar_lookup("Private Example LLC")

    assert payload == {
        "company": "Private Example LLC",
        "public": False,
        "reason": "No match in EDGAR - private or not a US-listed entity",
        "filings": [],
    }


def test_edgar_lookup_returns_recent_filings_for_public_company(monkeypatch) -> None:
    filing_date = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d")
    monkeypatch.setattr(
        edgar_lookup,
        "find_cik_via_company_search",
        lambda company_name: ("0000123456", "Example Corp"),
    )
    monkeypatch.setattr(
        edgar_lookup,
        "fetch_submissions",
        lambda cik: {
            "cik": "123456",
            "name": "Example Corporation",
            "filings": {
                "recent": {
                    "form": ["8-K"],
                    "filingDate": [filing_date],
                    "accessionNumber": ["0001234567-26-000001"],
                    "primaryDocument": ["doc.htm"],
                    "primaryDocDescription": ["Current report"],
                }
            },
        },
    )

    payload = edgar_lookup.edgar_lookup("Example Corp")

    assert payload["company"] == "Example Corp"
    assert payload["edgar_name"] == "Example Corporation"
    assert payload["cik"] == "0000123456"
    assert payload["public"] is True
    assert payload["filings"] == [
        {
            "form": "8-K",
            "filed": filing_date,
            "accession": "0001234567-26-000001",
            "description": "Current report",
            "url": "https://www.sec.gov/Archives/edgar/data/123456/000123456726000001/doc.htm",
        }
    ]
