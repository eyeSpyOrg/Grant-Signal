"""Fetch and parse raw IRS 990 e-file XML.

Files come from the GivingTuesday 990 data lake, a public mirror of the IRS
e-file release on S3 (no key required):
  https://gt990datalake-rawdata.s3.amazonaws.com/EfileData/XmlFiles/{object_id}_public.xml

Extracts:
  - grants paid (990-PF Part XV: GrantOrContributionPdDurYrGrp, plus approved-for-future)
  - grants paid (990 Schedule I: RecipientTable)
  - officers/directors/trustees (key people)
  - header info (form type, tax year, website, org name)
"""
import xml.etree.ElementTree as ET
import requests

XML_URL = "https://gt990datalake-rawdata.s3.amazonaws.com/EfileData/XmlFiles/{oid}_public.xml"
HEADERS = {"User-Agent": "EyeSpyGrantScout/1.0"}


def fetch_xml(object_id):
    r = requests.get(XML_URL.format(oid=object_id), headers=HEADERS, timeout=60)
    if r.status_code != 200:
        return None
    return r.content


def _strip(tag):
    return tag.split("}", 1)[1] if "}" in tag else tag


def _index_children(elem):
    """Map of localname -> first text/element for quick lookups."""
    out = {}
    for c in elem.iter():
        out.setdefault(_strip(c.tag), c)
    return out


def _text(elem, *names):
    """First non-empty text among descendant elements with any of the given local names."""
    wanted = set(names)
    for c in elem.iter():
        if _strip(c.tag) in wanted and c.text and c.text.strip():
            return c.text.strip()
    return None


def _int(elem, *names):
    t = _text(elem, *names)
    if t is None:
        return None
    try:
        return int(float(t))
    except ValueError:
        return None


def parse_filing(xml_bytes):
    """Parse one filing. Returns dict with header, grants, people."""
    root = ET.fromstring(xml_bytes)
    header = {
        "form_type": _text(root, "ReturnTypeCd", "ReturnType"),
        "tax_year": _int(root, "TaxYr", "TaxYear"),
        "org_name": None,
        "website": _text(root, "WebsiteAddressTxt", "WebSiteAddressTxt", "WebsiteAddress"),
        "mission": _text(root, "ActivityOrMissionDesc", "MissionDesc"),
    }
    # Filer name lives in ReturnHeader/Filer/BusinessName
    for c in root.iter():
        if _strip(c.tag) == "Filer":
            header["org_name"] = _text(c, "BusinessNameLine1Txt", "BusinessNameLine1")
            break

    grants = []
    people = []
    seen_person_elems = set()

    for elem in root.iter():
        tag = _strip(elem.tag)

        # --- 990-PF Part XV: grants paid during year / approved for future ---
        if tag in ("GrantOrContributionPdDurYrGrp", "GrantOrContributionPaidDuringYear",
                   "GrantOrContriApprvForFutGrp", "GrantOrContributionApprvForFut"):
            is_future = "Fut" in tag
            grants.append({
                "recipient_name": _text(elem, "BusinessNameLine1Txt", "BusinessNameLine1",
                                        "RecipientPersonNm", "RecipientPersonName"),
                "recipient_ein": None,
                "city": _text(elem, "CityNm", "City"),
                "state": _text(elem, "StateAbbreviationCd", "State"),
                "purpose": _text(elem, "GrantOrContributionPurposeTxt", "GrantOrContributionPurpose"),
                "amount": _int(elem, "Amt", "Amount"),
                "is_future": is_future,
            })

        # --- 990 Schedule I: grants to organizations ---
        elif tag in ("RecipientTable", "Form990ScheduleIPartII"):
            grants.append({
                "recipient_name": _text(elem, "BusinessNameLine1Txt", "BusinessNameLine1"),
                "recipient_ein": _text(elem, "RecipientEIN", "EINOfRecipient"),
                "city": _text(elem, "CityNm", "City"),
                "state": _text(elem, "StateAbbreviationCd", "State"),
                "purpose": _text(elem, "PurposeOfGrantTxt", "PurposeOfGrant"),
                "amount": _int(elem, "CashGrantAmt", "AmountOfCashGrant"),
                "is_future": False,
            })

        # --- key people: 990 Part VII / 990-PF officer list / 990-EZ ---
        elif tag in ("Form990PartVIISectionAGrp", "Form990PartVIISectionA",
                     "OfficerDirTrstKeyEmplInfoGrp", "OfficerDirectorTrusteeEmplGrp",
                     "OfficerDirectorTrusteeKeyEmpl", "OfcrDirTrusteesOrKeyEmployee",
                     "OfficerDirTrstKeyEmplGrp"):
            if id(elem) in seen_person_elems:
                continue
            seen_person_elems.add(id(elem))
            name = _text(elem, "PersonNm", "PersonName", "BusinessNameLine1Txt", "BusinessNameLine1")
            if not name:
                continue
            comp = _int(elem, "ReportableCompFromOrgAmt", "CompensationAmt", "Compensation",
                        "ReportableCompFromOrganization")
            people.append({
                "name": name,
                "title": _text(elem, "TitleTxt", "Title"),
                "compensation": comp,
            })

    # de-dup people by (name, title)
    seen = set()
    uniq_people = []
    for p in people:
        key = (p["name"], p.get("title"))
        if key not in seen:
            seen.add(key)
            uniq_people.append(p)

    # drop grant rows with no name and no amount (schema noise)
    grants = [g for g in grants if g.get("recipient_name") or g.get("amount")]

    return {"header": header, "grants": grants, "people": uniq_people}
