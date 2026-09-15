import re
import json
import logging
from bs4 import BeautifulSoup
from typing import Dict, Any, List, Optional
from urllib.parse import urlparse
from app.normalization.normalizer import normalizer

logger = logging.getLogger(__name__)

CCTLD_COUNTRY_MAP = {
    "mu": "Mauritius",
    "uk": "United Kingdom",
    "de": "Germany",
    "fr": "France",
    "ca": "Canada",
    "au": "Australia",
    "in": "India",
    "za": "South Africa",
    "sg": "Singapore",
    "ae": "United Arab Emirates",
    "jp": "Japan",
    "nl": "Netherlands",
    "ch": "Switzerland",
    "se": "Sweden",
    "no": "Norway",
    "dk": "Denmark",
    "fi": "Finland",
    "es": "Spain",
    "it": "Italy",
    "pt": "Portugal",
    "pl": "Poland",
    "ie": "Ireland",
    "il": "Israel",
    "nz": "New Zealand",
    "br": "Brazil",
    "mx": "Mexico",
    "ar": "Argentina",
    "cl": "Chile",
    "co": "Colombia",
    "my": "Malaysia",
    "id": "Indonesia",
    "ph": "Philippines",
    "th": "Thailand",
    "vn": "Vietnam",
    "kr": "South Korea",
    "ng": "Nigeria",
    "ke": "Kenya",
    "gh": "Ghana",
    "rw": "Rwanda",
    "tz": "Tanzania",
    "eg": "Egypt",
    "sa": "Saudi Arabia",
    "pk": "Pakistan",
    "bd": "Bangladesh",
    "tr": "Turkey",
    "at": "Austria",
    "be": "Belgium",
    "cz": "Czech Republic",
    "ro": "Romania",
    "gr": "Greece",
    "hu": "Hungary",
    "cn": "China",
    "hk": "Hong Kong",
    "tw": "Taiwan",
}

class CSSExtractor:
    @staticmethod
    def extract_deterministic_metadata(html_content: str, page_url: str) -> Dict[str, Any]:
        """Extract deterministic metadata from HTML structure without using an LLM."""
        soup = BeautifulSoup(html_content or "", "lxml")

        # 1. Page Title
        title = None
        og_title = soup.find("meta", property="og:title")
        if og_title and og_title.get("content"):
            title = normalizer.normalize_string(og_title.get("content"))
        if not title and soup.title and soup.title.string:
            title = normalizer.normalize_string(soup.title.string)
        if not title:
            h1 = soup.find("h1")
            if h1:
                title = normalizer.normalize_string(h1.text)

        # 2. Description
        description = None
        meta_desc = soup.find("meta", attrs={"name": "description"}) or soup.find("meta", property="og:description")
        if meta_desc and meta_desc.get("content"):
            description = normalizer.normalize_string(meta_desc.get("content"))

        # 3. Canonical URL
        canonical_url = page_url
        link_canonical = soup.find("link", rel="canonical")
        if link_canonical and link_canonical.get("href"):
            canonical_url = normalizer.normalize_url(link_canonical.get("href"), base_url=page_url) or page_url

        # 4. Language
        html_tag = soup.find("html")
        language = None
        if html_tag and html_tag.get("lang"):
            language = normalizer.normalize_language(html_tag.get("lang"))

        # 5. JSON-LD structured data extraction
        json_ld_data = []
        json_ld_emails = []
        schema_org_type: Optional[str] = None
        schema_address: Dict[str, Any] = {}
        schema_employees: Optional[str] = None

        def _traverse_schema(obj: Any):
            nonlocal schema_org_type, schema_address, schema_employees
            if isinstance(obj, list):
                for item in obj:
                    _traverse_schema(item)
            elif isinstance(obj, dict):
                # Check for @graph
                if "@graph" in obj and isinstance(obj["@graph"], list):
                    _traverse_schema(obj["@graph"])
                    return

                # Capture @type
                t = obj.get("@type")
                if t and isinstance(t, str) and not schema_org_type and t.lower() not in ["webpage", "website", "breadcrumblist"]:
                    schema_org_type = t
                elif isinstance(t, list):
                    for sub_t in t:
                        if isinstance(sub_t, str) and sub_t.lower() not in ["webpage", "website", "breadcrumblist"]:
                            schema_org_type = sub_t
                            break

                # Capture email
                if "email" in obj and isinstance(obj["email"], str):
                    json_ld_emails.append(obj["email"].strip())

                # Capture PostalAddress
                if "address" in obj and not schema_address:
                    addr = obj["address"]
                    if isinstance(addr, dict):
                        schema_address = {
                            "locality": addr.get("addressLocality"),
                            "region": addr.get("addressRegion"),
                            "country": addr.get("addressCountry") if isinstance(addr.get("addressCountry"), str) else (addr.get("addressCountry") or {}).get("name"),
                            "street": addr.get("streetAddress"),
                            "postal_code": addr.get("postalCode")
                        }
                    elif isinstance(addr, str) and len(addr.strip()) > 3:
                        schema_address = {"formatted": addr.strip()}

                # Capture numberOfEmployees
                if "numberOfEmployees" in obj and not schema_employees:
                    emp = obj["numberOfEmployees"]
                    if isinstance(emp, (int, float)):
                        schema_employees = str(int(emp))
                    elif isinstance(emp, dict):
                        v = emp.get("value") or emp.get("minValue")
                        if v:
                            schema_employees = str(v)
                    elif isinstance(emp, str):
                        schema_employees = emp.strip()

        for script in soup.find_all("script", type="application/ld+json"):
            try:
                if script.string:
                    data = json.loads(script.string.strip())
                    json_ld_data.append(data)
                    _traverse_schema(data)
            except Exception:
                pass

        # 6. HTML Meta Geo & Location Tags
        geo_region = None
        geo_placename = None
        geo_country = None

        meta_geo_reg = soup.find("meta", attrs={"name": "geo.region"})
        if meta_geo_reg and meta_geo_reg.get("content"):
            geo_region = meta_geo_reg.get("content").strip()

        meta_geo_place = soup.find("meta", attrs={"name": "geo.placename"})
        if meta_geo_place and meta_geo_place.get("content"):
            geo_placename = meta_geo_place.get("content").strip()

        meta_country = (
            soup.find("meta", attrs={"name": "country"})
            or soup.find("meta", property="og:country-name")
            or soup.find("meta", property="business:contact_data:country_name")
        )
        if meta_country and meta_country.get("content"):
            geo_country = meta_country.get("content").strip()

        meta_locality = soup.find("meta", property="business:contact_data:locality")
        if meta_locality and meta_locality.get("content") and not geo_placename:
            geo_placename = meta_locality.get("content").strip()

        # 7. ccTLD Country Detection from Page URL
        cctld_country: Optional[str] = None
        try:
            netloc = urlparse(page_url).netloc.lower().split(":")[0]
            parts = netloc.split(".")
            if len(parts) >= 2:
                last_part = parts[-1]
                if last_part in CCTLD_COUNTRY_MAP:
                    cctld_country = CCTLD_COUNTRY_MAP[last_part]
                elif len(parts) >= 3 and parts[-2] in ["co", "com", "org", "gov"] and parts[-1] in CCTLD_COUNTRY_MAP:
                    cctld_country = CCTLD_COUNTRY_MAP[parts[-1]]
        except Exception:
            pass

        # 8. Unified Location Resolution (Schema.org -> HTML Geo Meta -> ccTLD -> Regex)
        resolved_country: Optional[str] = None
        resolved_region: Optional[str] = None
        resolved_city: Optional[str] = None
        formatted_location: Optional[str] = None

        if schema_address:
            resolved_city = schema_address.get("locality")
            resolved_region = schema_address.get("region")
            raw_c = schema_address.get("country")
            if raw_c:
                resolved_country = normalizer.normalize_country(raw_c) or raw_c
            if schema_address.get("formatted"):
                formatted_location = schema_address["formatted"]

        if not resolved_country:
            if geo_country:
                resolved_country = normalizer.normalize_country(geo_country) or geo_country
            elif cctld_country:
                resolved_country = cctld_country

        if not resolved_city and geo_placename:
            resolved_city = geo_placename
        if not resolved_region and geo_region:
            resolved_region = geo_region

        # Synthesize formatted location string if not already provided
        if not formatted_location:
            parts_loc = []
            if resolved_city:
                parts_loc.append(resolved_city)
            if resolved_region and resolved_region != resolved_city:
                parts_loc.append(resolved_region)
            if resolved_country:
                parts_loc.append(resolved_country)
            if parts_loc:
                formatted_location = ", ".join(parts_loc)

        # 9. Evidence-based Email Extraction (mailto links + page content)
        import re as _email_re
        emails = []
        for a in soup.find_all("a", href=True):
            href = a.get("href", "")
            if href.lower().startswith("mailto:"):
                clean_email = href.split("?")[0].replace("mailto:", "").strip().lower()
                if _email_re.match(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", clean_email):
                    if not any(bad in clean_email for bad in ["example.com", "domain.com", "wixpress", "sentry", "github.com"]):
                        emails.append(clean_email)
        
        for em in json_ld_emails:
            em_clean = em.replace("mailto:", "").strip().lower()
            if _email_re.match(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", em_clean):
                if not any(bad in em_clean for bad in ["example.com", "domain.com", "wixpress", "sentry", "github.com"]):
                    emails.append(em_clean)

        unique_emails = list(dict.fromkeys(emails))[:5]

        return {
            "title": title,
            "description": description,
            "canonical_url": canonical_url,
            "language": language,
            "json_ld": json_ld_data,
            "headings_h1": [normalizer.normalize_string(h.text) for h in soup.find_all("h1") if h.text],
            "contact_emails": unique_emails,
            "schema_org_type": schema_org_type,
            "schema_employees": schema_employees,
            "location_info": {
                "city": resolved_city,
                "region": resolved_region,
                "country": resolved_country,
                "formatted": formatted_location
            }
        }

css_extractor = CSSExtractor()
