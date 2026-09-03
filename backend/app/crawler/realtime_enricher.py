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

        async def fetch_one(url: str) -> Optional[Dict[str, Any]]:
            try:
                from crawl4ai import AsyncWebCrawler, CrawlerRunConfig, CacheMode
                config = CrawlerRunConfig(
                    cache_mode=CacheMode.BYPASS,
                    word_count_threshold=5,
                    page_timeout=4000,
                    verbose=False
                )
                async with AsyncWebCrawler(verbose=False) as crawler:
                    res = await crawler.arun(url=url, config=config)
                    if res and res.success:
                        text_c = res.markdown or res.cleaned_html or ""
                        html_c = res.html or ""
                        if text_c:
                            return {
                                "url": url,
                                "text": text_c,
                                "html": html_c,
                                "word_count": len(text_c.split())
                            }
            except Exception:
                pass

            # Fast httpx fallback if AsyncWebCrawler runner skips URL
            try:
                import httpx
                headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
                async with httpx.AsyncClient(timeout=3.0, follow_redirects=True, headers=headers) as client:
                    resp = await client.get(url)
                    if resp.status_code == 200 and resp.text:
                        return {
                            "url": url,
                            "text": resp.text,
                            "html": resp.text,
                            "word_count": len(resp.text.split())
                        }
            except Exception:
                pass
            return None

        try:
            tasks = [fetch_one(u) for u in urls_to_crawl]
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
                        "http_status": 200,
                        "word_count": res_item["word_count"]
                    })
        except Exception as e:
            logger.warning(f"[Crawl4AI Realtime] Concurrent gather notice: {e}")

        combined_text = "\n\n".join(crawled_texts)
        combined_html = "\n\n".join(crawled_htmls)

        # 1. Real Verified Email Extraction
        emails = self.extract_real_emails(combined_text, clean_domain)

        # 2. Real Headquarters Extraction
        headquarters = self.extract_real_headquarters(combined_text)

        # 3. Real Decision Makers Extraction
        decision_makers = self.extract_real_decision_makers(combined_text, combined_html, c_name, clean_domain)

        logger.info(
            f"✅ [Crawl4AI Realtime] {clean_domain} -> "
            f"Emails: {len(emails)} | HQ: {headquarters or 'Not Found'} | Decision Makers: {len(decision_makers)}"
        )

        return {
            "domain": clean_domain,
            "verified_emails": emails,
            "headquarters": headquarters,
            "decision_makers": decision_makers,
            "crawled_subpages": crawled_subpages
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
                if len(loc) >= 4 and not any(junk in loc_lower for junk in ["privacy", "terms", "copyright", "rights"] + MONTHS_AND_DATES):
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
