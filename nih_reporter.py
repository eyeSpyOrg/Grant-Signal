"""NIH RePORTER API client (free, public, no API key required).

Docs: https://api.reporter.nih.gov/
Used for: searching *awarded* NIH/HHS research grants by keyword. This
doesn't surface open RFPs (NIH funding opportunities live on grants.gov
instead — see grants_gov.py) — it's for finding which NIH institutes and
program officers have already funded work in a given area (e.g. vision
research, assistive technology), so a prospect list of relevant funders
and PIs can be built even without a research arm of one's own.
"""
import requests

BASE = "https://api.reporter.nih.gov/v2/projects/search"
HEADERS = {"User-Agent": "EyeSpyGrantScout/1.0 (nonprofit grant research tool)",
           "Content-Type": "application/json"}

INCLUDE_FIELDS = [
    "ApplId", "ProjectTitle", "AbstractText", "Organization", "AwardAmount", "ProjectNum",
    "ContactPiName", "FiscalYear", "ProjectStartDate", "ProjectEndDate", "AgencyIcAdmin",
]


def search(keyword="", fiscal_years=None, limit=25, offset=0):
    """Search awarded NIH projects by keyword. Returns a simplified list of dicts."""
    criteria = {
        "advanced_text_search": {
            # Title-only on purpose: "terms" and "abstracttext" match against NIH's
            # broad RCDC term ontology and full abstracts, which turns a query like
            # "blindness" into 20,000+ hits (e.g. any "double-blind trial" abstract).
            # Title search stays precise for genuinely on-topic projects.
            "operator": "and",
            "search_field": "projecttitle",
            "search_text": keyword,
        }
    }
    if fiscal_years:
        criteria["fiscal_years"] = fiscal_years

    payload = {
        "criteria": criteria,
        "include_fields": INCLUDE_FIELDS,
        "offset": offset,
        "limit": limit,
    }
    r = requests.post(BASE, json=payload, headers=HEADERS, timeout=30)
    r.raise_for_status()
    data = r.json()

    total = (data.get("meta") or {}).get("total", 0)
    results = []
    for p in data.get("results", []):
        ic = p.get("agency_ic_admin") or {}
        org = p.get("organization") or {}
        abstract = p.get("abstract_text") or ""
        results.append({
            "project_num": p.get("project_num"),
            "title": p.get("project_title"),
            "org_name": org.get("org_name"),
            "pi_name": p.get("contact_pi_name"),
            "agency": ic.get("name"),
            "agency_abbr": ic.get("abbreviation"),
            "award_amount": p.get("award_amount"),
            "fiscal_year": p.get("fiscal_year"),
            "start_date": p.get("project_start_date"),
            "end_date": p.get("project_end_date"),
            "abstract_snippet": (abstract[:400] + "…") if len(abstract) > 400 else abstract,
            "url": f"https://reporter.nih.gov/project-details/{p.get('appl_id')}" if p.get("appl_id") else None,
        })

    return {"total": total, "results": results}
