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
    def clean_company_name(company_name: str) -> str:
        """Strip taglines, slogans, and corporate suffixes to extract the core brand name."""
        if not company_name:
            return "Company"
        # Split on hyphen, en-dash, em-dash, pipe, colon, bullet
        parts = [p.strip() for p in re.split(r'[\-–—|:•]', company_name) if p.strip()]
        if parts:
            first = parts[0]
            words = first.split()
            if len(words) <= 3:
                return first
            return " ".join(words[:2])
        words = company_name.strip().split()
        return " ".join(words[:2]) if len(words) > 3 else company_name.strip()

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

        clean_cname = KeyPeopleExtractor.clean_company_name(company_name)

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
            elif "co-ceo" in t_low or "co ceo" in t_low:
                clean_title = "Co-Founder & Co-CEO"
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
                    # No hallucinated LinkedIn profile URL from text
                    people.append({
                        "name": name,
                        "title": clean_title,
                        "company_name": clean_cname,
                        "linkedin_url": None,
                        "source_type": "official_page_text",
                        "evidence": match.group(0).strip()[:200]
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
                    people.append({
                        "name": name,
                        "title": clean_title,
                        "company_name": clean_cname,
                        "linkedin_url": None,
                        "source_type": "official_page_text",
                        "evidence": match.group(0).strip()[:200]
                    })

        # 3. Extract public LinkedIn profile URLs in HTML (e.g., href="https://linkedin.com/in/john-doe")
        if html:
            linkedin_links = re.findall(r'href=["\'](https?://(?:www\.)?linkedin\.com/in/[a-zA-Z0-9\-_]+)["\']', html)
            for link in set(linkedin_links):
                # Extract slug as candidate name
                slug = link.rstrip("/").split("/")[-1].split("?")[0]
                slug_name = " ".join([w.capitalize() for w in slug.split("-") if not w.isdigit() and len(w) > 1])
                if KeyPeopleExtractor._is_valid_person_name(slug_name, clean_cname):
                    n_lower = slug_name.lower()
                    if n_lower not in seen_names:
                        seen_names.add(n_lower)
                        clean_profile_url = f"https://www.linkedin.com/in/{slug}"
                        people.append({
                            "name": slug_name,
                            "title": "Executive / Leadership",
                            "company_name": clean_cname,
                            "linkedin_url": clean_profile_url,
                            "source_type": "official_page_linkedin_link",
                            "evidence": f"Official website link: {clean_profile_url}"
                        })

        return people[:8]

    @staticmethod
    def extract_company_linkedin_url(
        html: str,
        text: str = "",
        company_name: str = "",
        domain: str = ""
    ) -> Optional[str]:
        """Extract official corporate LinkedIn company page URL (linkedin.com/company/<slug>)."""
        combined = f"{html or ''} {text or ''}"
        if not combined:
            return None

        # 1. Direct regex for linkedin.com/company/<slug>
        matches = re.findall(r'https?://(?:www\.)?linkedin\.com/company/([a-zA-Z0-9\-_]+)', combined, re.IGNORECASE)
        ignored_slugs = {"linkedin", "home", "sharearticle", "sharing", "login", "signup", "jobs", "feed", "posts"}
        for slug in matches:
            slug_clean = slug.strip("/?#").lower()
            if slug_clean and slug_clean not in ignored_slugs and len(slug_clean) >= 2:
                return f"https://www.linkedin.com/company/{slug_clean}"

        # 2. Also support linkedin.com/school/ for educational/institutional companies
        school_matches = re.findall(r'https?://(?:www\.)?linkedin\.com/school/([a-zA-Z0-9\-_]+)', combined, re.IGNORECASE)
        for slug in school_matches:
            slug_clean = slug.strip("/?#").lower()
            if slug_clean and slug_clean not in ignored_slugs and len(slug_clean) >= 2:
                return f"https://www.linkedin.com/school/{slug_clean}"

        return None

    @staticmethod
    async def find_company_linkedin_via_search(company_name: str, domain: str = "") -> Optional[str]:
        """Verify and discover corporate LinkedIn presence via search query."""
        from app.crawler.searxng_service import searxng_service
        clean_cname = KeyPeopleExtractor.clean_company_name(company_name)
        queries = [
            f'site:linkedin.com/company "{clean_cname}"',
        ]
        if domain:
            clean_dom = domain.replace("https://", "").replace("http://", "").rstrip("/").replace("www.", "").split("/")[0]
            if clean_dom and clean_dom not in clean_cname.lower():
                queries.append(f'site:linkedin.com/company "{clean_dom}"')

        for q in queries:
            try:
                results, _, _ = await searxng_service.search_with_meta(query=q, max_results=4)
                for res in results:
                    u = res.get("url", "")
                    m = re.search(r'https?://(?:www\.)?linkedin\.com/company/([a-zA-Z0-9\-_]+)', u, re.IGNORECASE)
                    if m:
                        slug = m.group(1).lower().strip("/?#")
                        if slug not in {"linkedin", "home", "sharearticle", "sharing", "login", "signup", "jobs", "feed", "posts"}:
                            title_snip = f"{res.get('title', '')} {res.get('snippet', '')}".lower()
                            cname_words = [w.lower() for w in clean_cname.split() if len(w) >= 3]
                            if not cname_words or any(w in title_snip or w in slug for w in cname_words) or (domain and domain.split(".")[0].lower() in slug):
                                return f"https://www.linkedin.com/company/{slug}"
            except Exception as e:
                logger.debug(f"Search company linkedin notice on '{q}': {e}")
        return None

    @staticmethod
    def _normalize_executive_title(raw_title: str) -> Optional[str]:
        """Normalize to a genuine executive role. Return None if not an executive title."""
        if not raw_title or len(raw_title) < 2:
            return None
        t_low = raw_title.lower()
        if "founder" in t_low and "ceo" in t_low:
            return "Co-Founder & CEO"
        elif "co-ceo" in t_low or "co ceo" in t_low:
            return "Co-Founder & Co-CEO"
        elif "co-founder" in t_low or "cofounder" in t_low:
            return "Co-Founder"
        elif "founder" in t_low:
            return "Founder"
        elif "ceo" in t_low or "chief executive" in t_low:
            return "Chief Executive Officer"
        elif "cto" in t_low or "chief technology" in t_low or "chief technical" in t_low:
            return "Chief Technology Officer"
        elif "coo" in t_low or "chief operating" in t_low:
            return "Chief Operating Officer"
        elif "cfo" in t_low or "chief financial" in t_low:
            return "Chief Financial Officer"
        elif "president" in t_low:
            return "President"
        elif "managing director" in t_low:
            return "Managing Director"
        elif "executive director" in t_low or "director" in t_low:
            return "Director"
        elif "vice president" in t_low or re.search(r"\bvp\b", t_low):
            return "Vice President"
        elif "head of" in t_low:
            m = re.search(r"\b(head of [a-z\s]+)", t_low)
            return m.group(1).title() if m else "Head of Department"
        elif "managing partner" in t_low or "general partner" in t_low:
            return "Managing Partner"
        return None

    @staticmethod
    def extract_from_linkedin_search_snippets(
        snippets: List[Dict[str, Any]],
        company_name: str
    ) -> List[Dict[str, Any]]:
        """Extract key personnel from SearXNG/LinkedIn search snippet results."""
        people: List[Dict[str, Any]] = []
        seen = set()
        clean_cname = KeyPeopleExtractor.clean_company_name(company_name)

        for s in snippets:
            title_text = s.get("title", "").strip()
            snippet_text = s.get("snippet", "").strip()
            url = s.get("url", "").strip()
            combined = f"{title_text} {snippet_text}"

            extracted_name = None
            extracted_title = None

            # 1. Direct Pattern: "Firstname Lastname - Role / Title - Company | LinkedIn"
            m1 = re.search(r"^([A-Za-z\.\-]+(?:\s+[A-Za-z\.\-]+){1,2})\s*[\-–—\|:]\s*(.+?)(?:\s*[\-–—\|:]\s*(?:LinkedIn|at\s|[\w\s]+LinkedIn).*|\s*\|\s*LinkedIn.*|\s*$)", title_text, re.IGNORECASE)
            if m1:
                cand_name = m1.group(1).strip().title()
                cand_title = m1.group(2).strip()
                cand_title = re.sub(r"\s*[\-–—\|]\s*" + re.escape(clean_cname) + r".*$", "", cand_title, flags=re.IGNORECASE)
                cand_title = re.sub(r"\s+(?:at|@)\s+" + re.escape(clean_cname) + r".*$", "", cand_title, flags=re.IGNORECASE)
                cand_title = re.sub(r"\s*[\-–—\|]\s*LinkedIn.*$", "", cand_title, flags=re.IGNORECASE).strip()
                norm_title = KeyPeopleExtractor._normalize_executive_title(cand_title)
                if not norm_title and ("founder" in combined.lower() or "ceo" in combined.lower()):
                    norm_title = KeyPeopleExtractor._normalize_executive_title(combined)
                if norm_title and KeyPeopleExtractor._is_valid_person_name(cand_name, clean_cname):
                    extracted_name = cand_name
                    extracted_title = norm_title

            # 2. LinkedIn direct profile match: "Firstname Lastname | LinkedIn"
            if not extracted_name and "linkedin.com/in/" in url:
                m2 = re.search(r"^([A-Za-z\.\-]+(?:\s+[A-Za-z\.\-]+){1,2})\s*[\-–—\|:]\s*LinkedIn", title_text, re.IGNORECASE)
                if m2:
                    cand_name = m2.group(1).strip().title()
                    if KeyPeopleExtractor._is_valid_person_name(cand_name, clean_cname):
                        extracted_name = cand_name
                        extracted_title = KeyPeopleExtractor._normalize_executive_title(combined) or "Founder / Executive"

            # 3. URL slug extraction for linkedin.com/in/*
            if not extracted_name and "linkedin.com/in/" in url:
                slug = url.rstrip("/").split("/in/")[-1].split("?")[0].split("/")[0]
                slug_words = [w.capitalize() for w in slug.split("-") if not w.isdigit() and len(w) > 1 and not re.match(r"^[0-9a-f]{5,}$", w)]
                if 2 <= len(slug_words) <= 3:
                    cand_name = " ".join(slug_words).title()
                    if KeyPeopleExtractor._is_valid_person_name(cand_name, clean_cname):
                        extracted_name = cand_name
                        extracted_title = KeyPeopleExtractor._normalize_executive_title(combined) or "Key Decision Maker"

            # 4. Fallback search across combined snippet text
            if not extracted_name:
                match = re.search(r"([A-Za-z]+(?:\s+[A-Za-z]+){1,2})\s*[\-–—\|]\s*([A-Za-z0-9\s,\&]{3,40}(?:CEO|President|Director|Chief|Founder|Executive|Manager|VP)[A-Za-z0-9\s,\&]*)", combined)
                if match:
                    cand_name = match.group(1).strip().title()
                    cand_title = match.group(2).strip()
                    norm_title = KeyPeopleExtractor._normalize_executive_title(cand_title)
                    if norm_title and KeyPeopleExtractor._is_valid_person_name(cand_name, clean_cname):
                        extracted_name = cand_name
                        extracted_title = norm_title

            if extracted_name and extracted_title:
                n_lower = extracted_name.lower()
                if n_lower not in seen:
                    # Enforce Person-Company Association:
                    # Require that snippet or title contains reference to company brand
                    clean_c_words = [w.lower() for w in clean_cname.split() if len(w) >= 3 and w.lower() not in {"the", "and", "inc", "ltd", "corp", "llc"}]
                    has_company_evidence = any(cw in combined.lower() for cw in clean_c_words) if clean_c_words else True
                    
                    if not has_company_evidence and clean_cname.lower() not in combined.lower():
                        logger.debug(f"[KeyPeopleExtractor] Skipping candidate {extracted_name}: no company association with '{clean_cname}'")
                        continue

                    seen.add(n_lower)

                    # Direct LinkedIn profile URL strictly if genuine linkedin.com/in/<slug>
                    direct_match = re.search(r'https?://(?:www\.)?linkedin\.com/in/([a-zA-Z0-9\-_]+)', f"{url} {combined}")
                    slug = None
                    if direct_match:
                        slug = direct_match.group(1).lower()
                    elif "linkedin.com/in/" in url:
                        slug = url.rstrip("/").split("/in/")[-1].split("?")[0].split("/")[0].lower()

                    bad_slugs = {"search", "jobs", "feed", "login", "signup", "home", "pub", "in", "sharing", "posts"}
                    if slug and slug not in bad_slugs and len(slug) >= 2:
                        real_profile_url = f"https://www.linkedin.com/in/{slug}"
                    else:
                        real_profile_url = None

                    evidence_snippet = f"{title_text} — {snippet_text}".strip()[:300]
                    people.append({
                        "name": extracted_name,
                        "title": extracted_title,
                        "company_name": clean_cname,
                        "linkedin_url": real_profile_url,
                        "source_url": url,
                        "source_type": "linkedin_search_result" if real_profile_url else "web_search_snippet",
                        "evidence": evidence_snippet,
                        "confidence_score": 0.90 if real_profile_url else 0.75
                    })

        return people[:6]

    @staticmethod
    def _is_valid_person_name(name: str, company_name: str) -> bool:
        if not name or len(name) < 4 or len(name) > 35:
            return False
        words = name.split()
        if len(words) < 2 or len(words) > 4:
            return False

        # Every word must look like a capitalized name component
        for w in words:
            if not re.match(r"^[A-Z][a-zA-Z\.\-']{1,20}$", w):
                return False

        # Block non-person words, apps, products, prepositions, web junk
        stopwords = {
            "about", "contact", "home", "privacy", "terms", "policy", "company", "careers",
            "login", "sign", "register", "services", "products", "solutions", "features",
            "support", "sales", "news", "blog", "press", "media", "overview", "global",
            "world", "bank", "open", "data", "github", "desktop", "qatar", "airways",
            "what", "is", "how", "why", "story", "stories", "success", "founders", "founder",
            "leadership", "overview", "business", "model", "forbes", "aws", "cloud",
            "software", "platform", "official", "portal", "website", "inc", "corp", "llc",
            "university", "academy", "college", "school", "developer", "developers", "dev",
            "app", "apps", "application", "applications", "apk", "help", "guide", "download",
            "downloads", "introducing", "introduction", "free", "shipping", "price", "buy",
            "online", "shop", "store", "product", "products", "project", "projects", "video",
            "videos", "channel", "awards", "award", "design", "designs", "results", "records",
            "search", "case", "court", "federal", "pacer", "unicourt", "lotto", "api", "apis",
            "web", "tutorial", "tutorials", "course", "courses", "forum", "community", "tv",
            "youtube", "google", "android", "ios", "apple", "microsoft", "windows", "linux",
            "mac", "log", "in", "up", "out", "cart", "checkout", "long", "before", "after",
            "while", "during", "best", "top", "profitable", "ideas", "ways", "tools", "near",
            "me", "our", "team", "people", "management", "executive", "board", "investors",
            "partners", "advisor", "advisors", "consultant", "group", "holdings", "ltd",
            "ceo", "cto", "cfo", "coo", "chief", "president", "director", "manager", "officer",
            "vice", "vp", "lead", "head", "and", "or", "of", "the", "co-founder", "cofounder"
        }

        if any(w.lower() in stopwords for w in words):
            return False

        # Reject if name contains the company name or any significant word from company name
        clean_c = re.sub(r"[^\w\s]", "", company_name).lower() if company_name else ""
        c_words = [cw for cw in clean_c.split() if len(cw) >= 3 and cw not in {"the", "and", "inc", "ltd", "com", "net", "org"}]
        name_lower = name.lower()
        if any(cw in name_lower for cw in c_words):
            return False

        return True

key_people_extractor = KeyPeopleExtractor()
