from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime

import httpx
from bs4 import BeautifulSoup


CSLB_BASE = "https://www.cslb.ca.gov"


class SourceUnavailableError(Exception):
    pass


class LicenseNotFoundError(Exception):
    pass


def validate_license_number(value: str) -> str:
    number = value.strip()
    if not re.fullmatch(r"[0-9]{1,8}", number):
        raise ValueError("Enter a California contractor license number (1–8 digits).")
    return number


def parse_license_page(html: str, license_number: str) -> dict[str, object]:
    soup = BeautifulSoup(html, "html.parser")
    header = soup.find(id="MainContent_Header2Detail")
    if not header or header.get_text(" ", strip=True) != license_number:
        raise LicenseNotFoundError("No matching CSLB license detail was returned.")

    def field(element_id: str) -> str | None:
        element = soup.find(id=element_id)
        return element.get_text(" ", strip=True) if element else None

    business = soup.find(id="MainContent_BusInfo")
    name_node = business.find(string=True, recursive=False) if business else None
    name = str(name_node).strip() if name_node else None
    classifications = soup.find(id="MainContent_ClassCellTable")
    classes = [a.get_text(" ", strip=True) for a in classifications.find_all("a")] if classifications else []
    status = field("MainContent_Status")
    bond = field("MainContent_BondingCellTable")
    workers_comp = field("MainContent_WCStatus")
    if not name or not status:
        raise SourceUnavailableError("CSLB returned an incomplete license detail page.")

    active = "current and active" in status.lower()
    c10 = any(re.match(r"^C-?10\b", item, re.I) for item in classes)
    bond_on_record = bool(bond and "filed a Contractor's Bond" in bond)
    workers_comp_on_record = bool(workers_comp and "has workers compensation insurance" in workers_comp)
    checks = {
        "licenseActive": active,
        "c10Classification": c10,
        "contractorBondOnRecord": bond_on_record,
        "workersCompOnRecord": workers_comp_on_record,
    }
    if not active or not c10:
        assessment = "does_not_meet_selected_checks"
    elif not bond_on_record or not workers_comp_on_record:
        assessment = "review_required"
    else:
        assessment = "selected_checks_present"
    source_url = f"{CSLB_BASE}/{license_number}"
    return {
        "serviceId": "contractor-check",
        "licenseNumber": license_number,
        "businessName": name,
        "assessment": assessment,
        "checks": checks,
        "details": {
            "licenseStatus": status,
            "classifications": classes,
            "expirationDate": field("MainContent_ExpDt"),
            "bondRecord": bond,
            "workersCompRecord": workers_comp,
        },
        "source": {
            "publisher": "California Contractors State License Board",
            "url": source_url,
            "sourceAsOf": field("MainContent_extractDate"),
            "retrievedAt": datetime.now(UTC).isoformat(),
            "htmlSha256": hashlib.sha256(html.encode("utf-8")).hexdigest(),
        },
        "limitations": [
            "This report summarizes CSLB's public record at the stated time; the record can change.",
            "An insurance entry in CSLB's record does not authenticate a certificate or confirm project-specific coverage.",
            "Review the original CSLB page and insurance documents before approving a contractor.",
        ],
    }


async def fetch_license_report(license_number: str) -> dict[str, object]:
    number = validate_license_number(license_number)
    async with httpx.AsyncClient(timeout=12, follow_redirects=False) as client:
        try:
            response = await client.get(f"{CSLB_BASE}/{number}", headers={"User-Agent": "AgenticServices-ContractorCheck/0.1 (+https://aisoup.net)"})
        except httpx.RequestError as error:
            raise SourceUnavailableError("CSLB could not be reached.") from error
    if response.status_code != 200 or len(response.content) > 1_000_000:
        raise SourceUnavailableError("CSLB did not return an expected license detail page.")
    return parse_license_page(response.text, number)
