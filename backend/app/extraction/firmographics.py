import re
from typing import Dict, Any, Optional, Tuple
from urllib.parse import urlparse
from app.normalization.normalizer import normalizer

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

def map_employee_count_to_tier(count: int) -> str:
    """Standardize headcount integer into standard Company Size Tier."""
    if count <= 10:
        return "Startup / Micro (1-10)"
    elif count <= 50:
        return "Small (11-50)"
    elif count <= 200:
        return "Growth SMB (51-200)"
    elif count < 1000:
        return "Mid-Market (201-1000)"
    else:
        return "Enterprise (1000+)"

def standardize_company_tier(raw_size: Any) -> str:
    """
    Standardizes any raw company size representation into one of:
    - Startup / Micro (1-10)
    - Small (11-50)
    - Growth SMB (51-200)
    - Mid-Market (201-1000)
    - Enterprise (1000+)
    - Unknown
    """
    if not raw_size:
        return "Unknown"
    
    val_str = str(raw_size).strip()
    if val_str.lower() in ["unknown", "not specified", "null", "none", "undefined", ""]:
        return "Unknown"

    # Already standardized
    for tier in ["Startup / Micro", "Small (11-50)", "Growth SMB", "Mid-Market", "Enterprise"]:
        if tier.lower() in val_str.lower():
            return val_str

    # Range match: e.g. "50-200" or "50 to 100" or "201-500"
    range_match = re.search(r"(\d{1,6})\s*(?:-|to)\s*(\d{1,6})", val_str)
    if range_match:
        avg = (int(range_match.group(1)) + int(range_match.group(2))) // 2
        return map_employee_count_to_tier(avg)

    # Plus match: e.g. "1000+" or "500+" or "50+"
    plus_match = re.search(r"(\d{1,6})\+", val_str)
    if plus_match:
        num = int(plus_match.group(1))
        return map_employee_count_to_tier(num)

    # Single integer match: e.g. "250" or "2,500"
    single_match = re.search(r"(\d{1,3}(?:,\d{3})+|\d{1,6})", val_str)
    if single_match:
        num = int(single_match.group(1).replace(",", ""))
        return map_employee_count_to_tier(num)

    return "Unknown"

def extract_firmographic_size(text: str) -> Tuple[str, str]:
    """
    Extract employee count and standardized company size tier from text context.
    Returns (company_size, company_tier).
    """
    if not text:
        return "Unknown", "Unknown"

    # 1. Direct explicit headcount statements
    # e.g., "more than 2,000 employees", "over 500 team members", "150 staff"
    emp_pattern = re.search(
        r"(?:more than|over|approx(?:imately)?|around|nearly|\+)?\s*(\d{1,3}(?:,\d{3})+|\d{1,6})\s*(?:\+)?\s*(?:employees|team members|staff|colleagues|workers|associates|people worldwide)\b",
        text,
        re.IGNORECASE
    )
    if emp_pattern:
        raw_num = emp_pattern.group(1).replace(",", "")
        num = int(raw_num)
        size_str = f"{raw_num}+" if "+" in emp_pattern.group(0) or "more" in emp_pattern.group(0) or "over" in emp_pattern.group(0) else raw_num
        return size_str, map_employee_count_to_tier(num)

    # 2. Team of X pattern
    team_pattern = re.search(
        r"\b(?:team|workforce)\s+of\s+(?:more than\s+|over\s+)?(\d{1,3}(?:,\d{3})+|\d{1,6})\b",
        text,
        re.IGNORECASE
    )
    if team_pattern:
        num = int(team_pattern.group(1).replace(",", ""))
        return str(num), map_employee_count_to_tier(num)

    # 3. Range pattern: "50-200 employees"
    range_pattern = re.search(
        r"\b(\d{1,5})\s*(?:-|to)\s*(\d{1,5})\s*(?:employees|team members|staff|people)\b",
        text,
        re.IGNORECASE
    )
    if range_pattern:
        low = int(range_pattern.group(1))
        high = int(range_pattern.group(2))
        avg = (low + high) // 2
        return f"{low}-{high}", map_employee_count_to_tier(avg)

    # 4. Physical footprint / Store count heuristics for retail & chains
    # e.g. "operates 35 supermarkets", "network of 40 stores", "25 locations"
    store_pattern = re.search(
        r"\b(?:operates?|network of|over|with)\s*(\d{1,4})\s*(?:supermarkets?|hypermarkets?|stores?|retail outlets?|branches?)\b",
        text,
        re.IGNORECASE
    )
    if store_pattern:
        stores = int(store_pattern.group(1))
        if stores >= 25:
            return f"{stores} locations (~1,000+ staff)", "Enterprise (1000+)"
        elif stores >= 10:
            return f"{stores} locations (~250-500 staff)", "Mid-Market (201-1000)"
        elif stores >= 3:
            return f"{stores} locations (~50-150 staff)", "Growth SMB (51-200)"
        elif stores >= 1:
            return f"{stores} store(s)", "Small (11-50)"

    return "Unknown", "Unknown"

def extract_firmographic_location(
    text: str,
    page_url: str = "",
    html_location_info: Optional[Dict[str, Any]] = None
) -> Dict[str, Optional[str]]:
    """
    Extract high-precision location, region, and country from multiple signals.
    """
    city = None
    region = None
    country = None
    formatted = None

    if html_location_info:
        city = html_location_info.get("city")
        region = html_location_info.get("region")
        country = html_location_info.get("country")
        formatted = html_location_info.get("formatted")

    # ccTLD country fallback if country still missing
    if not country and page_url:
        try:
            netloc = urlparse(page_url).netloc.lower().split(":")[0]
            parts = netloc.split(".")
            if len(parts) >= 2:
                last_tld = parts[-1]
                if last_tld in CCTLD_COUNTRY_MAP:
                    country = CCTLD_COUNTRY_MAP[last_tld]
                elif len(parts) >= 3 and parts[-2] in ["co", "com", "org", "gov"] and parts[-1] in CCTLD_COUNTRY_MAP:
                    country = CCTLD_COUNTRY_MAP[parts[-1]]
        except Exception:
            pass

    # Regex search for physical HQ or address in text if formatted location is missing
    if not formatted and text:
        # Pattern 1: Headquarters / HQ in City, Country/State
        hq_match = re.search(
            r"(?:Headquarters|HQ|Main Office|Corporate Office|Based in|Located in|Registered Office)\s*[:\-–]?\s*([A-Z][A-Za-z0-9\s,\.\-]{3,60}(?:Mauritius|United States|USA|UK|United Kingdom|Germany|France|India|Singapore|Australia|Canada|Japan|CA|NY|TX|WA|IL|FL|\d{5}))",
            text
        )
        if hq_match:
            formatted = hq_match.group(1).strip().rstrip(".,")
            # If country not set yet, check if country name is in matched string
            if not country:
                for c_name in CCTLD_COUNTRY_MAP.values():
                    if c_name.lower() in formatted.lower():
                        country = c_name
                        break

    # If still no formatted location, construct from available components
    if not formatted:
        parts_loc = [p for p in [city, region, country] if p]
        if parts_loc:
            formatted = ", ".join(dict.fromkeys(parts_loc))

    return {
        "city": city,
        "region": region,
        "country": normalizer.normalize_country(country) if country else None,
        "formatted": formatted
    }
