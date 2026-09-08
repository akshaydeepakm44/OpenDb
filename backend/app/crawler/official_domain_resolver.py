import re
import logging
from typing import Dict, Any, List, Optional
from urllib.parse import urlparse
from app.safety.guardrails import extract_domain, get_root_domain
from app.safety.domain_safety_guard import domain_safety_guard
from app.extraction.key_people_extractor import key_people_extractor

logger = logging.getLogger(__name__)

# Common non-company domain suffixes to ignore when looking for official domains
IGNORED_GENERIC_DOMAINS = {
    "linkedin.com", "crunchbase.com", "tracxn.com", "g2.com", "capterra.com",
    "clutch.co", "producthunt.com", "ycombinator.com", "facebook.com", "twitter.com",
    "instagram.com", "github.com", "wikipedia.org", "youtube.com", "medium.com"
}


class OfficialDomainResolver:
    """
    Official Domain Resolver — Phase 5 of Master Architecture.
    Resolves untrusted directory/third-party candidates into verified official company domains.
    Extracts key people when candidate stems from LinkedIn or company directories.
    """

    @staticmethod
    def resolve_candidate(candidate: Dict[str, Any]) -> Dict[str, Any]:
        """
        Resolves candidate result into official domain structure.
        """
        raw_url = candidate.get("url", "")
        title = candidate.get("title", "")
        snippet = candidate.get("snippet", "")
        source_domain = extract_domain(raw_url)

        evidence = []
        confidence = 50
        extracted_key_people = []

        # 1. Extract Company Name from title/snippet
        company_name = OfficialDomainResolver._extract_company_name(title, snippet, source_domain)

        # 2. Extract Key People if candidate comes from LinkedIn or directory snippet
        if "linkedin.com" in source_domain or "crunchbase.com" in source_domain or "g2.com" in source_domain or "directory" in title.lower():
            people = key_people_extractor.extract_from_text_and_html(
                text=f"{title}\n{snippet}",
                html="",
                company_name=company_name,
                domain=source_domain,
                source_url=raw_url
            )
            if people:
                extracted_key_people.extend(people)
                evidence.append(f"Extracted {len(people)} key leadership personnel from directory snippet")

        # 3. Extract potential official domain from snippet or URL
        extracted_domain = OfficialDomainResolver._extract_official_domain(raw_url, snippet)

        if extracted_domain and extracted_domain not in IGNORED_GENERIC_DOMAINS:
            # Evaluate extracted official domain safety
            safety = domain_safety_guard.evaluate_domain_safety(extracted_domain)
            if safety["allowed"]:
                official_domain = safety["domain"]
                official_url = f"https://{official_domain}"
                confidence += 30
                evidence.append(f"Extracted official website '{official_domain}' from candidate evidence")
            else:
                official_domain = ""
                official_url = ""
                evidence.append(f"Rejected extracted domain '{extracted_domain}' (Unsafe: {safety['reason']})")
        else:
            # If candidate URL itself is already an official non-directory domain
            if source_domain not in IGNORED_GENERIC_DOMAINS:
                safety = domain_safety_guard.evaluate_domain_safety(source_domain)
                if safety["allowed"]:
                    official_domain = source_domain
                    official_url = f"https://{official_domain}"
                    confidence += 25
                    evidence.append(f"Candidate domain '{official_domain}' verified as direct official site")
                else:
                    official_domain = ""
                    official_url = ""
            else:
                official_domain = ""
                official_url = ""

        # 4. Domain branding match bonus
        if company_name and official_domain:
            clean_name = re.sub(r'[^a-zA-Z0-9]', '', company_name.lower())
            clean_dom = official_domain.split('.')[0]
            if clean_name in clean_dom or clean_dom in clean_name:
                confidence += 15
                evidence.append(f"Domain branding match: '{clean_name}' matches '{clean_dom}'")

        # 5. Enforce minimum confidence >= 70
        final_status = "RESOLVED" if (confidence >= 70 and official_domain) else "PENDING_DOMAIN_RESOLUTION"

        return {
            "company_name": company_name or official_domain.capitalize(),
            "official_domain": official_domain,
            "official_url": official_url,
            "confidence": min(100, confidence),
            "evidence": evidence,
            "extracted_key_people": extracted_key_people,
            "source_candidate_url": raw_url,
            "status": final_status
        }

    @staticmethod
    def _extract_company_name(title: str, snippet: str, domain: str) -> str:
        """Extract clean company name from title/snippet."""
        clean_title = re.sub(r'\s*[\|-]\s*(?:LinkedIn|Crunchbase|G2|Clutch|Product Hunt|Twitter|Facebook).*$', '', title, flags=re.IGNORECASE)
        clean_title = re.sub(r'^(?:Top|List of|Best)\s+', '', clean_title, flags=re.IGNORECASE).strip()
        if len(clean_title) > 3 and len(clean_title) < 60:
            return clean_title
        dom_parts = domain.split('.')[0].capitalize()
        return dom_parts

    @staticmethod
    def _extract_official_domain(url: str, snippet: str) -> str:
        """Search text for explicit official domain links (e.g. www.acme.com or acme.com)."""
        urls = re.findall(r'https?://(?:www\.)?([a-zA-Z0-9-]+\.[a-zA-Z]{2,})', snippet)
        for found_dom in urls:
            found_clean = extract_domain(found_dom)
            if found_clean and found_clean not in IGNORED_GENERIC_DOMAINS:
                return found_clean
        return ""

official_domain_resolver = OfficialDomainResolver()
