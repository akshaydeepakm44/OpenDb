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

from crawl4ai import AsyncWebCrawler, CrawlerRunConfig, CacheMode
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

                if progress_callback:
                    await progress_callback(
                        stage="CRAWLING",
                        pages_discovered=len(visited_urls) + len(queue),
                        pages_crawled=len(visited_urls),
                        current_url=curr_url
                    )

                try:
                    crawl_res = await crawler.arun(url=curr_url, config=config)
                    if not crawl_res or not crawl_res.success:
                        logger.warning(f"Failed to crawl page: {curr_url}")
                        continue

                    html_raw = crawl_res.html or ""
                    markdown_raw = crawl_res.markdown or ""
                    
                    # Extract page title and clean text using BeautifulSoup fallback if Crawl4AI markdown is plain
                    soup = BeautifulSoup(html_raw, "lxml")
                    title = normalizer.normalize_string(soup.title.string) if soup.title else ""
                    if not title:
                        h1 = soup.find("h1")
                        title = normalizer.normalize_string(h1.text) if h1 else curr_url

                    text_clean = normalizer.normalize_string(soup.get_text()) or markdown_raw

                    # Extract page links & images
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

                    page_result = CrawlResultItem(
                        url=curr_url,
                        title=title,
                        html_content=html_raw,
                        markdown=markdown_raw,
                        text=text_clean,
                        http_status=crawl_res.status_code or 200,
                        content_type="text/html",
                        links=raw_links,
                        media=raw_media,
                        metadata={
                            "canonical_url": crawl_res.url,
                            "word_count": len(text_clean.split()),
                            "links_count": len(raw_links),
                            "images_count": len(raw_media)
                        }
                    )
                    results.append(page_result)

                    # Enqueue discovered subpages if depth limits allow
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

        return results

crawler_service = CrawlerService()
