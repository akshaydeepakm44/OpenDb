"""
Quality Filter — §19 of Master Prompt
Rejects spam, parked domains, irrelevant pages, and low-quality sources
before they get stored in PostgreSQL, saving storage and noise.
"""
import re
import logging
from typing import Tuple, Optional
from urllib.parse import urlparse
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# ─── Blacklisted domains (spam, parked, non-business, news, docs, edu, gov) ──
BLACKLISTED_DOMAINS = {
    # Social media & Community link aggregators
    "reddit.com", "twitter.com", "x.com", "facebook.com", "instagram.com",
    "tiktok.com", "youtube.com", "pinterest.com", "tumblr.com", "linkedin.com",
    "quora.com", "stackoverflow.com", "stackexchange.com", "lowyat.net",

    # News & Media portals (Not corporate B2B lead targets)
    "cnn.com", "businessinsider.com", "bloomberg.com", "reuters.com", "nytimes.com",
    "wsj.com", "forbes.com", "fortune.com", "techcrunch.com", "wired.com", "theverge.com",
    "cnet.com", "engadget.com", "news.ycombinator.com", "yahoo.com", "msn.com",
    "foxnews.com", "cnbc.com", "bbc.com", "indianexpress.com", "timesofindia.com",
    "businesstoday.in", "business-standard.com", "builtin.com", "medium.com", "substack.com",

    # Educational / Academic / Knowledge Repositories
    "coursera.org", "udemy.com", "edx.org", "khanacademy.org", "wikipedia.org",
    "wikidata.org", "wikimedia.org", "w3schools.com", "geeksforgeeks.org",
    "tutorialspoint.com", "merriam-webster.com", "britannica.com", "techopedia.com",
    "dictionary.com", "investopedia.com", "ourworldindata.org", "springer.com",
    "sciencedirect.com", "ieee.org", "arxiv.org", "nature.com", "researchgate.net",

    # Developer Documentation & Tech Reference portals (Not company leads)
    "developer.apple.com", "developer.android.com", "developer.mozilla.org",
    "developer.microsoft.com", "developer.google.com", "developers.google.com",
    "developer.chrome.com", "visualstudio.microsoft.com",
    "docs.github.com", "docs.google.com", "support.google.com", "support.microsoft.com",

    # Accelerators, Directories & Research Aggregators
    "crunchbase.com", "wellfound.com", "marketsandmarkets.com", "startupschool.org",
    "deals.ycombinator.com", "bookface.ycombinator.com",

    # Consumer Cloud / File sharing
    "onedrive.live.com", "drive.google.com", "dropbox.com", "live.com",

    # Vehicle rental & consumer travel
    "enterprise.com", "enterprise.co", "enterprise.ca", "enterprise.co.uk",
    "enterprisetrucks.com", "hertz.com", "avis.com", "budget.com", "sixt.com",
    "nationalcar.com", "rentalcars.com", "kayak.com", "expedia.com",
    "booking.com", "tripadvisor.com", "agoda.com", "airbnb.com",

    # Job sites
    "indeed.com", "glassdoor.com", "monster.com", "ziprecruiter.com",

    # E-commerce marketplaces
    "amazon.com", "ebay.com", "aliexpress.com", "etsy.com",

    # Parking / Ad networks
    "parking.godaddy.com", "sedo.com", "afternic.com",
}

# ─── Blacklisted URL path patterns ────────────────────────────────────────────
BLACKLISTED_PATH_PATTERNS = [
    r"/tag/", r"/tags/", r"/category/", r"/categories/", r"/archive/",
    r"/page/\d+", r"/search\?", r"\?q=", r"/feed/", r"/rss",
    r"/author/", r"/user/", r"#comment", r"/wp-content/",
    r"/articles/", r"/article/", r"/topic/", r"/topics/", r"/definition/",
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
MIN_WORD_COUNT = 15           # Thin pages unlikely to be real company homepages
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

    def filter_url(self, url: str, db: Session = None) -> Tuple[bool, str]:
        """
        Stage 1: URL-level filter.
        Enforces safety guardrails: blocklist check + code-level heuristic check.
        Returns (should_keep: bool, reason: str)
        """
        if not url or not url.startswith("http"):
            return False, "Invalid URL format"

        if len(url) > MAX_URL_LENGTH:
            return False, f"URL too long ({len(url)} chars)"

        parsed = urlparse(url)
        domain = parsed.netloc.lower().lstrip("www.")

        # Reject TLDs like .edu, .gov (Academic / Municipal, non-commercial B2B)
        if domain.endswith(".edu") or domain.endswith(".gov") or ".gov." in domain or ".edu." in domain:
            return False, f"Non-commercial domain TLD (.edu / .gov): {domain}"

        # Safety Guardrail Pre-Check 1: Database Blocklist Lookup
        if db:
            from app.safety.guardrails import is_domain_blocked
            if is_domain_blocked(db, domain):
                return False, f"Blocked domain in DB: {domain}"

        # Safety Guardrail Pre-Check 2: Non-LLM Code Heuristics Scanner
        from app.safety.guardrails import check_content_heuristics
        is_disallowed, category = check_content_heuristics(url)
        if is_disallowed:
            return False, f"Disallowed safety category '{category}': {domain}"

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
        if not canonical_name or not isinstance(canonical_name, str):
            return False, "Entity name missing or not a string"

        name_strip = canonical_name.strip()
        name_lower = name_strip.lower()

        # Must be at least 3 characters
        if len(name_strip) < 3:
            return False, f"Entity name too short: '{canonical_name}'"

        # Must contain at least one letter (a-z)
        if not re.search(r"[a-zA-Z]", name_strip):
            return False, f"Entity name has no alphabetic characters: '{canonical_name}'"

        # Reject pure punctuation / dots / hashes
        if re.match(r"^[\.\,\-\_\/\?\:\;\!\#\$\%\^\&\*\(\)\=\+\<\>\{\}\[\]]+$", name_strip):
            return False, f"Punctuation-only entity name rejected: '{canonical_name}'"

        # Reject hex hashes (e.g., 67A9F99B5716)
        if re.match(r"^[0-9a-fA-F]{8,}$", name_strip):
            return False, f"Hex hash entity name rejected: '{canonical_name}'"

        # Reject question marks in company names (e.g. "What is cybersecurity?", "Is Whatsapp down?")
        if "?" in canonical_name or name_strip.endswith("?"):
            return False, f"Article/Question title rejected: '{canonical_name}'"

        # Reject bot challenge / error / report pages / undefined
        if any(term in name_lower for term in [
            "attention required!", "cloudflare", "verify you are human", "access denied",
            "market size", "market report", "market research", "undefined", "support@undefined"
        ]):
            return False, f"Non-entity / Challenge / Report / Undefined title rejected: '{canonical_name}'"

        # Reject informational article prefixes & action titles
        article_prefixes = (
            "what is", "what are", "what does", "how to", "definition of",
            "guide to", "introduction to", "tutorial", "key concepts", "types of",
            "top 10", "best 10", "versus", "is whatsapp", "reservar", "car rental",
            "rent a car", "vehicle rental"
        )
        if name_lower.startswith(article_prefixes):
            return False, f"Informational article title rejected: '{canonical_name}'"

        # Junk entity names
        junk_patterns = [
            r"^\d+$",               # Pure numbers
            r"^http",               # URLs as names
            r"^www\.",              # WWW domains
            r"^(null|none|n/a|na|unknown|unnamed|untitled|book|home|index|login|sign in|sign up|\.|\.\.)$",  # Nulls & generic pages
            r"^(company|organization|startup|firm|inc|llc|ltd)$",  # Generic
        ]
        for pattern in junk_patterns:
            if re.match(pattern, name_lower, re.IGNORECASE):
                return False, f"Junk entity name: '{canonical_name}'"

        # Low confidence
        if confidence < MIN_ENTITY_CONFIDENCE:
            return False, f"Low confidence: {confidence:.2f} < {MIN_ENTITY_CONFIDENCE}"

        # URL validation
        if not url or not isinstance(url, str):
            return False, "Entity has no URL"

        url_strip = url.strip().lower()
        if not (url_strip.startswith("http://") or url_strip.startswith("https://")):
            return False, f"Invalid URL scheme: '{url}'"

        if "undefined" in url_strip or url_strip.endswith("/.") or url_strip == ".":
            return False, f"Junk or undefined URL: '{url}'"

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
