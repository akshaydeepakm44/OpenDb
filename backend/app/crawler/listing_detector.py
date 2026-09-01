"""
Listing Page Detector — §11 of Master Prompt
Determines whether a URL/page is a DIRECTORY/LISTING containing many entities,
or an ENTITY DETAIL page about a single company/organization.

Listing pages: YCombinator companies, Crunchbase categories, G2 category listings,
               Clutch agency directories, ProductHunt collections, etc.

Entity pages: Individual company homepages, LinkedIn company pages, etc.
"""
import re
import logging
from typing import List, Tuple
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# ─── URL patterns that strongly indicate listing/directory pages ───────────────
LISTING_URL_PATTERNS = [
    # Aggregator sites
    r"crunchbase\.com/organizations",
    r"crunchbase\.com/hub",
    r"crunchbase\.com/discover",
    r"ycombinator\.com/companies",
    r"angel\.co/companies",
    r"angellist\.com/companies",
    r"tracxn\.com/d/",
    r"tracxn\.com/explore",
    r"g2\.com/categories",
    r"g2\.com/compare",
    r"capterra\.com/[\w-]+software",
    r"clutch\.co/directory",
    r"clutch\.co/agencies",
    r"clutch\.co/developers",
    r"clutch\.co/it-services",
    r"producthunt\.com/topics",
    r"producthunt\.com/collections",
    r"getapp\.com/[\w-]+",
    r"softwareadvice\.com/[\w-]+",
    r"techcrunch\.com/startups",
    r"forbes\.com/lists",
    r"inc\.com/\d+",
    r"builtinchicago\.org/companies",
    r"builtinsf\.org/companies",
    r"builtinnyc\.com/companies",
    r"builtin\.com/companies",
    r"ventureradar\.com",
    r"cbinsights\.com/research",
    r"pitchbook\.com",
    r"startupranking\.com",
    r"dealroom\.co",
    r"sifted\.eu/sector",
    r"eu-startups\.com",
    r"seedtable\.com",
    r"craft\.co/industry",
    r"owler\.com/search",
    r"apollo\.io/companies",
    r"zoominfo\.com/c/",
    r"mattermark\.com",
    r"github\.com/topics",
    r"alternativeto\.net",
    r"slashdot\.org/software",
]

# ─── URL patterns that strongly indicate single-entity pages ──────────────────
ENTITY_URL_PATTERNS = [
    r"linkedin\.com/company/[^/]+/?$",
    r"crunchbase\.com/organization/[^/]+/?$",
    r"angellist\.com/company/[^/]+/?$",
    r"github\.com/[^/]+/?$",  # org root
]

# ─── HTML content patterns that indicate a listing page ──────────────────────
LISTING_CONTENT_PATTERNS = [
    r"<li[^>]*>.*?<a[^>]*href.*?</a>.*?</li>",          # many list links
    r"company\s+card",
    r"company[-_\s]list",
    r"startup\s+directory",
    r"vendor\s+list",
    r"product\s+comparison",
    r"view\s+all\s+companies",
    r"showing\s+\d+\s+(?:to|of)\s+\d+\s+(?:companies|results|startups)",
    r"filter\s+by\s+(?:category|industry|location)",
]

# ─── Minimum entity links on a page to consider it a listing ──────────────────
LISTING_MIN_ENTITY_LINKS = 5


class ListingDetector:
    """
    Classifies URLs and pages into:
    - "listing" — a directory/catalog containing many entity links
    - "entity"  — a single company/organization detail page
    """

    def classify_url(self, url: str) -> str:
        """Fast URL-pattern-based classification. Returns 'listing' or 'entity'."""
        url_lower = url.lower()
        for pattern in LISTING_URL_PATTERNS:
            if re.search(pattern, url_lower):
                logger.debug(f"[ListingDetector] URL classified as LISTING: {url}")
                return "listing"
        for pattern in ENTITY_URL_PATTERNS:
            if re.search(pattern, url_lower):
                logger.debug(f"[ListingDetector] URL classified as ENTITY: {url}")
                return "entity"
        return "unknown"

    def classify_page(self, url: str, html_content: str, text_content: str) -> Tuple[str, float]:
        """
        Deep content-based classification.
        Returns: (classification: 'listing'|'entity', confidence: float)
        """
        url_classification = self.classify_url(url)
        if url_classification == "listing":
            return "listing", 0.95
        if url_classification == "entity":
            return "entity", 0.90

        # Heuristic content analysis
        listing_signals = 0
        total_signals = 0

        # Signal 1: Many external links (listings have many outbound links)
        if html_content:
            hrefs = re.findall(r'href=["\']([^"\']+)["\']', html_content, re.IGNORECASE)
            external_links = [h for h in hrefs if h.startswith("http") and
                              urlparse(h).netloc != urlparse(url).netloc]
            if len(external_links) > 20:
                listing_signals += 2
            elif len(external_links) > 10:
                listing_signals += 1
            total_signals += 2

        # Signal 2: Content keyword patterns
        if text_content:
            text_lower = text_content.lower()
            listing_keywords = [
                "compare", "directory", "all companies", "startups list",
                "top 10", "best companies", "filter by", "showing results",
                "view all", "explore more", "browse all"
            ]
            hits = sum(1 for kw in listing_keywords if kw in text_lower)
            if hits >= 3:
                listing_signals += 2
            elif hits >= 1:
                listing_signals += 1
            total_signals += 2

        # Signal 3: Entity-page indicators
        if text_content:
            text_lower = text_content.lower()
            entity_keywords = [
                "our mission", "about us", "contact us", "our team",
                "our products", "careers at", "founded in", "headquarters",
                "privacy policy", "terms of service"
            ]
            entity_hits = sum(1 for kw in entity_keywords if kw in text_lower)
            if entity_hits >= 3:
                total_signals += 2  # entity evidence — don't increment listing_signals
            elif entity_hits >= 1:
                total_signals += 1
            else:
                listing_signals += 1  # no entity signals = +1 listing signal

        # Signal 4: Page title patterns
        if html_content:
            title_match = re.search(r"<title[^>]*>(.*?)</title>", html_content, re.IGNORECASE | re.DOTALL)
            if title_match:
                title = title_match.group(1).lower()
                listing_title_kws = ["list", "directory", "top ", "best ", "compare", "companies", "tools"]
                if any(kw in title for kw in listing_title_kws):
                    listing_signals += 1
                total_signals += 1

        if total_signals == 0:
            return "entity", 0.5

        ratio = listing_signals / total_signals
        if ratio >= 0.5:
            confidence = 0.6 + (ratio - 0.5) * 0.6
            return "listing", min(0.95, confidence)
        else:
            confidence = 0.6 + (0.5 - ratio) * 0.6
            return "entity", min(0.95, confidence)

    def extract_entity_links(self, html_content: str, base_url: str) -> List[str]:
        """
        Extract individual entity/company links from a listing page.
        Returns list of candidate entity URLs to crawl individually.
        """
        base_domain = urlparse(base_url).netloc
        links = []

        # Find all anchor tags with hrefs
        hrefs = re.findall(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>', html_content, re.IGNORECASE)

        for href in hrefs:
            href = href.strip()
            if not href or href.startswith("#") or href.startswith("javascript:"):
                continue

            # Normalize relative to absolute
            if href.startswith("//"):
                href = "https:" + href
            elif href.startswith("/"):
                parsed = urlparse(base_url)
                href = f"{parsed.scheme}://{parsed.netloc}{href}"
            elif not href.startswith("http"):
                continue

            parsed = urlparse(href)
            link_domain = parsed.netloc

            # For aggregator sites, entity links are usually the SAME domain but different paths
            # For general listing pages, entity links are EXTERNAL domains
            if link_domain == base_domain:
                # Same-domain link — include if it looks like a company profile
                path = parsed.path.lower()
                profile_patterns = [
                    r"/company/", r"/organization/", r"/startup/", r"/profile/",
                    r"/c/", r"/co/", r"/vendor/", r"/product/", r"/app/"
                ]
                if any(re.search(pat, path) for pat in profile_patterns):
                    links.append(href)
            else:
                # External link — these are actual company websites
                # Filter out common non-company domains
                excluded_domains = {
                    "linkedin.com", "twitter.com", "x.com", "facebook.com",
                    "instagram.com", "youtube.com", "github.com", "google.com",
                    "wikipedia.org", "medium.com", "reddit.com", "amazon.com",
                    "apple.com", "microsoft.com", "cloudflare.com",
                }
                if not any(exc in link_domain for exc in excluded_domains):
                    links.append(href)

        # Deduplicate preserving order
        seen = set()
        unique_links = []
        for link in links:
            if link not in seen:
                seen.add(link)
                unique_links.append(link)

        logger.info(f"[ListingDetector] Extracted {len(unique_links)} entity links from {base_url}")
        return unique_links[:50]  # cap at 50 per listing page


listing_detector = ListingDetector()
