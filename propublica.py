"""ProPublica Nonprofit Explorer API client (free, no API key required).

Docs: https://projects.propublica.org/nonprofits/api
Used for: org search, org profiles + financial history, and discovering the
IRS e-file "object IDs" that let us fetch the raw 990 XML for grant details.
"""
import re
import requests

import db

BASE = "https://projects.propublica.org/nonprofits/api/v2"
HEADERS = {"User-Agent": "EyeSpyGrantScout/1.0 (nonprofit grant research tool)"}

# NTEE major categories per the ProPublica API
NTEE_CATEGORIES = {
    1: "Arts, Culture & Humanities",
    2: "Education",
    3: "Environment and Animals",
    4: "Health",
    5: "Human Services",
    6: "International, Foreign Affairs",
    7: "Public, Societal Benefit",
    8: "Religion Related",
    9: "Mutual/Membership Benefit",
    10: "Unknown, Unclassified",
}

US_STATES = ["AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL", "GA", "HI", "ID", "IL",
             "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE",
             "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD",
             "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY"]

FORM_TYPES = {0: "990", 1: "990-EZ", 2: "990-PF"}


def search(q="", state=None, ntee=None, c_code=None, page=0):
    """Search organizations. Returns the raw API response dict."""
    params = {"q": q, "page": page}
    if state:
        params["state[id]"] = state
    if ntee:
        params["ntee[id]"] = ntee
    if c_code:
        params["c_code[id]"] = c_code
    r = requests.get(f"{BASE}/search.json", params=params, headers=HEADERS, timeout=30)
    if r.status_code == 404:  # API returns 404 for no results on some queries
        return {"total_results": 0, "organizations": [], "num_pages": 0, "cur_page": 0}
    r.raise_for_status()
    return r.json()


def get_org(ein, use_cache=True):
    """Org profile + filings. Cached locally for 24h to be polite to the API."""
    ein = str(ein).replace("-", "")
    if use_cache:
        cached = db.get_cached_org(ein)
        if cached:
            return cached
    r = requests.get(f"{BASE}/organizations/{ein}.json", headers=HEADERS, timeout=30)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    data = r.json()
    db.cache_org(ein, data)
    return data


def discover_object_ids(ein, use_cache=True):
    """Scrape e-file XML object IDs from the ProPublica org page.

    The JSON API doesn't expose them, but the org page links each filing's
    raw XML by object ID. The XML itself is then fetched from the public
    GivingTuesday 990 data lake on S3 (see xml990.py).
    """
    ein = str(ein).replace("-", "")
    if use_cache:
        known = db.get_object_ids(ein)
        if known:
            return known
    url = f"https://projects.propublica.org/nonprofits/organizations/{ein}"
    ua = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
    r = requests.get(url, headers=ua, timeout=30)
    if r.status_code != 200:
        return db.get_object_ids(ein)
    oids = sorted(set(re.findall(r"object_id=(\d{18})", r.text)), reverse=True)
    if oids:
        db.save_object_ids(ein, oids)
    return oids


def financial_history(org_data):
    """Condense filings_with_data into a simple list for display/charting."""
    out = []
    for f in org_data.get("filings_with_data", []):
        form = FORM_TYPES.get(f.get("formtype"), "990")
        expenses = f.get("totfuncexpns") or f.get("totexpnsexempt") or f.get("totexpnspbks")
        out.append({
            "year": f.get("tax_prd_yr"),
            "form": form,
            "revenue": f.get("totrevenue"),
            "expenses": expenses,
            "assets": f.get("totassetsend"),
            "grants_paid": f.get("qlfydistribtot") if form == "990-PF" else None,
            "pdf_url": f.get("pdf_url"),
        })
    out.sort(key=lambda x: x["year"] or 0, reverse=True)
    return out
