from urllib.parse import urlparse, urljoin
from typing import List, Set
from app.normalization.normalizer import normalizer

class URLDiscoveryService:
    @staticmethod
    def get_domain_host(url: str) -> str:
        parsed = urlparse(url)
        return parsed.netloc.lower()

    @staticmethod
    def is_same_domain(target_url: str, base_host: str) -> bool:
        target_host = URLDiscoveryService.get_domain_host(target_url)
        if not target_host or not base_host:
            return False
        # Remove www. for domain comparison
        target_clean = target_host.replace("www.", "")
        base_clean = base_host.replace("www.", "")
        return target_clean == base_clean or target_clean.endswith("." + base_clean)

    @staticmethod
    def filter_and_normalize_links(
        links: List[str],
        base_url: str,
        visited_urls: Set[str],
        allowed_host: str
    ) -> List[str]:
        valid_links = []
        for raw_link in links:
            norm_link = normalizer.normalize_url(raw_link, base_url=base_url)
            if not norm_link:
                continue
            if norm_link in visited_urls:
                continue
            if not norm_link.startswith(("http://", "https://")):
                continue
            if URLDiscoveryService.is_same_domain(norm_link, allowed_host):
                valid_links.append(norm_link)
        return valid_links

url_discovery = URLDiscoveryService()
