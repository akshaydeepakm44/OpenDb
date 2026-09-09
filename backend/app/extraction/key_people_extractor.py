import re
import logging
from typing import List, Dict, Any, Optional
from urllib.parse import quote, urlparse

logger = logging.getLogger(__name__)

# Common executive & decision maker titles
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
    Extracts key decision makers, executives, and leadership personnel from:
    1. Crawled webpage HTML/Markdown content
    2. Public LinkedIn profile links in HTML
    3. SearXNG public search snippets
    """

    @staticmethod
    def extract_from_text_and_html(
        text: str,
        html: str,
        company_name: str,
        domain: str
    ) -> List[Dict[str, Any]]:
        people: List[Dict[str, Any]] = []
        seen_names = set()

        if not text and not html:
            return people

        clean_cname = company_name.split("|")[0].split("-")[0].strip()

        # 1. Regex Pattern: Name followed by title (e.g., "Elon Musk, CEO and Product Architect")
        p1 = re.compile(
            r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})\b\s*[\–\-,\:\(]{1,3}\s*(" + TITLE_PATTERN + r"[A-Za-z0-9\s,\&]{0,40})",
            re.IGNORECASE
        )
        for match in p1.finditer(text):
            name = match.group(1).strip()
            name = re.sub(r"(Co|Founder|Ceo)$", "", name, flags=re.IGNORECASE).strip()
            name = re.sub(r"^(desk\s+of|from\s+the\s+desk\s+of)\s*", "", name, flags=re.IGNORECASE).strip()
            title = match.group(2).strip().rstrip(".,)")
            t_low = title.lower()
            if "founder" in t_low and "ceo" in t_low:
                clean_title = "Co-Founder & CEO"
            elif "founder" in t_low or "co-founder" in t_low:
                clean_title = "Founder"
            elif "ceo" in t_low:
                clean_title = "Chief Executive Officer"
            elif "cto" in t_low:
                clean_title = "Chief Technology Officer"
            elif "coo" in t_low:
                clean_title = "Chief Operating Officer"
            elif "cfo" in t_low:
                clean_title = "Chief Financial Officer"
            elif "president" in t_low:
                clean_title = "President"
            elif "director" in t_low:
                clean_title = "Executive Director"
            elif "vp" in t_low or "vice president" in t_low:
                clean_title = "Vice President"
            else:
                clean_title = title[:30].title()

            if KeyPeopleExtractor._is_valid_person_name(name, clean_cname):
                n_lower = name.lower()
                if n_lower not in seen_names:
                    seen_names.add(n_lower)
                    link_u = f"https://www.linkedin.com/search/results/all/?keywords={quote(name + ' ' + clean_cname)}"
                    people.append({
                        "name": name,
                        "title": clean_title,
                        "linkedin_search_url": link_u,
                        "linkedin_url": link_u
                    })

        # 2. Regex Pattern: Title followed by Name (e.g., "CEO: Jane Doe", "Founder - Alex Smith")
        p2 = re.compile(
            r"\b(" + TITLE_PATTERN + r")\s*[\–\-,\:]\s*\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})\b",
            re.IGNORECASE
        )
        for match in p2.finditer(text):
            title = match.group(1).strip()
            name = match.group(2).strip()
            name = re.sub(r"(Co|Founder|Ceo)$", "", name, flags=re.IGNORECASE).strip()
            name = re.sub(r"^(desk\s+of|from\s+the\s+desk\s+of)\s*", "", name, flags=re.IGNORECASE).strip()
            t_low = title.lower()
            if "founder" in t_low and "ceo" in t_low:
                clean_title = "Co-Founder & CEO"
            elif "founder" in t_low or "co-founder" in t_low:
                clean_title = "Founder"
            elif "ceo" in t_low:
                clean_title = "Chief Executive Officer"
            elif "cto" in t_low:
                clean_title = "Chief Technology Officer"
            elif "coo" in t_low:
                clean_title = "Chief Operating Officer"
            elif "cfo" in t_low:
                clean_title = "Chief Financial Officer"
            elif "president" in t_low:
                clean_title = "President"
            elif "director" in t_low:
                clean_title = "Executive Director"
            elif "vp" in t_low or "vice president" in t_low:
                clean_title = "Vice President"
            else:
                clean_title = title[:30].title()

            if KeyPeopleExtractor._is_valid_person_name(name, clean_cname):
                n_lower = name.lower()
                if n_lower not in seen_names:
                    seen_names.add(n_lower)
                    link_u = f"https://www.linkedin.com/search/results/all/?keywords={quote(name + ' ' + clean_cname)}"
                    people.append({
                        "name": name,
                        "title": clean_title,
                        "linkedin_search_url": link_u,
                        "linkedin_url": link_u
                    })

        # 3. Extract public LinkedIn profile URLs in HTML (e.g., href="https://linkedin.com/in/john-doe")
        if html:
            linkedin_links = re.findall(r'href=["\'](https?://(?:www\.)?linkedin\.com/in/[a-zA-Z0-9\-_]+)["\']', html)
            for link in set(linkedin_links):
                # Extract slug as candidate name
                slug = link.rstrip("/").split("/")[-1]
                slug_name = " ".join([w.capitalize() for w in slug.split("-") if not w.isdigit() and len(w) > 1])
                if KeyPeopleExtractor._is_valid_person_name(slug_name, clean_cname):
                    n_lower = slug_name.lower()
                    if n_lower not in seen_names:
                        seen_names.add(n_lower)
                        people.append({
                            "name": slug_name,
                            "title": "Executive / Leadership",
                            "linkedin_search_url": link
                        })

        return people[:8]

    @staticmethod
    def extract_from_linkedin_search_snippets(
        snippets: List[Dict[str, Any]],
        company_name: str
    ) -> List[Dict[str, Any]]:
        """Extract key personnel from SearXNG/LinkedIn search snippet results."""
        people: List[Dict[str, Any]] = []
        seen = set()
        clean_cname = company_name.split("|")[0].split("-")[0].strip()

        for s in snippets:
            title_text = s.get("title", "").strip()
            snippet_text = s.get("snippet", "").strip()
            url = s.get("url", "").strip()
            combined = f"{title_text} {snippet_text}"

            extracted_name = None
            extracted_title = "Executive / Leadership"

            # 1. Direct Pattern: "Firstname Lastname - Role / Title - Company | LinkedIn"
            m1 = re.search(r"^([A-Z][a-zA-Z\.\-]+(?:\s+[A-Z][a-zA-Z\.\-]+){1,2})\s*[\-–\|:]\s*(.+?)(?:\s*[\-–\|:]\s*(?:LinkedIn|at\s|[\w\s]+LinkedIn).*|\s*\|\s*LinkedIn.*|\s*$)", title_text, re.IGNORECASE)
            if m1:
                cand_name = m1.group(1).strip()
                cand_title = m1.group(2).strip()
                # Clean company suffix from title if present
                cand_title = re.sub(r"\s*[\-–\|]\s*" + re.escape(clean_cname) + r".*$", "", cand_title, flags=re.IGNORECASE)
                cand_title = re.sub(r"\s*[\-–\|]\s*LinkedIn.*$", "", cand_title, flags=re.IGNORECASE).strip()
                if KeyPeopleExtractor._is_valid_person_name(cand_name, clean_cname):
                    extracted_name = cand_name
                    if cand_title and len(cand_title) >= 3:
                        extracted_title = cand_title

            # 2. LinkedIn direct profile match: "Firstname Lastname | LinkedIn"
            if not extracted_name:
                m2 = re.search(r"^([A-Z][a-zA-Z\.\-]+(?:\s+[A-Z][a-zA-Z\.\-]+){1,2})\s*\|\s*LinkedIn", title_text, re.IGNORECASE)
                if m2:
                    cand_name = m2.group(1).strip()
                    if KeyPeopleExtractor._is_valid_person_name(cand_name, clean_cname):
                        extracted_name = cand_name
                        extracted_title = "Co-Founder / Executive"

            # 3. URL slug extraction for linkedin.com/in/*
            if not extracted_name and "linkedin.com/in/" in url:
                slug = url.rstrip("/").split("/in/")[-1].split("?")[0].split("/")[0]
                slug_words = [w.capitalize() for w in slug.split("-") if not w.isdigit() and len(w) > 1 and not re.match(r"^[0-9a-f]{5,}$", w)]
                if 2 <= len(slug_words) <= 3:
                    cand_name = " ".join(slug_words)
                    if KeyPeopleExtractor._is_valid_person_name(cand_name, clean_cname):
                        extracted_name = cand_name
                        extracted_title = "Key Decision Maker"

            # 4. Fallback search across combined snippet text
            if not extracted_name:
                match = re.search(r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})\s*[\-|\–]\s*([A-Za-z0-9\s,\&]{3,40}(?:CEO|President|Director|Chief|Founder|Executive|Manager|VP)[A-Za-z0-9\s,\&]*)", combined)
                if match:
                    cand_name = match.group(1).strip()
                    cand_title = match.group(2).strip()
                    if KeyPeopleExtractor._is_valid_person_name(cand_name, clean_cname):
                        extracted_name = cand_name
                        extracted_title = cand_title.title()

            if extracted_name:
                n_lower = extracted_name.lower()
                if n_lower not in seen:
                    seen.add(n_lower)
                    link_url = url if "linkedin.com/in/" in url else f"https://www.linkedin.com/search/results/all/?keywords={quote(extracted_name + ' ' + clean_cname)}"
                    people.append({
                        "name": extracted_name,
                        "title": extracted_title.title(),
                        "linkedin_search_url": link_url,
                        "linkedin_url": link_url
                    })

        return people[:6]

    @staticmethod
    def _is_valid_person_name(name: str, company_name: str) -> bool:
        if not name or len(name) < 4 or len(name) > 35:
            return False
        words = name.split()
        if len(words) < 2 or len(words) > 4:
            return False
        
        # Block non-person words
        stopwords = {
            "about", "contact", "home", "privacy", "terms", "policy", "company", "careers",
            "login", "sign", "register", "services", "products", "solutions", "features",
            "support", "sales", "news", "blog", "press", "media", "overview", "global",
            "world", "bank", "open", "data", "github", "desktop", "qatar", "airways",
            "what", "is", "how", "why", "story", "stories", "success", "founders", "founder",
            "leadership", "overview", "business", "model", "forbes", "aws", "cloud",
            "software", "platform", "official", "portal", "website", "inc", "corp", "llc"
        }
        
        if any(w.lower() in stopwords for w in words):
            return False
        if company_name and name.lower() == company_name.lower():
            return False
        if company_name and company_name.lower() in name.lower():
            return False
        return True

key_people_extractor = KeyPeopleExtractor()
