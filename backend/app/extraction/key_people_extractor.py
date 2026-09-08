import re
import logging
from typing import List, Dict, Any, Optional
from urllib.parse import quote, urlparse

logger = logging.getLogger(__name__)

EXECUTIVE_TITLES = [
    "Chief Executive Officer", "CEO", "Founder", "Co-Founder",
    "Chief Technology Officer", "CTO", "President", "Managing Director",
    "Chief Operating Officer", "COO", "Chief Financial Officer", "CFO", "VP",
    "Vice President", "Director", "Head of Engineering", "Head of Product",
    "Head of Data", "General Partner", "Executive Director", "Principal"
]

TITLE_PATTERN = r"(?:CEO|CTO|COO|CFO|President|Founder|Co-Founder|Managing Director|Executive Director|Vice President|VP|Director|Head of [A-Za-z]+|Chief [A-Za-z]+ Officer)"

class KeyPeopleExtractor:
    """
    Extracts key decision makers, executives, and leadership personnel with:
    - Four-part provenance (value, source_url, source_type, extracted_at)
    - Deterministic title-to-role_tags compound array mapping
    - Generated LinkedIn search URLs (never direct private LinkedIn scraping)
    """

    @staticmethod
    def derive_role_tags(title: str) -> List[str]:
        """
        Deterministic, immutable mapping table from job title to compound role tags (role_tags: []).
        """
        if not title:
            return ["Unknown"]
            
        t_low = title.lower()
        
        # 1. Economic Buyers
        if any(term in t_low for term in ["ceo", "chief executive", "cfo", "chief financial", "founder", "co-founder", "president", "managing director", "owner", "general partner"]):
            return ["Economic Buyer", "Contact Person"]

        # 2. Directors (explicit B2B rule for Director level leadership)
        if "director" in t_low and not any(term in t_low for term in ["hr", "support", "office"]):
            return ["Contact Person", "Economic Buyer"]

        # 3. Technical Buyers
        if any(term in t_low for term in ["cto", "chief technology", "engineering", "technology", "architect", "data", "developer", "devops"]):
            return ["Technical Buyer", "Contact Person"]

        # 4. Champions
        if any(term in t_low for term in ["product", "growth", "sales", "marketing", "business development", "head of "]):
            return ["Champion", "Contact Person"]

        # 5. Contact Person
        if any(term in t_low for term in ["hr", "human resources", "office", "support", "pr", "communications", "operations", "coo", "manager", "vp", "vice president"]):
            return ["Contact Person"]

        return ["Unknown"]

    @staticmethod
    def build_person_record(
        name: str,
        title: str,
        company_name: str,
        source_url: str = "",
        confidence: str = "high"
    ) -> Dict[str, Any]:
        role_tags = KeyPeopleExtractor.derive_role_tags(title)
        query_str = quote(f"{name} {company_name}")
        linkedin_url = f"https://www.linkedin.com/search/results/all/?keywords={query_str}"
        return {
            "name": name,
            "title": title,
            "role_tags": role_tags,
            "role_tag": role_tags[0] if role_tags else "Unknown",
            "linkedin_search_url": linkedin_url,
            "source_url": source_url,
            "confidence": confidence
        }

    @staticmethod
    def extract_from_text_and_html(
        text: str,
        html: str,
        company_name: str,
        domain: str,
        source_url: str = ""
    ) -> List[Dict[str, Any]]:
        people: List[Dict[str, Any]] = []
        seen_names = set()

        if not text and not html:
            return people

        clean_cname = company_name.split("|")[0].split("-")[0].strip()
        site_url = source_url or f"https://{domain}"

        # 1. Regex Pattern: Name followed by title (e.g., "Jane Doe, CEO" or "Jane Doe Chief Executive Officer")
        p1 = re.compile(
            r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})\b\s*[\–\-,\:\(]?\s*(" + TITLE_PATTERN + r"[A-Za-z0-9\s,\&]{0,40})",
            re.IGNORECASE
        )
        for match in p1.finditer(text):
            name = match.group(1).strip()
            title = match.group(2).strip().rstrip(".,)")
            if KeyPeopleExtractor._is_valid_person_name(name, clean_cname):
                n_lower = name.lower()
                if n_lower not in seen_names:
                    seen_names.add(n_lower)
                    people.append(KeyPeopleExtractor.build_person_record(
                        name=name,
                        title=title.title(),
                        company_name=clean_cname,
                        source_url=site_url,
                        confidence="high"
                    ))

        # 2. Regex Pattern: Title followed by Name (e.g., "CEO: Jane Doe", "Founder - Alex Smith")
        p2 = re.compile(
            r"\b(" + TITLE_PATTERN + r")\s*[\–\-,\:]\s*\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})\b",
            re.IGNORECASE
        )
        for match in p2.finditer(text):
            title = match.group(1).strip()
            name = match.group(2).strip()
            if KeyPeopleExtractor._is_valid_person_name(name, clean_cname):
                n_lower = name.lower()
                if n_lower not in seen_names:
                    seen_names.add(n_lower)
                    people.append(KeyPeopleExtractor.build_person_record(
                        name=name,
                        title=title.title(),
                        company_name=clean_cname,
                        source_url=site_url,
                        confidence="high"
                    ))

        return people[:8]

    @staticmethod
    def extract_from_linkedin_search_snippets(
        snippets: List[Dict[str, Any]],
        company_name: str
    ) -> List[Dict[str, Any]]:
        people: List[Dict[str, Any]] = []
        seen = set()
        clean_cname = company_name.split("|")[0].split("-")[0].strip()
        cname_tokens = {t.lower() for t in clean_cname.split() if len(t) > 2}

        for s in snippets:
            title_text = s.get("title", "")
            snippet_text = s.get("snippet", "")
            url = s.get("url", "")
            combined = f"{title_text} {snippet_text}"
            comb_lower = combined.lower()

            if cname_tokens and not any(t in comb_lower for t in cname_tokens):
                continue

            match = re.search(r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})\s*[\-|\–]\s*([A-Za-z0-9\s,\&]{3,40}(?:CEO|President|Director|Chief|Founder|Executive|Manager|VP)[A-Za-z0-9\s,\&]*)", combined)
            if match:
                name = match.group(1).strip()
                t_str = match.group(2).strip()
                if KeyPeopleExtractor._is_valid_person_name(name, clean_cname):
                    n_lower = name.lower()
                    if n_lower not in seen:
                        seen.add(n_lower)
                        people.append(KeyPeopleExtractor.build_person_record(
                            name=name,
                            title=t_str.title(),
                            company_name=clean_cname,
                            source_url=url,
                            confidence="medium"
                        ))

        return people[:6]

    @staticmethod
    def _is_valid_person_name(name: str, company_name: str) -> bool:
        if not name or len(name) < 4 or len(name) > 35:
            return False
        words = name.split()
        if len(words) < 2 or len(words) > 4:
            return False
        
        stopwords = {
            "about", "contact", "home", "privacy", "terms", "policy", "company", "careers",
            "login", "sign", "register", "services", "products", "solutions", "features",
            "support", "sales", "news", "blog", "press", "media", "overview", "global",
            "world", "bank", "open", "data", "github", "desktop", "qatar", "airways"
        }
        
        if any(w.lower() in stopwords for w in words):
            return False
        if company_name and name.lower() == company_name.lower():
            return False
        return True

key_people_extractor = KeyPeopleExtractor()
