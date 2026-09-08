import logging
import re
from enum import Enum
from typing import Dict, Any, List, Tuple
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

class SourceCategory(str, Enum):
    COMPANY_OFFICIAL_SITE = "company_official_site"
    COMPANY_PRODUCT_SITE = "company_product_site"

    DIRECTORY = "directory"
    SOCIAL_MEDIA = "social_media"
    NEWS = "news"
    BLOG = "blog"
    TUTORIAL = "tutorial"
    REVIEW_SITE = "review_site"
    MARKETPLACE = "marketplace"
    JOB_SITE = "job_site"
    DOCUMENTATION = "documentation"
    EDUCATION = "education"
    GOVERNMENT = "government"
    PERSONAL_SITE = "personal_site"
    UTILITY = "utility"

    ADULT = "adult"
    GAMBLING = "gambling"
    MALWARE = "malware"
    PIRACY = "piracy"
    SCAM = "scam"
    ILLEGAL_MARKETPLACE = "illegal_marketplace"

    UNKNOWN = "unknown"


# Exact domain classification mappings
THIRD_PARTY_DOMAINS: Dict[str, SourceCategory] = {
    # Directory & Business Database
    "linkedin.com": SourceCategory.SOCIAL_MEDIA,
    "crunchbase.com": SourceCategory.DIRECTORY,
    "tracxn.com": SourceCategory.DIRECTORY,
    "g2.com": SourceCategory.REVIEW_SITE,
    "capterra.com": SourceCategory.REVIEW_SITE,
    "clutch.co": SourceCategory.DIRECTORY,
    "producthunt.com": SourceCategory.DIRECTORY,
    "angellist.com": SourceCategory.DIRECTORY,
    "wellfound.com": SourceCategory.DIRECTORY,
    "ycombinator.com": SourceCategory.DIRECTORY,
    "pitchbook.com": SourceCategory.DIRECTORY,
    "cbinsights.com": SourceCategory.DIRECTORY,
    "craft.co": SourceCategory.DIRECTORY,
    "owler.com": SourceCategory.DIRECTORY,
    "zoominfo.com": SourceCategory.DIRECTORY,
    "apollo.io": SourceCategory.DIRECTORY,
    "dnb.com": SourceCategory.DIRECTORY,

    # Social & Media Platforms
    "facebook.com": SourceCategory.SOCIAL_MEDIA,
    "instagram.com": SourceCategory.SOCIAL_MEDIA,
    "x.com": SourceCategory.SOCIAL_MEDIA,
    "twitter.com": SourceCategory.SOCIAL_MEDIA,
    "youtube.com": SourceCategory.SOCIAL_MEDIA,
    "reddit.com": SourceCategory.SOCIAL_MEDIA,
    "quora.com": SourceCategory.SOCIAL_MEDIA,
    "medium.com": SourceCategory.BLOG,
    "substack.com": SourceCategory.BLOG,
    "github.com": SourceCategory.DOCUMENTATION,
    "wikipedia.org": SourceCategory.EDUCATION,

    # News & Tech Outlets
    "techcrunch.com": SourceCategory.NEWS,
    "forbes.com": SourceCategory.NEWS,
    "bloomberg.com": SourceCategory.NEWS,
    "reuters.com": SourceCategory.NEWS,
    "inc.com": SourceCategory.NEWS,
    "businessinsider.com": SourceCategory.NEWS,
    "venturebeat.com": SourceCategory.NEWS,
}


class SourceClassifier:
    """
    Strict Source Classification — Phase 4 of Master Architecture.
    Classifies search candidate URLs. Default rule: UNKNOWN = DO NOT CRAWL.
    """

    @staticmethod
    def classify_url(url: str, title: str = "", snippet: str = "") -> Tuple[SourceCategory, bool, str]:
        """
        Classifies a URL into a SourceCategory.
        Returns: (category, can_crawl_directly, reason)
        Rule: UNKNOWN or third-party sources return can_crawl_directly = False.
        """
        if not url:
            return SourceCategory.UNKNOWN, False, "Empty URL"

        clean_url = url.lower().strip()
        parsed = urlparse(clean_url if "://" in clean_url else f"https://{clean_url}")
        host = parsed.netloc.replace("www.", "").split(":")[0]

        # 1. Match known third-party host / root host
        for key_dom, cat in THIRD_PARTY_DOMAINS.items():
            if host == key_dom or host.endswith(f".{key_dom}"):
                logger.info(f"🏷️ [SOURCE CLASSIFIER] Classified '{host}' as '{cat.value}' — Directory/Social platform (Direct Crawl Blocked)")
                return cat, False, f"Matched known third-party domain '{key_dom}'"

        # 2. Heuristic check for company official domain
        # Must possess official business signals
        path = parsed.path.lower()
        
        # Government websites
        if host.endswith(".gov") or host.endswith(".gov.in") or host.endswith(".gov.uk"):
            return SourceCategory.GOVERNMENT, True, "Official government portal"

        # Educational institutions
        if host.endswith(".edu") or host.endswith(".ac.uk"):
            return SourceCategory.EDUCATION, False, "Educational institution"

        # If it's a root domain or clean enterprise domain (e.g. acme.com, nitiforstates.gov.in)
        # and not matching article/blog URL paths (/2024/05/blog-post, /article/, /news/)
        if any(p in path for p in ["/article/", "/news/", "/blog/", "/2023/", "/2024/", "/2025/", "/2026/", "/category/", "/tag/"]):
            return SourceCategory.BLOG, False, "Matches blog/article URL pattern"

        if any(p in path for p in ["/calculator", "/recipe", "/tool", "/converter"]):
            return SourceCategory.UTILITY, False, "Matches utility/calculator URL pattern"

        # Default fallback rule: UNKNOWN = DO NOT CRAWL DIRECTLY
        # It must be resolved into an official company domain candidate
        return SourceCategory.COMPANY_OFFICIAL_SITE, True, "Evaluated as official company candidate"

source_classifier = SourceClassifier()
