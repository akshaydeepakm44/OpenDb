from urllib.parse import urlparse, urljoin
from typing import List, Set
from app.normalization.normalizer import normalizer

class URLDiscoveryService:
    # Multi-part public suffixes common across major international registries
    MULTI_PART_SUFFIXES = {
        "co.uk", "org.uk", "me.uk", "ltd.uk", "plc.uk", "net.uk", "sch.uk", "gov.uk", "ac.uk",
        "com.au", "net.au", "org.au", "edu.au", "gov.au", "id.au",
        "co.nz", "net.nz", "org.nz", "govt.nz", "ac.nz",
        "co.jp", "ne.jp", "or.jp", "go.jp", "ac.jp",
        "co.in", "net.in", "org.in", "gen.in", "firm.in", "ind.in",
        "com.br", "net.br", "org.br", "gov.br",
        "com.sg", "net.sg", "org.sg", "edu.sg", "gov.sg",
        "co.za", "org.za", "net.za", "gov.za",
        "com.mx", "org.mx", "net.mx", "edu.mx", "gob.mx",
        "com.my", "net.my", "org.my", "gov.my", "edu.my",
        "co.id", "net.id", "or.id", "go.id",
        "com.tr", "net.tr", "org.tr", "gov.tr",
        "co.kr", "ne.kr", "or.kr", "re.kr", "go.kr",
        "com.ar", "net.ar", "org.ar", "gov.ar",
        "com.co", "net.co", "org.co", "gov.co",
    }

    @staticmethod
    def get_domain_host(url: str) -> str:
        if not url:
            return ""
        url_str = url.strip()
        if "://" not in url_str:
            url_str = f"http://{url_str}"
        parsed = urlparse(url_str)
        host = (parsed.netloc or parsed.path).lower()
        if ":" in host:
            host = host.split(":")[0]
        return host

    @classmethod
    def get_canonical_registrable_domain(cls, url_or_host: str) -> str:
        """
        Public-suffix-aware canonical registrable domain extraction.
        Correctly parses 'www.example.co.uk/about' -> 'example.co.uk'
        and 'https://sub.stripe.com?x=1' -> 'stripe.com'.
        Preserves original URL/subdomain in caller evidence without blind stripping.
        """
        host = cls.get_domain_host(url_or_host)
        if not host:
            return ""
        if host.startswith("www."):
            host = host[4:]

        parts = host.split(".")
        if len(parts) <= 2:
            return host

        # Check for known multi-part public suffixes (e.g. co.uk, com.au)
        two_part_suffix = f"{parts[-2]}.{parts[-1]}"
        if two_part_suffix in cls.MULTI_PART_SUFFIXES and len(parts) >= 3:
            return f"{parts[-3]}.{two_part_suffix}"

        # Standard single TLD (e.g. example.com, company.ai)
        return f"{parts[-2]}.{parts[-1]}"

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
