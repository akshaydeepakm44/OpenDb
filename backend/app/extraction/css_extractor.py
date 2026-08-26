import json
import logging
from bs4 import BeautifulSoup
from typing import Dict, Any, List
from app.normalization.normalizer import normalizer

logger = logging.getLogger(__name__)

class CSSExtractor:
    @staticmethod
    def extract_deterministic_metadata(html_content: str, page_url: str) -> Dict[str, Any]:
        """Extract deterministic metadata from HTML structure without using an LLM."""
        soup = BeautifulSoup(html_content or "", "lxml")

        # 1. Page Title
        title = None
        og_title = soup.find("meta", property="og:title")
        if og_title and og_title.get("content"):
            title = normalizer.normalize_string(og_title.get("content"))
        if not title and soup.title and soup.title.string:
            title = normalizer.normalize_string(soup.title.string)
        if not title:
            h1 = soup.find("h1")
            if h1:
                title = normalizer.normalize_string(h1.text)

        # 2. Description
        description = None
        meta_desc = soup.find("meta", attrs={"name": "description"}) or soup.find("meta", property="og:description")
        if meta_desc and meta_desc.get("content"):
            description = normalizer.normalize_string(meta_desc.get("content"))

        # 3. Canonical URL
        canonical_url = page_url
        link_canonical = soup.find("link", rel="canonical")
        if link_canonical and link_canonical.get("href"):
            canonical_url = normalizer.normalize_url(link_canonical.get("href"), base_url=page_url) or page_url

        # 4. Language & Country
        html_tag = soup.find("html")
        language = None
        if html_tag and html_tag.get("lang"):
            language = normalizer.normalize_language(html_tag.get("lang"))

        # 5. JSON-LD structured data
        json_ld_data = []
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                if script.string:
                    data = json.loads(script.string.strip())
                    json_ld_data.append(data)
            except Exception:
                pass

        return {
            "title": title,
            "description": description,
            "canonical_url": canonical_url,
            "language": language,
            "json_ld": json_ld_data,
            "headings_h1": [normalizer.normalize_string(h.text) for h in soup.find_all("h1") if h.text]
        }

css_extractor = CSSExtractor()
