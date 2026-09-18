import os
import sys
import asyncio
import logging
import time
from typing import List, Dict, Any, Set
from urllib.parse import urlparse
from bs4 import BeautifulSoup

# Ensure Windows asyncio event loop policy for Crawl4AI/Playwright
if sys.platform == 'win32':
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    except Exception:
        pass

from app.crawler.url_discovery import url_discovery
from app.crawler.resource_discovery import resource_discovery
from app.normalization.normalizer import normalizer
from app.storage.file_storage import file_storage
from app.crawler.distributed_slot_manager import slot_manager
from app.safety.resource_governor import governor
from app.config import settings

logger = logging.getLogger(__name__)

# Atomic tracker for open browser contexts in current process
_ACTIVE_BROWSER_CONTEXTS: int = 0
_CONTEXT_LOCK = asyncio.Lock() if hasattr(asyncio, "Lock") else None

def get_active_browser_contexts_count() -> int:
    return _ACTIVE_BROWSER_CONTEXTS

PRIORITY_PATH_HINTS = (
    "about", "team", "leadership", "management", "who-we-are",
    "contact", "company", "our-story", "founders", "board",
    "management-team", "our-people", "meet-the-team"
)

NOISE_PATH_HINTS = (
    "/cart", "/checkout", "/wishlist", "/login", "/register",
    "/product/", "/category/", "/boutique/", "/shop/", "compare"
)

def _score_link(url: str, link_text: str) -> int:
    """Lower score = crawled first. 0 = high-value info page, 2 = likely noise."""
    combined = f"{url} {link_text}".lower()
    if any(h in combined for h in PRIORITY_PATH_HINTS):
        return 0
    if any(h in combined for h in NOISE_PATH_HINTS):
        return 2
    return 1

class CrawlResultItem:
    def __init__(
        self,
        url: str,
        title: str,
        html_content: str,
        markdown: str,
        text: str,
        http_status: int,
        content_type: str,
        links: List[Dict[str, str]],
        media: List[Dict[str, str]],
        metadata: Dict[str, Any]
    ):
        self.url = url
        self.title = title
        self.html_content = html_content
        self.markdown = markdown
        self.text = text
        self.http_status = http_status
        self.content_type = content_type
        self.links = links
        self.media = media
        self.metadata = metadata

class CrawlerService:
    def __init__(self):
        pass

    async def crawl_site(
        self,
        starting_url: str,
        max_depth: int = 2,
        max_pages: int = 4,
        concurrency: int = 2,
        slot_type: str = "standard",
        progress_callback=None
    ) -> List[CrawlResultItem]:
        global _ACTIVE_BROWSER_CONTEXTS
        norm_start_url = normalizer.normalize_url(starting_url)
        if not norm_start_url:
            raise ValueError(f"Invalid starting URL: {starting_url}")

        # 1. Proactive Resource Governor Check
        can_crawl, pause_reason = governor.can_start_crawl(slot_type=slot_type)
        if not can_crawl:
            logger.warning(f"[CrawlerService] Crawl rejected by ResourceGovernor: {pause_reason}")
            raise RuntimeError(f"CRAWL_REJECTED_BY_GOVERNOR: {pause_reason}")

        base_host = url_discovery.get_domain_host(norm_start_url)
        visited_urls: Set[str] = set()
        queue: List[Dict[str, Any]] = [{"url": norm_start_url, "depth": 0, "priority": 0}]
        results: List[CrawlResultItem] = []

        overall_timeout = float(getattr(settings, "CRAWL_TIMEOUT_SECONDS", 60))

        # 2. Acquire Global Distributed Crawl Slot (fail-closed in production)
        async with slot_manager.distributed_crawl_slot(slot_type=slot_type, wait_timeout=overall_timeout) as lease_id:
            logger.info(f"[CrawlerService] Acquired distributed slot '{lease_id}' for {starting_url}")
            try:
                from crawl4ai import AsyncWebCrawler, CrawlerRunConfig, CacheMode
                config = CrawlerRunConfig(
                    cache_mode=CacheMode.BYPASS,
                    word_count_threshold=5,
                    page_timeout=int(overall_timeout * 500),  # 30s per page
                    verbose=False
                )

                _ACTIVE_BROWSER_CONTEXTS += 1
                try:
                    async with AsyncWebCrawler(verbose=False) as crawler:
                        while queue and len(visited_urls) < max_pages:
                            current_item = queue.pop(0)
                            curr_url = current_item["url"]
                            curr_depth = current_item["depth"]

                            if curr_url in visited_urls:
                                continue

                            visited_urls.add(curr_url)

                            # ─── Safety Guardrail Pre-Crawl Checks ───
                            from app.persistence.database import SessionLocal
                            from app.safety.guardrails import is_domain_blocked, add_to_blocklist
                            from app.safety.reputation import reputation_checker

                            with SessionLocal() as db:
                                # 1. Database Blocklist Check
                                if is_domain_blocked(db, curr_url):
                                    logger.warning(f"🚫 [SAFETY PRE-CRAWL] Skipping blocked URL: {curr_url}")
                                    continue

                                # 2. Pre-crawl Robots.txt Compliance Check
                                allowed, robots_reason = reputation_checker.is_robots_allowed(curr_url)
                                if not allowed:
                                    logger.info(f"🤖 [SAFETY PRE-CRAWL] Skipping disallowed by robots.txt: {curr_url}")
                                    continue

                                # 3. Pre-crawl Threat Intel / Reputation API Check (Fail-Closed)
                                rep_safe, threat_type = await reputation_checker.check_url_reputation(curr_url)
                                if not rep_safe:
                                    logger.warning(f"⚠️ [SAFETY PRE-CRAWL] Reputation check failed/flagged for '{curr_url}': {threat_type}")
                                    add_to_blocklist(db, curr_url, reason_category="malware_phishing", source="reputation_api")
                                    continue

                            if progress_callback:
                                await progress_callback(
                                    stage="CRAWLING",
                                    pages_discovered=len(visited_urls) + len(queue),
                                    pages_crawled=len(visited_urls),
                                    current_url=curr_url
                                )

                            from app.audit.tracer import tracer, Checkpoint
                            t_crawl_start = time.time()
                            tracer.log_event(
                                level="INFO",
                                checkpoint=Checkpoint.CP18_CRAWL_EXECUTION,
                                event="CRAWL_START",
                                message=f"Crawl4AI / Playwright requesting: {curr_url} (depth={curr_depth})",
                                lead_id=base_host,
                                extra={"url": curr_url, "depth": curr_depth}
                            )

                            try:
                                html_raw = ""
                                markdown_raw = ""
                                status_code = 200
                                canonical_url = curr_url

                                crawl_timeout = 35.0
                                try:
                                    crawl_res = await asyncio.wait_for(crawler.arun(url=curr_url, config=config), timeout=crawl_timeout)
                                    if crawl_res and crawl_res.success:
                                        html_raw = crawl_res.html or ""
                                        markdown_raw = crawl_res.markdown or ""
                                        status_code = crawl_res.status_code or 200
                                        canonical_url = crawl_res.url or curr_url
                                        crawl_dur = time.time() - t_crawl_start
                                        tracer.log_event(
                                            level="DEBUG",
                                            checkpoint=Checkpoint.CP18_CRAWL_EXECUTION,
                                            event="PAGE_RESPONSE",
                                            message=f"Crawl4AI received {status_code} for {curr_url} ({len(html_raw)} chars)",
                                            lead_id=base_host,
                                            duration=crawl_dur,
                                            status="SUCCESS",
                                            extra={"url": curr_url, "status_code": status_code, "html_len": len(html_raw)}
                                        )
                                    else:
                                        raise ValueError(f"Crawl4AI returned success=False for {curr_url}")
                                except Exception as c_err:
                                    logger.warning(f"[CrawlerService] Browser crawl failed for {curr_url} ({c_err}). Invoking fast HTTP fallback...")
                                    try:
                                        import httpx
                                        fallback_headers = {
                                            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                                            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                                            "Accept-Language": "en-US,en;q=0.9",
                                        }
                                        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True, headers=fallback_headers, verify=False) as http_client:
                                            http_resp = await http_client.get(curr_url)
                                            status_code = http_resp.status_code
                                            if http_resp.text:
                                                html_raw = http_resp.text
                                                canonical_url = str(http_resp.url)
                                                markdown_raw = html_raw
                                                logger.info(f"[CrawlerService] Fast HTTP fallback succeeded for {curr_url} (HTTP {status_code}, {len(html_raw)} bytes)")
                                            else:
                                                raise ValueError(f"HTTP fallback returned empty text with status {status_code}")
                                    except Exception as http_err:
                                        crawl_dur = time.time() - t_crawl_start
                                        is_timeout = isinstance(c_err, asyncio.TimeoutError)
                                        evt_name = "CRAWL_TIMEOUT" if is_timeout else "CRAWL_FAILED"
                                        tracer.log_event(
                                            level="ERROR",
                                            checkpoint=Checkpoint.CP18_CRAWL_EXECUTION,
                                            event=evt_name,
                                            message=f"{evt_name} — Browser and HTTP fallback failed for {curr_url}: {c_err} | {http_err}",
                                            lead_id=base_host,
                                            duration=crawl_dur,
                                            status="FAILED",
                                            extra={"url": curr_url, "browser_error": str(c_err), "http_error": str(http_err)},
                                            exc_info=True
                                        )
                                        raise RuntimeError(f"CRAWL_FAILED: All crawl methods failed for {curr_url} ({c_err} | {http_err})")

                                soup = BeautifulSoup(html_raw, "html.parser")
                                title = normalizer.normalize_string(soup.title.string) if soup.title else ""
                                if not title:
                                    h1 = soup.find("h1")
                                    title = normalizer.normalize_string(h1.text) if h1 else curr_url

                                text_clean = normalizer.normalize_string(soup.get_text()) or markdown_raw
                                
                                raw_links = []
                                for a in soup.find_all("a", href=True):
                                    raw_links.append({
                                        "href": a["href"],
                                        "text": normalizer.normalize_string(a.text) or ""
                                    })

                                raw_media = []
                                for img in soup.find_all(["img", "source"], src=True):
                                    raw_media.append({
                                        "src": img["src"],
                                        "alt": normalizer.normalize_string(img.get("alt", "")) or ""
                                    })

                                # Extract Logo / Favicon URL & store in MinIO vault
                                logo_url = None
                                logo_rel_path = None
                                try:
                                    icon_link = soup.find("link", rel=lambda r: r and any(x in str(r).lower() for x in ["icon", "shortcut icon", "apple-touch-icon"]))
                                    og_image = soup.find("meta", property="og:image")
                                    logo_img = soup.find("img", alt=lambda a: a and "logo" in str(a).lower()) or soup.find("img", class_=lambda c: c and "logo" in str(c).lower()) or soup.find("img", id=lambda i: i and "logo" in str(i).lower())

                                    candidate_img_src = None
                                    if icon_link and icon_link.get("href"):
                                        candidate_img_src = icon_link["href"]
                                    elif og_image and og_image.get("content"):
                                        candidate_img_src = og_image["content"]
                                    elif logo_img and logo_img.get("src"):
                                        candidate_img_src = logo_img["src"]

                                    if candidate_img_src:
                                        from urllib.parse import urljoin
                                        logo_url = urljoin(curr_url, candidate_img_src)
                                        import httpx
                                        async with httpx.AsyncClient(timeout=3.0, follow_redirects=True) as img_client:
                                            img_resp = await img_client.get(logo_url)
                                            if img_resp.status_code == 200 and len(img_resp.content) > 100:
                                                from app.safety.moderation import content_moderator
                                                img_mod = await content_moderator.moderate_image_asset(logo_url, curr_url)
                                                if img_mod["is_safe"]:
                                                    ext = logo_url.split(".")[-1].split("?")[0].lower()
                                                    if ext not in ["png", "jpg", "jpeg", "gif", "svg", "ico", "webp"]:
                                                        ext = "png"
                                                    _, logo_rel_path = file_storage.save_logo(img_resp.content, ext=ext)
                                                else:
                                                    logger.warning(f"🛡️ [SAFETY LOGO REJECTED] {logo_url} failed moderation: {img_mod['reason']}")
                                except Exception as logo_err:
                                    logger.debug(f"Logo extraction notice for {curr_url}: {logo_err}")

                                page_result = CrawlResultItem(
                                    url=curr_url,
                                    title=title,
                                    html_content=html_raw,
                                    markdown=markdown_raw,
                                    text=text_clean,
                                    http_status=status_code,
                                    content_type="text/html",
                                    links=raw_links,
                                    media=raw_media,
                                    metadata={
                                        "canonical_url": canonical_url,
                                        "word_count": len(text_clean.split()),
                                        "links_count": len(raw_links),
                                        "images_count": len(raw_media),
                                        "logo_url": logo_url,
                                        "logo_rel_path": logo_rel_path,
                                    }
                                )
                                results.append(page_result)

                                if curr_depth < max_depth and len(visited_urls) + len(queue) < max_pages:
                                    extracted_hrefs = [l["href"] for l in raw_links]
                                    next_links = url_discovery.filter_and_normalize_links(
                                        links=extracted_hrefs,
                                        base_url=curr_url,
                                        visited_urls=visited_urls,
                                        allowed_host=base_host
                                    )
                                    link_text_map = {}
                                    for l in raw_links:
                                        norm = normalizer.normalize_url(l["href"], base_url=curr_url)
                                        if norm and norm not in link_text_map:
                                            link_text_map[norm] = l.get("text", "")

                                    noise_queued_this_page = 0
                                    new_items = []
                                    for n_url in next_links:
                                        if n_url not in visited_urls and not any(q["url"] == n_url for q in queue) and not any(item["url"] == n_url for item in new_items):
                                            link_text = link_text_map.get(n_url, "")
                                            score = _score_link(n_url, link_text)
                                            if score == 2:
                                                if noise_queued_this_page >= 5:
                                                    continue
                                                noise_queued_this_page += 1
                                            new_items.append({"url": n_url, "depth": curr_depth + 1, "priority": score})

                                    queue.extend(new_items)
                                    queue.sort(key=lambda x: (x.get("priority", 1), x.get("depth", 0)))

                            except Exception as e:
                                logger.error(f"Error crawling {curr_url}: {e}")
                finally:
                    _ACTIVE_BROWSER_CONTEXTS = max(0, _ACTIVE_BROWSER_CONTEXTS - 1)
            except Exception as crawl_err:
                logger.error(f"CRAWL_FAILED — Crawl4AI / Playwright engine unavailable: {crawl_err}")
                raise RuntimeError(f"CRAWL_FAILED: Crawl4AI / Playwright engine unavailable ({crawl_err})")

        return results

crawler_service = CrawlerService()
