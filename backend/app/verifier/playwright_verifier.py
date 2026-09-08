"""
Playwright Real-Browser Verification Engine — §9 of Master Rules

Executes live headless Chromium browser checks for candidate company websites:
- Live URL redirect resolution
- DOM network readiness probe
- Parked domain detection (multi-signal context, avoiding false positives on "coming soon")
- Live rendered text & identity signal extraction (emails, addresses, HQ, key personnel links)
"""
import logging
import re
from typing import Dict, Any, List, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Strong parked/dead domain indicators (must match multiple or explicit domain seller patterns)
EXPLICIT_PARKED_SIGNALS = [
    "this domain is for sale", "buy this domain", "domain name is available",
    "hugedomains.com", "sedo.com", "godaddy.com/domainsearch", "dan.com",
    "namecheap.com/domains", "domain market"
]

class PlaywrightVerifier:
    def verify_url(self, url: str, timeout_ms: int = 15000) -> Dict[str, Any]:
        """
        Execute live Playwright browser verification for a target website.
        """
        if not url or not url.startswith("http"):
            return {
                "is_verified": False,
                "status": "VERIFICATION_FAILED",
                "reason": "Invalid target URL",
                "final_url": url,
                "http_status": 0,
            }

        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            logger.warning("[Playwright Verifier] Playwright library not installed. Falling back.")
            return {
                "is_verified": False,
                "status": "VERIFICATION_FAILED",
                "reason": "Playwright library unavailable",
                "final_url": url,
                "http_status": 0,
            }

        logger.info(f"🌐 [Playwright Verifier] Launching headless browser for: {url}")
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
                )
                context = browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                    viewport={"width": 1280, "height": 800}
                )
                page = context.new_page()

                # Track HTTP response status
                response_status = 200
                def handle_response(response):
                    nonlocal response_status
                    if response.url == url or response.url == page.url:
                        response_status = response.status

                page.on("response", handle_response)

                try:
                    response = page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                    if response:
                        response_status = response.status
                except Exception as goto_err:
                    logger.warning(f"[Playwright Verifier] Navigation timeout/error for {url}: {goto_err}")

                final_url = page.url or url
                page_title = page.title() or ""
                rendered_text = page.inner_text("body") if page.query_selector("body") else ""
                html_content = page.content() or ""

                browser.close()

                # ── Multi-Signal Parked / Dead Domain Analysis ─────────────────
                text_lower = rendered_text.lower()
                title_lower = page_title.lower()

                # Explicit domain parked seller check
                is_parked = any(sig in text_lower or sig in title_lower for sig in EXPLICIT_PARKED_SIGNALS)
                
                # Check for dead error pages (404, 502, 503)
                is_error_page = response_status in (404, 500, 502, 503, 504) or "404 not found" in title_lower

                # Contextual "coming soon" check (only reject if page is empty or parked, NOT if enterprise landing)
                has_coming_soon = "coming soon" in text_lower
                word_count = len(rendered_text.split())
                is_empty = word_count < 15

                is_rejected = is_parked or is_error_page or (has_coming_soon and is_empty)
                
                reject_reason = ""
                if is_parked:
                    reject_reason = "Parked domain for sale detected"
                elif is_error_page:
                    reject_reason = f"HTTP error status {response_status} or 404 page"
                elif has_coming_soon and is_empty:
                    reject_reason = "Empty placeholder / Coming soon page"

                # ── Extract Identity & Contact Evidence ───────────────────────
                emails = list(set(re.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', rendered_text)))
                # Filter out asset emails
                clean_emails = [e for e in emails if not any(x in e.lower() for x in ['.png', '.jpg', '.jpeg', 'sentry', 'example'])]

                # Extract social links (LinkedIn)
                linkedin_links = []
                if "href=" in html_content:
                    links = re.findall(r'href=["\'](https?://[^\s"\']*linkedin\.com/[^\s"\']*)["\']', html_content, re.IGNORECASE)
                    linkedin_links = list(set(links))

                is_verified = not is_rejected and (response_status == 200) and (word_count >= 15)

                logger.info(
                    f"✅ [Playwright Verifier] {url} -> Verified: {is_verified} | "
                    f"Status: {response_status} | Words: {word_count} | Emails: {len(clean_emails)}"
                )

                return {
                    "is_verified": is_verified,
                    "status": "VERIFIED" if is_verified else "VERIFICATION_FAILED",
                    "reason": "Passed Playwright live verification" if is_verified else reject_reason,
                    "final_url": final_url,
                    "http_status": response_status,
                    "page_title": page_title,
                    "word_count": word_count,
                    "extracted_emails": clean_emails,
                    "linkedin_links": linkedin_links,
                    "rendered_text_snippet": rendered_text[:1000],
                }

        except Exception as err:
            logger.error(f"[Playwright Verifier] Execution failed for {url}: {err}")
            return {
                "is_verified": False,
                "status": "VERIFICATION_FAILED",
                "reason": f"Playwright verification exception: {err}",
                "final_url": url,
                "http_status": 0,
            }


playwright_verifier = PlaywrightVerifier()
