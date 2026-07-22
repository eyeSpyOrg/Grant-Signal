"""Grants.gov Search2 API client (free, public, no API key required).

Docs: https://grants.gov/api/api-guide
Used for: searching *open* federal funding opportunities (forecasted/posted
RFPs with real application deadlines) — this is data our 990-based Grants
Database can't see, since award history doesn't tell you what's currently
accepting applications.
"""
import requests

BASE = "https://api.grants.gov/v1/api/search2"
HEADERS = {"User-Agent": "EyeSpyGrantScout/1.0 (nonprofit grant research tool)",
           "Content-Type": "application/json"}

# Grants.gov "fundingCategories" codes relevant to a vision/disability/human-services nonprofit.
FUNDING_CATEGORIES = {
    "HL": "Health",
    "ED": "Education",
    "HU": "Humanities",
    "IS": "Income Security and Social Services",
    "DPR": "Disability Programs and Rehabilitation",
    "ISS": "Information and Statistics",
    "CD": "Community Development",
    "ENV": "Environment",
    "O": "Other",
}

OPP_STATUS_DEFAULT = "forecasted|posted"


def search(keyword="", opp_statuses=OPP_STATUS_DEFAULT, funding_categories=None,
           agencies=None, rows=25, start=0):
    """Search federal grant opportunities. Returns a simplified list of dicts.

    keyword: free text (e.g. "blind", "vision impairment", "assistive technology")
    opp_statuses: "forecasted|posted" (open/upcoming) or "closed|archived"
    funding_categories: list of codes from FUNDING_CATEGORIES, e.g. ["HL","DPR"]
    agencies: list of agency codes, e.g. ["HHS","ED"]
    """
    payload = {
        "keyword": keyword,
        "oppStatuses": opp_statuses,
        "rows": rows,
        "startRecordNum": start,
    }
    if funding_categories:
        payload["fundingCategories"] = "|".join(funding_categories)
    if agencies:
        payload["agencies"] = "|".join(agencies)

    r = requests.post(BASE, json=payload, headers=HEADERS, timeout=30)
    r.raise_for_status()
    data = r.json()

    hits = (data.get("data") or {}).get("oppHits") or []
    total = (data.get("data") or {}).get("hitCount", 0)
    results = [{
        "id": h.get("id"),
        "number": h.get("number"),
        "title": h.get("title"),
        "agency": h.get("agency"),
        "agency_code": h.get("agencyCode"),
        "open_date": h.get("openDate"),
        "close_date": h.get("closeDate"),
        "status": h.get("oppStatus"),
        "doc_type": h.get("docType"),
        "cfda_list": h.get("cfdaList") or [],
        "url": f"https://www.grants.gov/search-results-detail/{h.get('id')}" if h.get("id") else None,
    } for h in hits]

    return {"total": total, "results": results}
