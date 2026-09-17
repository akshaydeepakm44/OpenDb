import logging
import httpx
from bs4 import BeautifulSoup
from typing import Tuple, Optional

from app.safety.resource_governor import governor
from app.safety.reputation import reputation_checker

logger = logging.getLogger(__name__)

async def governed_lightweight_fetch(url: str, timeout: float = 10.0) -> Tuple[bool, Optional[str], str]:
    """
    Performs a lightweight HTTP fetch of the given URL while explicitly obeying
    the ResourceGovernor constraints and robots.txt.
    Returns (success_boolean, clean_text, status_message).
    """
    # 1. Proactive Resource Governor Check
    can_crawl, pause_reason = governor.can_start_crawl(slot_type="lightweight")
    if not can_crawl:
        msg = f"CRAWL_REJECTED_BY_GOVERNOR: {pause_reason}"
        logger.warning(f"[LightweightFetcher] {msg}")
        return False, None, msg

    # 2. Robots.txt Compliance Check
    allowed, robots_reason = reputation_checker.is_robots_allowed(url)
    if not allowed:
        msg = f"ROBOTS_DISALLOWED: {robots_reason}"
        logger.info(f"[LightweightFetcher] {msg} for {url}")
        return False, None, msg

    # 3. Perform Fetch
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, verify=False) as client:
            resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36"})
            if resp.status_code != 200:
                msg = f"HTTP_ERROR: Status code {resp.status_code}"
                logger.debug(f"[LightweightFetcher] {msg} for {url}")
                return False, None, msg
            
            html_content = resp.text
            
            # 4. Clean Text Extraction
            soup = BeautifulSoup(html_content, "html.parser")
            for element in soup(["script", "style", "noscript", "svg", "head", "title", "meta", "[document]"]):
                element.extract()
            
            clean_text = soup.get_text(separator=' ', strip=True)
            return True, clean_text, "SUCCESS"
            
    except httpx.TimeoutException:
        msg = "HTTP_TIMEOUT"
        logger.debug(f"[LightweightFetcher] {msg} for {url}")
        return False, None, msg
    except Exception as e:
        msg = f"HTTP_FETCH_ERROR: {e}"
        logger.warning(f"[LightweightFetcher] {msg} for {url}")
        return False, None, msg
