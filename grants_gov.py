"""Grants.gov Search2 API client (free, public, no API key required).

Docs: https://grants.gov/api/api-guide
Used for: searching federal funding opportunities (forecasted/posted RFPs with
real application deadlines) — this is data our 990-based Grants Database can't
see, since award history doesn't tell you what's currently accepting
applications. Closed/archived opportunities are searchable too, which is how you
learn when a ~5-year-cycle program is due to reopen.
"""
import re

import requests

BASE = "https://api.grants.gov/v1/api/search2"
FETCH_URL = "https://api.grants.gov/v1/api/fetchOpportunity"
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

# Agencies that actually fund blind/low-vision and disability work. Browsing by
# agency is far higher-signal than keyword search: HHS-ACL alone returns almost
# entirely relevant results (NIDILRR, Centers for Independent Living, ABLE), where
# the keyword "blind" mostly returns double-blind clinical trials.
AGENCIES = {
    "HHS-ACL": "HHS — Administration for Community Living (NIDILRR, CILs, ABLE)",
    "ED": "Dept. of Education (OSERS / OSEP / RSA)",
    "HHS-NIH11": "HHS — National Institutes of Health (incl. NEI)",
    "NSF": "National Science Foundation",
    "IMLS": "Institute of Museum and Library Services",
    "HHS-CDC": "HHS — Centers for Disease Control and Prevention",
    "HHS-HRSA": "HHS — Health Resources and Services Administration",
    "USDOL": "Dept. of Labor (ODEP / employment)",
}

# Terms that identify a genuinely BVI-relevant opportunity *in a title*. Used both
# for the curated preset search and for title-scoped filtering.
BVI_TITLE_TERMS = [
    "blind", "blindness", "low vision", "visual impairment", "visually impaired",
    "vision loss", "vision rehabilitation", "eye", "ocular", "retina", "braille",
    "assistive technology", "accessible", "accessibility", "rehabilitation",
    "independent living", "disability", "disabilities",
]

# Words that look like a match but never are, in this domain. "double-blind" is
# the single biggest source of false positives in federal grant text.
TITLE_NOISE = ["double blind", "double-blind", "single blind", "single-blind",
               "blind spot", "blinded"]

OPP_STATUS_DEFAULT = "forecasted|posted"
OPP_STATUS_PAST = "closed|archived"
OPP_STATUS_ALL = "forecasted|posted|closed|archived"


def _matches_title(title, keyword):
    """True if `keyword` (or any of its words) appears in the opportunity title.

    Grants.gov matches a keyword against the *full* opportunity text, so "blind"
    returns clinical-trial methodology NOFOs and "assistive technology" returns
    semiconductor research. nih_reporter.py already solved this by restricting to
    the project title; the Search2 API has no title-scoped field, so we filter
    client-side instead.
    """
    t = (title or "").lower()
    if not t:
        return False
    for noise in TITLE_NOISE:
        t = t.replace(noise, " ")
    k = (keyword or "").strip().lower()
    if not k:
        return True
    if k in t:
        return True
    # Multi-word keywords: accept a title that contains every word.
    words = [w for w in re.split(r"\W+", k) if len(w) > 2]
    return bool(words) and all(w in t for w in words)


def _simplify(h):
    return {
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
    }


def search(keyword="", opp_statuses=OPP_STATUS_DEFAULT, funding_categories=None,
           agencies=None, rows=25, start=0, title_only=False):
    """Search federal grant opportunities. Returns {"total", "results", "filtered"}.

    keyword: free text (e.g. "blind", "vision impairment", "assistive technology")
    opp_statuses: "forecasted|posted" (open/upcoming) or "closed|archived" (past cycles)
    funding_categories: list of codes from FUNDING_CATEGORIES, e.g. ["HL","DPR"]
    agencies: list of agency codes from AGENCIES, e.g. ["HHS-ACL","ED"]
    title_only: keep only hits whose *title* matches the keyword. Over-fetches from
        the API first, since the filtering happens on our side.
    """
    fetch_rows = min(rows * 6, 300) if (title_only and keyword) else rows
    payload = {
        "keyword": keyword,
        "oppStatuses": opp_statuses,
        "rows": fetch_rows,
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
    results = [_simplify(h) for h in hits]

    filtered = 0
    if title_only and keyword:
        kept = [x for x in results if _matches_title(x["title"], keyword)]
        filtered = len(results) - len(kept)
        results = kept

    return {"total": total, "results": results[:rows], "filtered": filtered}


def search_bvi(opp_statuses=OPP_STATUS_DEFAULT, rows=50):
    """Curated blind/low-vision sweep: the query set that actually finds this money.

    Runs the BVI title terms and de-duplicates by opportunity id. Use this instead
    of a raw keyword search as the default entry point.
    """
    seen = {}
    for term in ["blind", "low vision", "visual impairment", "vision rehabilitation",
                 "assistive technology", "independent living"]:
        try:
            out = search(keyword=term, opp_statuses=opp_statuses, rows=rows, title_only=True)
        except Exception:
            continue
        for row in out["results"]:
            if row["id"] not in seen:
                row["matched_term"] = term
                seen[row["id"]] = row
    results = list(seen.values())
    results.sort(key=lambda r: (r.get("close_date") or "9999"), reverse=False)
    return {"total": len(results), "results": results, "filtered": 0}


def _first(d, *keys):
    for k in keys:
        v = d.get(k)
        if v not in (None, "", []):
            return v
    return None


def fetch(opportunity_id):
    """Fetch one opportunity's detail record.

    Search results carry only title/dates/agency. Award ceiling & floor, estimated
    total funding and applicant eligibility — the fields that actually decide
    whether a grant is worth pursuing — exist only here. (NIDILRR DRRP awards are
    $495K–$500K and explicitly open to "public or private organizations"; a
    researcher without this fetch would wrongly skip the whole program.)
    """
    r = requests.post(FETCH_URL, json={"opportunityId": int(opportunity_id)},
                      headers=HEADERS, timeout=30)
    r.raise_for_status()
    data = (r.json() or {}).get("data") or {}
    syn = data.get("synopsis") or {}
    forecast = data.get("forecast") or {}
    detail = syn or forecast

    applicant_types = detail.get("applicantTypes") or []
    if applicant_types and isinstance(applicant_types[0], dict):
        applicant_types = [a.get("description") or a.get("id") for a in applicant_types]

    return {
        "id": data.get("id") or opportunity_id,
        "number": data.get("opportunityNumber"),
        "title": data.get("opportunityTitle"),
        "agency": _first(detail, "agencyName") or data.get("agencyName"),
        "agency_code": data.get("agencyCode"),
        "status": data.get("oppStatus") or data.get("opportunityCategory"),
        "post_date": _first(detail, "postingDate", "estimatedPostDate"),
        "close_date": _first(detail, "responseDate", "estimatedSynopsisCloseDate"),
        "close_note": _first(detail, "responseDateDesc", "estimatedSynopsisCloseDateExplanation"),
        "award_ceiling": _first(detail, "awardCeiling"),
        "award_floor": _first(detail, "awardFloor"),
        "estimated_funding": _first(detail, "estimatedFunding"),
        "expected_awards": _first(detail, "numberOfAwards", "expectedNumberOfAwards"),
        "cost_sharing": detail.get("costSharing"),
        "eligibility": _first(detail, "applicantEligibilityDesc"),
        "applicant_types": [a for a in applicant_types if a],
        "description": _first(detail, "synopsisDesc", "forecastDesc", "description"),
        "cfda_list": [f"{c.get('cfdaNumber')} — {c.get('programTitle')}"
                      for c in (data.get("opportunityCfdas") or []) if isinstance(c, dict)],
        "url": f"https://www.grants.gov/search-results-detail/{data.get('id') or opportunity_id}",
    }


def program_lineage(title):
    """Collapse a title to a program key so reissues of the same program group together.

    Federal BVI programs run on ~5-year cycles (the RRTC on Employment of People
    Who Are Blind or Have Low Vision ran 2015, 2020, 2025). Grouping past and open
    opportunities by lineage makes that cadence visible — for a small nonprofit,
    knowing when the next window opens is worth more than any single open RFP.
    """
    t = (title or "").lower()
    t = re.sub(r"\b(fy\s*)?(19|20)\d{2}\b", " ", t)          # strip years
    t = re.sub(r"\bcfda\b.*$", " ", t)
    t = re.sub(r"[^a-z ]+", " ", t)
    stop = {"the", "a", "an", "of", "for", "and", "to", "in", "on", "program",
            "grants", "grant", "announcement", "notice", "funding", "opportunity"}
    words = [w for w in t.split() if w not in stop and len(w) > 2]
    return " ".join(words[:8])
