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

logger = logging.getLogger(__name__)

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
        max_pages: int = 20,
        concurrency: int = 5,
        progress_callback=None
    ) -> List[CrawlResultItem]:
        norm_start_url = normalizer.normalize_url(starting_url)
        if not norm_start_url:
            raise ValueError(f"Invalid starting URL: {starting_url}")

        base_host = url_discovery.get_domain_host(norm_start_url)
        visited_urls: Set[str] = set()
        queue: List[Dict[str, Any]] = [{"url": norm_start_url, "depth": 0}]
        results: List[CrawlResultItem] = []

        # Try using Crawl4AI / Playwright or fallback to httpx AsyncClient
        try:
            from crawl4ai import AsyncWebCrawler, CrawlerRunConfig, CacheMode
            config = CrawlerRunConfig(
                cache_mode=CacheMode.BYPASS,
                word_count_threshold=5,
                page_timeout=30000,
                verbose=False
            )
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

                    try:
                        html_raw = ""
                        markdown_raw = ""
                        status_code = 200
                        canonical_url = curr_url

                        try:
                            crawl_res = await asyncio.wait_for(crawler.arun(url=curr_url, config=config), timeout=7.0)
                            if crawl_res and crawl_res.success:
                                html_raw = crawl_res.html or ""
                                markdown_raw = crawl_res.markdown or ""
                                status_code = crawl_res.status_code or 200
                                canonical_url = crawl_res.url or curr_url
                            else:
                                raise ValueError(f"Crawl4AI returned success=False for {curr_url}")
                        except Exception as c_err:
                            logger.warning(f"Crawl4AI/Playwright failed or timed out for {curr_url} ({c_err}). Falling back to httpx AsyncClient.")
                            import httpx
                            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
                            async with httpx.AsyncClient(timeout=4.0, follow_redirects=True, headers=headers) as h_client:
                                resp = await h_client.get(curr_url)
                                html_raw = resp.text
                                markdown_raw = ""
                                status_code = resp.status_code
                                canonical_url = str(resp.url)

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
                                        # Mandatory Safety Guardrail: Content moderation for logo/favicon path
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
                            for n_url in next_links:
                                if n_url not in visited_urls and not any(q["url"] == n_url for q in queue):
                                    queue.append({"url": n_url, "depth": curr_depth + 1})

                    except Exception as e:
                        logger.error(f"Error crawling {curr_url}: {e}")
        except Exception as crawl_err:
            logger.warning(f"Crawl4AI unavailable ({crawl_err}), falling back to httpx fetcher...")
            import httpx
            async with httpx.AsyncClient(timeout=4.0, follow_redirects=True) as client:
                while queue and len(visited_urls) < max_pages:
                    current_item = queue.pop(0)
                    curr_url = current_item["url"]
                    if curr_url in visited_urls:
                        continue
                    visited_urls.add(curr_url)
                    try:
                        resp = await client.get(curr_url, headers={"User-Agent": "OpenDB/2.4 Lead Discovery"})
                        html_raw = resp.text
                        soup = BeautifulSoup(html_raw, "html.parser")
                        title = soup.title.string if soup.title else curr_url
                        text_clean = soup.get_text()
                        results.append(CrawlResultItem(
                            url=curr_url,
                            title=normalizer.normalize_string(title),
                            html_content=html_raw,
                            markdown=text_clean,
                            text=normalizer.normalize_string(text_clean),
                            http_status=resp.status_code,
                            content_type="text/html",
                            links=[],
                            media=[],
                            metadata={"word_count": len(text_clean.split())}
                        ))
                    except Exception as fe:
                        logger.error(f"HTTPX fetch failed for {curr_url}: {fe}")

        return results

crawler_service = CrawlerService()
