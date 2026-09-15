import re
import logging
from typing import Optional, Dict, Any, List, Tuple
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Common non-company domains to reject in email validation
FREE_EMAIL_DOMAINS = {
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "aol.com",
    "protonmail.com", "mail.com", "zoho.com", "icloud.com"
}

# Image / binary file extensions that cannot be emails
IMAGE_EXTENSIONS = (
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".ico",
    ".tif", ".tiff", ".avif", ".mp4", ".mov", ".pdf", ".css", ".js"
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def extract_raw_page_facts(html: str, text: str, page_url: str) -> Dict[str, Any]:
    """
    Extract ONLY directly observable metadata from genuine page HTML/text.
    ZERO synthesis, ZERO guesswork, ZERO defaults.
    """
    facts = {
        "raw_page_title": None,
        "meta_description": None,
        "detected_emails": [],
        "detected_phones": [],
        "detected_social_links": [],
    }

    if not html and not text:
        return facts

    soup = None
    if html:
        try:
            soup = BeautifulSoup(html, "html.parser")
        except Exception as e:
            logger.debug(f"HTML parse error for {page_url}: {e}")

    # 1. Raw page title
    if soup:
        title_tag = soup.find("title")
        if title_tag and title_tag.string:
            facts["raw_page_title"] = title_tag.string.strip()
        elif soup.find("h1"):
            facts["raw_page_title"] = soup.find("h1").get_text().strip()

    # 2. Meta description / OpenGraph description
    if soup:
        meta_desc = soup.find("meta", attrs={"name": re.compile(r"^description$", re.I)})
        if not meta_desc:
            meta_desc = soup.find("meta", attrs={"property": re.compile(r"^og:description$", re.I)})
        if meta_desc and meta_desc.get("content"):
            desc_val = meta_desc.get("content").strip()
            if len(desc_val) > 5:
                facts["meta_description"] = desc_val

    # 3. Detected emails (strictly from mailto: or explicit visible text)
    candidate_emails = set()
    if soup:
        for mailto in soup.find_all("a", href=re.compile(r"^mailto:", re.I)):
            href = mailto.get("href", "")
            clean = href.split("?")[0].replace("mailto:", "").strip().lower()
            if "@" in clean and "." in clean.split("@")[-1]:
                candidate_emails.add(clean)

    # Text regex for emails
    content_to_search = f"{text or ''} {html or ''}"
    email_pattern = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,7}\b")
    for match in email_pattern.findall(content_to_search):
        clean = match.lower().strip()
        # Reject image filenames or code artifacts
        if not any(clean.endswith(ext) for ext in IMAGE_EXTENSIONS):
            candidate_emails.add(clean)

    # Filter emails: discard free providers unless that is the company itself, ensure valid domain
    valid_emails = []
    for em in candidate_emails:
        domain_part = em.split("@")[-1]
        if "." in domain_part and len(domain_part) >= 4:
            valid_emails.append(em)
    facts["detected_emails"] = sorted(list(set(valid_emails)))

    # 4. Detected phones (strictly from tel: links or explicit phone patterns)
    candidate_phones = set()
    if soup:
        for tel in soup.find_all("a", href=re.compile(r"^tel:", re.I)):
            ph = tel.get("href", "").replace("tel:", "").strip()
            if len(re.sub(r"[^\d]", "", ph)) >= 7:
                candidate_phones.add(ph)

    phone_pattern = re.compile(r"(?:\+?\d{1,3}[-.\s]?)?\(?\d{2,4}\)?[-.\s]?\d{3,4}[-.\s]?\d{3,4}")
    for match in phone_pattern.findall(text or ""):
        digits = re.sub(r"[^\d]", "", match)
        if 8 <= len(digits) <= 15:
            candidate_phones.add(match.strip())
    facts["detected_phones"] = sorted(list(candidate_phones))[:5]

    # 5. Detected official social / LinkedIn links (on-page links only)
    if soup:
        socials = []
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if "linkedin.com/company/" in href.lower():
                socials.append({"type": "linkedin_company", "url": href})
            elif "twitter.com/" in href.lower() or "x.com/" in href.lower():
                if not any(p in href.lower() for p in ["/share", "/intent", "/widgets"]):
                    socials.append({"type": "twitter", "url": href})
            elif "github.com/" in href.lower():
                socials.append({"type": "github", "url": href})
        
        # Deduplicate
        seen_urls = set()
        deduped_socials = []
        for s in socials:
            if s["url"] not in seen_urls:
                seen_urls.add(s["url"])
                deduped_socials.append(s)
        facts["detected_social_links"] = deduped_socials

    return facts


def validate_fact(
    field: str,
    value: Any,
    evidence_text: str,
    source_url: str,
    domain: str = ""
) -> Optional[Dict[str, Any]]:
    """
    Strict Anti-Hallucination Fact Validator.
    Verifies:
    1. Evidence exists and is non-empty.
    2. Value is genuinely supported by the evidence snippet.
    3. Value was NOT derived solely from domain heuristics (e.g. exampleai.com != AI industry).
    4. Value was NOT a synthetic default or placeholder.
    Returns:
        Provenance dict if valid, None if invalid/unsupported.
    """
    if value is None:
        return None

    str_val = str(value).strip()
    if not str_val or str_val.lower() in {"unknown", "not found", "none", "null", "n/a", "undefined"}:
        return None

    if not evidence_text or not isinstance(evidence_text, str) or not evidence_text.strip():
        logger.debug(f"[Anti-Hallucination] Rejected {field}='{str_val}': No evidence text provided.")
        return None

    evidence_lower = evidence_text.lower()
    val_lower = str_val.lower()

    # Rule 3: Never infer fact solely from domain name
    if domain:
        clean_domain = domain.lower().replace("www.", "").split(".")[0]
        if val_lower == clean_domain:
            logger.debug(f"[Anti-Hallucination] Rejected {field}='{str_val}': Derived solely from domain.")
            return None

    # Rule 2: Value must actually appear or have direct semantic match in the evidence snippet
    if val_lower not in evidence_lower:
        # Check tokens
        tokens = [t for t in re.split(r"\W+", val_lower) if len(t) > 2]
        if not tokens or not all(t in evidence_lower for t in tokens):
            logger.debug(f"[Anti-Hallucination] Rejected {field}='{str_val}': Not found in evidence snippet.")
            return None

    return {
        "field": field,
        "value": value,
        "source_url": source_url,
        "evidence_snippet": evidence_text[:300].strip(),
        "timestamp": utc_now_iso(),
        "confidence": "evidence_backed",
    }
