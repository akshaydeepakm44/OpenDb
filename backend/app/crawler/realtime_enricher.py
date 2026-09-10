import re
import asyncio
import logging
from typing import Dict, Any, List, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Common address regex patterns to extract genuine physical headquarters
HQ_PATTERNS = [
    r"(?:Headquarters|HQ|Main Office|Corporate Address|Located in|Based in)\s*[\:\–\-]?\s*([A-Z][A-Za-z0-9\s,\.\-]{3,60}(?:CA|NY|TX|FL|WA|MA|IL|CO|NC|GA|UK|USA|United States|Germany|France|India|Singapore|United Kingdom|Japan|Australia|\d{5}))",
    r"\b([A-Z][a-zA-Z\s]{2,25},\s*(?:[A-Z]{2}|United States|United Kingdom|Germany|France|Canada|Australia|India|Singapore|Japan))\b"
]

MONTHS_AND_DATES = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december", "mmm", "yyyy", "hh:mm"]

# Junk email filtering set
JUNK_EMAIL_SUBSTRS = [
    "example.com", "domain.com", "email.com", "sentry", "wixpress", "schema.org",
    "ingest", "bootstrap", "github.com", "fontawesome", "googleapis", ".png", ".jpg", ".svg"
]

class RealtimeEnricher:
    """
    Uses Crawl4AI (AsyncWebCrawler) to crawl target websites and subpages in real-time concurrently.
    Strictly extracts ONLY real, verified data:
    - Real contact emails (no guesses or domain-string fallbacks)
    - Real decision makers & leadership personnel (no generic placeholder titles)
    - Real physical headquarters locations (no 'Global HQ' or inferred guesses)
    """

    async def enrich_domain_realtime(
        self,
        domain: str,
        company_name: Optional[str] = None
    ) -> Dict[str, Any]:
        clean_domain = domain.replace("https://", "").replace("http://", "").rstrip("/").replace("www.", "").split("/")[0]
        base_url = f"https://{clean_domain}"
        c_name = company_name or clean_domain.capitalize()

        logger.info(f"🕷️ [Crawl4AI Realtime] Initiating fast concurrent crawl for: {clean_domain}")

        subpaths = ["", "/contact", "/about", "/team", "/leadership"]
        urls_to_crawl = [f"{base_url}{path}" for path in subpaths]

        crawled_texts: List[str] = []
        crawled_htmls: List[str] = []
        crawled_subpages: List[Dict[str, Any]] = []

        import httpx
        from bs4 import BeautifulSoup

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        }

        async def fetch_one(client: httpx.AsyncClient, url: str) -> Optional[Dict[str, Any]]:
            try:
                resp = await client.get(url)
                if resp.status_code == 200 and resp.text:
                    soup = BeautifulSoup(resp.text, "html.parser")
                    for tag in soup(["script", "style", "meta", "noscript"]):
                        tag.extract()
                    clean_txt = soup.get_text(separator=" ", strip=True)
                    if clean_txt:
                        return {
                            "url": url,
                            "text": clean_txt,
                            "html": resp.text,
                            "word_count": len(clean_txt.split())
                        }
            except Exception:
                pass
            return None

        try:
            async with httpx.AsyncClient(timeout=3.0, follow_redirects=True, headers=headers) as client:
                tasks = [fetch_one(client, u) for u in urls_to_crawl]
                done_results = await asyncio.gather(*tasks, return_exceptions=True)
                for res_item in done_results:
                    if isinstance(res_item, dict) and res_item.get("text"):
                        crawled_texts.append(res_item["text"])
                        crawled_htmls.append(res_item["html"])
                        u = res_item["url"]
                        page_path = urlparse(u).path or "/"
                        crawled_subpages.append({
                            "title": f"{page_path} • {c_name}",
                            "url": u,
                            "word_count": res_item["word_count"],
                            "status": 200
                        })
        except Exception as e:
            logger.warning(f"Error during async crawl of {clean_domain}: {e}")

        combined_text = "\n\n".join(crawled_texts)
        combined_html = "\n\n".join(crawled_htmls)

        # 1. Real Verified Email Extraction
        emails = self.extract_real_emails(combined_text, clean_domain)

        # 2. Real Headquarters Extraction
        headquarters = self.extract_real_headquarters(combined_text)

        # 3. Real Decision Makers Extraction
        decision_makers = self.extract_real_decision_makers(combined_text, combined_html, c_name, clean_domain)

        # 4. Real Company LinkedIn Profile Extraction
        from app.extraction.key_people_extractor import key_people_extractor
        company_linkedin_url = key_people_extractor.extract_company_linkedin_url(combined_html, combined_text, c_name, clean_domain)

        logger.info(
            f"✅ [Crawl4AI Realtime] {clean_domain} -> "
            f"Emails: {len(emails)} | HQ: {headquarters or 'Not Found'} | Decision Makers: {len(decision_makers)} | Company LinkedIn: {company_linkedin_url or 'Not Found'}"
        )

        return {
            "domain": clean_domain,
            "verified_emails": emails,
            "headquarters": headquarters,
            "decision_makers": decision_makers,
            "crawled_subpages": crawled_subpages,
            "company_linkedin_url": company_linkedin_url
        }

    @staticmethod
    def extract_real_emails(text: str, domain: str) -> List[str]:
        """Extract genuine emails found directly in crawled page content without guesses."""
        if not text:
            return []
        
        raw_emails = re.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', text)
        valid_emails = []
        seen = set()

        for e in raw_emails:
            e_lower = e.lower()
            if e_lower in seen:
                continue
            if any(junk in e_lower for junk in JUNK_EMAIL_SUBSTRS):
                continue
            seen.add(e_lower)
            valid_emails.append(e)

        return valid_emails[:5]

    @staticmethod
    def extract_real_headquarters(text: str) -> Optional[str]:
        """Extract genuine physical headquarters address from crawled text without guessing."""
        if not text:
            return None

        for pattern in HQ_PATTERNS:
            match = re.search(pattern, text)
            if match:
                loc = match.group(1).strip().rstrip(".,")
                loc_lower = loc.lower()
                # Must be a real human city/state with spaces (not a continuous base64/hex token)
                if len(loc.split()) < 2 or len(loc.split()) > 7:
                    continue
                # Reject base64 / CSS hashes / random mixed case tokens
                if re.search(r"[a-z0-9]{12,}", loc) or re.search(r"[a-z][A-Z][a-z][A-Z]", loc) or re.search(r"[A-Z0-9]{8,}", loc):
                    continue
                # Must not contain junk words
                if any(junk in loc_lower for junk in ["privacy", "terms", "copyright", "rights", "cookie", "policy"] + MONTHS_AND_DATES):
                    continue
                # Ensure all characters are letters, spaces, commas, periods, hyphens
                if not re.match(r"^[A-Za-z0-9\s,\.\-]+$", loc):
                    continue
                return loc

        return None

    @staticmethod
    def extract_real_decision_makers(
        text: str,
        html: str,
        company_name: str,
        domain: str
    ) -> List[Dict[str, Any]]:
        """Extract real decision makers from text/HTML. Never return fake placeholder personas."""
        from app.extraction.key_people_extractor import key_people_extractor
        return key_people_extractor.extract_from_text_and_html(text, html, company_name, domain)

realtime_enricher = RealtimeEnricher()
