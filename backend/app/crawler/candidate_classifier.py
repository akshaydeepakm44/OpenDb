"""
Candidate Classification Module — §7 of Master Rules

Classifies discovered search result URLs into:
- COMPANY_WEBSITE
- PRODUCT_PAGE
- BLOG
- NEWS_ARTICLE
- DIRECTORY
- JOB_PAGE
- SOCIAL_MEDIA
- DOCUMENTATION
- OTHER

Only COMPANY_WEBSITE and PRODUCT_PAGE proceed automatically to the crawl queue.
"""
import re
import logging
from urllib.parse import urlparse
from typing import Tuple, Dict, Any

logger = logging.getLogger(__name__)

class CandidateCategory:
    COMPANY_OFFICIAL_SITE = "COMPANY_OFFICIAL_SITE"
    COMPANY_SUBDOMAIN = "COMPANY_SUBDOMAIN"
    BLOG = "BLOG"
    NEWS = "NEWS"
    DIRECTORY = "DIRECTORY"
    REVIEW_SITE = "REVIEW_SITE"
    SOCIAL_MEDIA = "SOCIAL_MEDIA"
    JOB_SITE = "JOB_SITE"
    DOCUMENTATION = "DOCUMENTATION"
    MARKETPLACE = "MARKETPLACE"
    GOVERNMENT = "GOVERNMENT"
    EDUCATION = "EDUCATION"
    PERSONAL_SITE = "PERSONAL_SITE"
    UTILITY = "UTILITY"
    RECIPE = "RECIPE"
    UNKNOWN = "UNKNOWN"

# Domain blocklists per category (Rule #3 & Rule B Gate 1)
SOCIAL_DOMAINS = {
    "linkedin.com", "facebook.com", "instagram.com", "twitter.com", "x.com",
    "youtube.com", "tiktok.com", "pinterest.com", "reddit.com", "quora.com"
}

DIRECTORY_DOMAINS = {
    "crunchbase.com", "pitchbook.com", "zoominfo.com", "apollo.io",
    "clutch.co", "g2.com", "capterra.com", "trustpilot.com",
    "yelp.com", "yellowpages.com", "glassdoor.com", "builtin.com",
    "wikipedia.org", "slopachi-station.com", "p-world.co.jp", "p-town.dmm.com"
}

DOCUMENTATION_DOMAINS = {
    "github.com", "gitlab.com", "bitbucket.org", "readthedocs.io",
    "npmjs.com", "pypi.org", "developer.mozilla.org", "docs.google.com",
    "stackexchange.com", "stackoverflow.com"
}

NEWS_DOMAINS = {
    "techcrunch.com", "forbes.com", "bloomberg.com", "reuters.com",
    "wsj.com", "nytimes.com", "businessinsider.com", "venturebeat.com",
    "cnbc.com", "theverge.com", "wired.com", "medium.com", "substack.com",
    "bbc.com", "cnet.com", "engadget.com", "hindustantimes.com", "republicworld.com",
    "etvbharat.com", "sacnilk.com", "bollymoviereviewz.com", "indianexpress.com", "timesofindia.com"
}

TUTORIAL_BLOG_DOMAINS = {
    "sysprobs.com", "intowindows.com", "recipebuster.com", "196flavors.com",
    "pacificislandrecipe.com", "techengage.com", "geekuninstaller.com", "manaui.com"
}

UTILITY_DOMAINS = {
    "percentagecalculator.net", "calculateme.com", "calculatorhistory.net",
    "myapps.microsoft.com", "mysignins.microsoft.com"
}


class CandidateClassifier:
    def classify(self, url: str, title: str = "", snippet: str = "") -> Tuple[str, str, bool]:
        """
        Gate 1 — Fast Rejection Source Classifier.
        Returns: (category: str, reason: str, is_allowed_for_crawl: bool)
        ONLY COMPANY_OFFICIAL_SITE and COMPANY_SUBDOMAIN return is_allowed_for_crawl = True.
        """
        if not url or not url.startswith("http"):
            return CandidateCategory.UNKNOWN, "Invalid URL format", False

        parsed = urlparse(url)
        domain = parsed.netloc.lower().replace("www.", "")
        path = parsed.path.lower()
        title_lower = (title or "").lower()

        # 1. Social Media Check (LinkedIn URLs are NEVER primary company domains)
        if any(d in domain for d in SOCIAL_DOMAINS):
            return CandidateCategory.SOCIAL_MEDIA, f"Social network domain ({domain})", False

        # 2. Directory / Aggregator Check
        if any(d in domain for d in DIRECTORY_DOMAINS):
            return CandidateCategory.DIRECTORY, f"Directory / Aggregator domain ({domain})", False

        # 3. Documentation / Code Repository Check
        if any(d in domain for d in DOCUMENTATION_DOMAINS) or any(x in path for x in ["/docs/", "/documentation/", "/api-ref/"]):
            return CandidateCategory.DOCUMENTATION, f"Documentation domain/path ({domain})", False

        # 4. News / Media Check
        if any(d in domain for d in NEWS_DOMAINS):
            return CandidateCategory.NEWS, f"News/Media publication domain ({domain})", False

        # 5. Tutorial / Recipe / Blog Check
        if any(d in domain for d in TUTORIAL_BLOG_DOMAINS):
            return CandidateCategory.BLOG, f"Tutorial/Blog domain ({domain})", False

        if any(x in path for x in ["/blog/", "/blogs/", "/article/", "/articles/", "/news/", "/posts/", "/recipe/", "/recipes/"]):
            return CandidateCategory.BLOG, f"Blog/Article/Recipe path ({path})", False

        # 6. Online Utility / Calculator Check
        if any(d in domain for d in UTILITY_DOMAINS) or "calculator" in domain or "converter" in domain:
            return CandidateCategory.UTILITY, f"Single utility/calculator domain ({domain})", False

        # 7. Job Portal Check
        if any(x in path for x in ["/jobs/", "/careers/", "/work-with-us/"]) or "job" in title_lower:
            return CandidateCategory.JOB_SITE, "Careers/Job portal page", False

        # 8. Root Domain vs Subdomain check
        parts = domain.split(".")
        if len(parts) > 2 and parts[0] not in ("www", "m"):
            if parts[0] in ("blog", "news", "careers", "jobs", "docs", "help", "support", "forum"):
                return CandidateCategory.COMPANY_SUBDOMAIN, f"Non-corporate subdomain target ({parts[0]})", False

        # 9. Clean Homepage or Official Corporate Path
        clean_path = path.rstrip("/")
        if clean_path in ("", "/about", "/about-us", "/company", "/contact", "/contact-us", "/pricing", "/team", "/solutions", "/platform"):
            return CandidateCategory.COMPANY_OFFICIAL_SITE, "Official corporate portal domain", True

        if len(clean_path.split("/")) <= 2:
            return CandidateCategory.COMPANY_OFFICIAL_SITE, "Standard corporate portal structure", True

        return CandidateCategory.UNKNOWN, f"Deep path non-company candidate target ({path})", False


candidate_classifier = CandidateClassifier()
