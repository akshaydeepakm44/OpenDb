"""
Quality Filter — §19 of Master Prompt
Rejects spam, parked domains, irrelevant pages, and low-quality sources
before they get stored in PostgreSQL, saving storage and noise.
"""
import re
import logging
from typing import Tuple
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# ─── Blacklisted domains (spam, parked, non-business) ─────────────────────────
BLACKLISTED_DOMAINS = {
    # Link aggregators / social media (not companies)
    "reddit.com", "twitter.com", "x.com", "facebook.com", "instagram.com",
    "tiktok.com", "youtube.com", "pinterest.com", "tumblr.com", "linkedin.com",
    # News/media aggregators (not entity targets)
    "medium.com", "substack.com", "blogger.com", "wordpress.com",
    "dailythanthi.com", "oneindia.com", "indianexpress.com", "timesofindia.com",
    "businesstoday.in", "business-standard.com", "geeksforgeeks.org", "tutorialspoint.com",
    "merriam-webster.com", "britannica.com", "wikipedia.org", "wikidata.org",
    # Generic SaaS/infra that aren't leads
    "notion.so", "airtable.com", "typeform.com", "surveymonkey.com",
    "mailchimp.com", "hubspot.com", "salesforce.com",
    # Ad networks / parked domains
    "parking.godaddy.com", "sedo.com", "afternic.com",
    "docs.google.com", "amazonaws.com", "cloudflare.com", "fastly.com",
    # Job sites (not company homepages)
    "indeed.com", "glassdoor.com", "monster.com", "ziprecruiter.com",
    # Generic Shopping portals
    "amazon.com", "ebay.com", "aliexpress.com", "etsy.com",
}

# ─── Blacklisted URL path patterns ────────────────────────────────────────────
BLACKLISTED_PATH_PATTERNS = [
    r"/tag/", r"/tags/", r"/category/", r"/categories/", r"/archive/",
    r"/page/\d+", r"/search\?", r"\?q=", r"/feed/", r"/rss",
    r"/author/", r"/user/", r"#comment", r"/wp-content/",
    r"\.pdf$", r"\.xml$", r"\.json$", r"\.csv$",
]

# ─── Spam/parked page content indicators ──────────────────────────────────────
SPAM_CONTENT_PATTERNS = [
    r"this domain is for sale",
    r"domain\s+parking",
    r"buy this domain",
    r"parked by",
    r"this website is for sale",
    r"click here to buy",
    r"godaddy\.com",
    r"sedoparking",
    r"domain expired",
    r"account suspended",
    r"coming soon",
    r"under construction",
    r"hello world",
    r"default web page",
    r"test page",
    r"apache2 default page",
    r"nginx welcome page",
    r"it works",
]

# ─── Minimum content quality thresholds ────────────────────────────────────────
MIN_WORD_COUNT = 10           # Very thin pages unlikely to be real companies
MIN_TITLE_LENGTH = 3          # Pages without real titles
MIN_CANONICAL_NAME_LENGTH = 2 # Entity names must be meaningful
MAX_URL_LENGTH = 500          # Extremely long URLs are usually junk

# ─── Entity confidence thresholds ─────────────────────────────────────────────
MIN_ENTITY_CONFIDENCE = 0.30  # Entities with < 30% confidence are discarded


class QualityFilter:
    """
    Multi-stage quality filter applied at:
    1. URL level (before crawling)
    2. Content level (after crawling, before storage)
    3. Entity level (after extraction, before DB write)
    """

    def filter_url(self, url: str) -> Tuple[bool, str]:
        """
        Stage 1: URL-level filter.
        Returns (should_keep: bool, reason: str)
        """
        if not url or not url.startswith("http"):
            return False, "Invalid URL format"

        if len(url) > MAX_URL_LENGTH:
            return False, f"URL too long ({len(url)} chars)"

        parsed = urlparse(url)
        domain = parsed.netloc.lower().lstrip("www.")

        # Check blacklisted domains
        for blacklisted in BLACKLISTED_DOMAINS:
            if domain == blacklisted or domain.endswith(f".{blacklisted}"):
                return False, f"Blacklisted domain: {domain}"

        # Check blacklisted path patterns
        path = parsed.path.lower()
        for pattern in BLACKLISTED_PATH_PATTERNS:
            if re.search(pattern, url.lower()):
                return False, f"Blacklisted URL pattern: {pattern}"

        # Require at least a recognizable TLD
        if "." not in domain:
            return False, "No TLD in domain"

        return True, "OK"

    def filter_content(self, url: str, html_content: str, text_content: str,
                        title: str, word_count: int) -> Tuple[bool, str]:
        """
        Stage 2: Content-level filter (after crawling, before extraction).
        Returns (should_keep: bool, reason: str)
        """
        # Very thin pages
        if word_count < MIN_WORD_COUNT:
            return False, f"Thin content ({word_count} words < {MIN_WORD_COUNT})"

        # No title
        if not title or len(title.strip()) < MIN_TITLE_LENGTH:
            return False, "Missing or empty page title"

        # Spam/parked content detection
        if text_content:
            text_lower = text_content.lower()
            for pattern in SPAM_CONTENT_PATTERNS:
                if re.search(pattern, text_lower):
                    return False, f"Spam/parked page: matched '{pattern}'"

        # HTTP error pages
        if title:
            title_lower = title.lower()
            error_titles = ["404", "not found", "403", "forbidden", "500", "error",
                           "access denied", "page not found", "file not found"]
            for err in error_titles:
                if err in title_lower:
                    return False, f"Error page: title contains '{err}'"

        return True, "OK"

    def filter_entity(self, canonical_name: str, url: str,
                      confidence: float) -> Tuple[bool, str]:
        """
        Stage 3: Entity-level filter (after extraction, before DB write).
        Returns (should_keep: bool, reason: str)
        """
        # Insufficient name
        if not canonical_name or len(canonical_name.strip()) < MIN_CANONICAL_NAME_LENGTH:
            return False, f"Entity name too short or missing: '{canonical_name}'"

        # Junk entity names
        junk_patterns = [
            r"^\d+$",               # Pure numbers
            r"^http",               # URLs as names
            r"^www\.",              # WWW domains
            r"^(null|none|n/a|na|unknown|unnamed|untitled)$",  # Nulls
            r"^(company|organization|startup|firm|inc|llc|ltd)$",  # Generic
        ]
        name_lower = canonical_name.lower().strip()
        for pattern in junk_patterns:
            if re.match(pattern, name_lower, re.IGNORECASE):
                return False, f"Junk entity name: '{canonical_name}'"

        # Low confidence
        if confidence < MIN_ENTITY_CONFIDENCE:
            return False, f"Low confidence: {confidence:.2f} < {MIN_ENTITY_CONFIDENCE}"

        # URL must be present
        if not url:
            return False, "Entity has no URL"

        return True, "OK"

    def score_entity_completeness(self, domain_data: dict) -> float:
        """
        Score how complete the extracted entity data is (0.0 - 1.0).
        Used to boost confidence of well-filled records.
        """
        important_fields = [
            "company_name", "description", "industry", "headquarters",
            "founding_year", "employee_count", "technologies",
            "contact_emails", "key_people", "funding_stage", "products"
        ]
        filled = sum(1 for f in important_fields if domain_data.get(f))
        return filled / len(important_fields)


quality_filter = QualityFilter()
