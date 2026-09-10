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

    # App Stores & Download Directories (Not corporate targets)
    "play.google.com", "apps.apple.com", "apkpure.com", "apkcombo.com",
    "apkmirror.com", "softonic.com", "download.cnet.com", "uptodown.com", "filehippo.com",

    # Entertainment, Sports betting, Movie streaming, Math puzzles
    "multimovies.org", "multimovies.com", "123movies.net", "projectreal.gg",
    "mathsisfun.com", "unicourt.com", "dratings.com", "picksandparlays.net",

    # Developer Portals & Course Subdomains
    "developer.servicenow.com", "developers.meta.com", "developers.openai.com",
    "cloud.google.com", "skills.google", "learn.microsoft.com", "roadmap.sh", "ml-ops.org",

    # News, Media, Journalism & Financial Broadcast Portals (Not corporate B2B lead targets)
    "cnn.com", "businessinsider.com", "bloomberg.com", "reuters.com", "nytimes.com",
    "wsj.com", "forbes.com", "fortune.com", "techcrunch.com", "wired.com", "theverge.com",
    "cnet.com", "engadget.com", "news.ycombinator.com", "yahoo.com", "msn.com",
    "foxnews.com", "cnbc.com", "bbc.com", "indianexpress.com", "timesofindia.com",
    "timesofindia.indiatimes.com", "thehindubusinessline.com", "indiatimes.com",
    "businesstoday.in", "business-standard.com", "builtin.com", "medium.com", "substack.com",
    "financialexpress.com", "zeebiz.com", "moneycontrol.com", "ndtv.com", "livemint.com",
    "thehindu.com", "news18.com", "indiatoday.in", "economictimes.indiatimes.com",
    "dnaindia.com", "deccanherald.com", "tribuneindia.com", "hindustantimes.com",
    "firstpost.com", "theprint.in", "thewire.in", "scroll.in", "dailymail.co.uk",
    "theguardian.com", "telegraph.co.uk", "independent.co.uk", "aljazeera.com",
    "usatoday.com", "npr.org", "politico.com", "axios.com", "huffpost.com",
    "buzzfeed.com", "vox.com", "slate.com", "salon.com", "thedailybeast.com",
    "rightnews.news", "inshorts.com", "newsweek.com", "time.com", "theatlantic.com",

    # Sports clubs, leagues, streaming & betting
    "realmadrid.com", "fcbarcelona.com", "fifa.com", "uefa.com", "nba.com",
    "nfl.com", "mlb.com", "espn.com", "cricbuzz.com", "espncricinfo.com",
    "goal.com", "livescore.com", "flashscore.com",

    # Consumer retail, second-hand marketplaces, tutoring
    "therealreal.com", "poshmark.com", "thredup.com", "pw.live", "allen.ac.in",
    "unacademy.com", "vedantu.com", "byjus.com",

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

    # Chinese web portals, Q&A sites, and aggregators (not company homepages)
    "baidu.com", "zhidao.baidu.com", "wenku.baidu.com", "tieba.baidu.com",
    "baike.baidu.com", "map.baidu.com", "sohu.com", "sina.com", "sina.com.cn",
    "163.com", "qq.com", "weibo.com", "bilibili.com", "douyin.com",
    "taobao.com", "jd.com", "tmall.com", "alipay.com", "wechat.com",
    "zhihu.com", "csdn.net", "cnblogs.com", "jianshu.com",
    "ifeng.com", "toutiao.com", "36kr.com", "ithome.com",
    "oschina.net", "segmentfault.com", "lagou.com", "51job.com",

    # Software review / comparison aggregators (listing pages, not companies)
    "g2.com", "capterra.com", "getapp.com", "softwareadvice.com",
    "trustradius.com", "sourceforge.net", "alternativeto.net",
    "producthunt.com", "slashdot.org", "crozdesk.com",

    # Generic Q&A, forums, wiki aggregators
    "answers.microsoft.com", "superuser.com", "serverfault.com",
    "askubuntu.com", "unix.stackexchange.com", "community.atlassian.com",
    "support.apple.com", "discussions.apple.com",

    # Domain/company data aggregators & directories
    "owler.com", "dnb.com", "zoominfo.com", "apollo.io",
    "similarweb.com", "semrush.com", "ahrefs.com",
    "manta.com", "yelp.com", "yellowpages.com", "bbb.org",
    "opencorporates.com", "bloomberg.com", "pitchbook.com",
    "business.com", "piliapp.com", "blauarbeit.de", "slicelife.com",
    "clutch.co", "goodfirms.co", "trustpilot.com",
}

# ─── Blacklisted URL path patterns ────────────────────────────────────────────
BLACKLISTED_PATH_PATTERNS = [
    r"/tag/", r"/tags/", r"/category/", r"/categories/", r"/archive/",
    r"/page/\d+", r"/search\?", r"\?q=", r"/feed/", r"/rss",
    r"/author/", r"/user/", r"#comment", r"/wp-content/",
    r"/articles/", r"/article/", r"/topic/", r"/topics/", r"/definition/",
    r"/blog/", r"/blogs/", r"/post/", r"/posts/", r"/news/",
    r"/course/", r"/courses/", r"/course_templates/",
    r"/download/", r"/downloads/", r"/store/apps/", r"/apk/",
    r"/tutorial/", r"/tutorials/", r"/lesson/", r"/lessons/",
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

        # Reject private / link-local / metadata IP addresses (SSRF prevention)
        import ipaddress
        host = parsed.hostname or domain.split(":")[0]
        try:
            ip = ipaddress.ip_address(host)
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or str(ip) == "169.254.169.254":
                return False, f"Private / Link-local / Metadata IP blocked: {host}"
        except ValueError:
            pass
        if host in ["localhost", "127.0.0.1", "0.0.0.0", "169.254.169.254"]:
            return False, f"Restricted host blocked: {host}"

        # Reject TLDs like .edu, .gov (Academic / Municipal, non-commercial B2B)
        if domain.endswith(".edu") or domain.endswith(".gov") or ".gov." in domain or ".edu." in domain:
            return False, f"Non-commercial domain TLD (.edu / .gov): {domain}"

        # Reject non-commercial, blog, news, media, and broadcast TLDs
        news_media_tlds = (
            ".news", ".blog", ".press", ".media", ".report", ".review",
            ".live", ".today", ".buzz", ".info", ".wiki", ".top", ".xyz", ".club"
        )
        if any(domain.endswith(tld) or f"{tld}." in domain for tld in news_media_tlds):
            return False, f"News/Media/Blog TLD: {domain}"

        # Reject domains containing explicit news, media, or broadcast indicators
        if re.search(r"(?:^|[.\-])(news|daily|times|express|gazette|tribune|journal|herald|chronicle|sports|casino|lottery|recipe|recipes|cricket|football|betting)(?:[.\-]|$)", domain):
            return False, f"News/Media/Sports domain pattern: {domain}"

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

        # Reject directory, magazine, advice, and guide portal domains (e.g. kuechenfibel.de, werkstatt-magazin.de)
        if re.search(r"(magazin|magazine|fibel|ratgeber|vergleich)", domain):
            return False, f"Directory/Magazine/Guide portal domain: {domain}"

        # Reject documentation, developer, help, training, franchise location, and resource subdomains
        # (Legitimate startup subdomains like app. or platform. are preserved and handled via root resolution)
        app_subdomains = [
            "developer.", "developers.", "docs.", "doc.", "support.", "help.",
            "locations.", "location.", "stores.", "store.", "branches.", "branch.",
            "order.", "delivery.", "menu.", "pizza.",
            "api.", "download.", "downloads.", "play.", "training.",
            "chat.", "login.", "signin.", "auth.", "portal.",
            "console.", "admin.", "mail.", "status.", "billing.", "account.", "accounts.",
            "kite.", "trade.", "web.", "my.", "tracking.", "service.", "services.",
            "forum.", "community.", "discussions.", "news.", "blog.", "blogs.", "shop.", "books."
        ]
        if any(domain.startswith(p) for p in app_subdomains):
            return False, f"Resource/Support/Location/Doc subdomain: {domain}"

        # Check blacklisted path patterns
        path = parsed.path.lower()
        app_paths = [
            r"^/chat(?:/|$|\?)", r"^/login(?:/|$|\?)", r"^/signin(?:/|$|\?)",
            r"^/signup(?:/|$|\?)", r"^/register(?:/|$|\?)", r"^/discussions(?:/|$|\?)",
            r"/crime-news/", r"/tracking-support", r"/web/services",
            r"/messerrecht", r"/rechtliche-", r"/frage-und-antwort/",
            r"/continuous-", r"/our-brand/", r"/brand/", r"/brands/",
            r"/latest-", r"/market/", r"/markets/", r"/stocks/", r"/stock/",
            r"/opinion/", r"/editorial/", r"/read/", r"/insights/", r"/whitepaper/",
            r"/whitepapers/", r"/case-study/", r"/case-studies/", r"/events/",
            r"/webinar/", r"/webinars/", r"/press-release/", r"/press-releases/",
            r"/media-room/", r"/episodes/", r"/podcast/", r"/podcasts/"
        ]
        for pattern in BLACKLISTED_PATH_PATTERNS + app_paths:
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

        # Reject listicles (e.g. "20 Profitable SaaS & Micro-SaaS Ideas", "10 Best CRM Tools")
        if re.search(r"\b\d+\s+(?:best|top|profitable|popular|free|ways|ideas|tools|apps|saas|plugins|alternatives)\b", name_lower):
            return False, f"Listicle article rejected: '{canonical_name}'"

        # Reject non-company app descriptions, tools, portals, legal statutes, and listicles
        if any(term in name_lower for term in [
            "apps on google play", "download apk", "apk for android", "watch free",
            "predictions and futures", "college football", "getting started |",
            "security checkpoint", "just a moment...", "checking your browser",
            "human verification", "learning management system", "roadmap to",
            "watch movies", "cartoons online", "login to", "sign in to", "wrongfully convicted",
            "leaderboard", "rankings for", "download for", "where's my package", "where is my package",
            "tracking support", "gesetzgebung", "waffengesetz", "vorabinformation",
            "ai chat -", "ai chat |", "ai leaderboard", "iso download", "digital seva portal",
            "books online", "google books", "paragraf", "messerrecht"
        ]):
            return False, f"Non-company application/article title rejected: '{canonical_name}'"

        # Reject long phrase/sentence titles (> 50 chars or > 6 words)
        words = name_strip.split()
        if len(words) > 6 or len(name_strip) > 50:
            return False, f"Entity name is a sentence/phrase ({len(words)} words, {len(name_strip)} chars): '{canonical_name}'"

        # Reject news, broadcast, stock ticker, retail product titles
        if any(term in name_lower for term in [
            "business news", "stock market", "sensex", "nifty", "bse/nse",
            "live updates", "latest updates", "breaking news", "live news",
            "official website", "official site", "buy & sell", "designer clothes",
            "healthy fruit", "fruit juices", "power juices", "continuous integration",
            "continuous delivery", "real madrid", "fc barcelona", "right news",
            "financial express", "zee business", "moneycontrol", "live score",
            "test series", "real test", "news today", "latest news"
        ]):
            return False, f"News/Media/Non-company title rejected: '{canonical_name}'"

        # Reject informational article prefixes & action titles
        article_prefixes = (
            "what is", "what are", "what does", "how to", "definition of",
            "guide to", "introduction to", "tutorial", "key concepts", "types of",
            "top 10", "best 10", "versus", "is whatsapp", "reservar", "car rental",
            "rent a car", "vehicle rental", "best", "top", "guide", "list of",
            "dishes", "recipes", "food guide", "how-to", "review"
        )
        if name_lower.startswith(article_prefixes) or "guide" in name_lower or "best" in name_lower:
            return False, f"Informational article/guide title rejected: '{canonical_name}'"

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

    def resolve_subdomain_and_root(self, url_or_domain: str) -> dict:
        """
        Classify whether a host is a root domain, a legitimate company app/platform subdomain,
        or a resource/support/location subdomain that must not become an independent company.
        """
        two_part_tlds = {
            "co.uk", "org.uk", "gov.uk", "ac.uk", "com.au", "net.au", "org.au",
            "co.in", "net.in", "org.in", "gen.in", "ind.in", "co.nz", "com.br",
            "co.za", "co.jp", "ne.jp", "com.mx", "com.sg", "co.kr"
        }

        if not url_or_domain:
            return {"root_domain": "", "subdomain": "", "is_resource_subdomain": False, "resource_type": None}

        if "://" in url_or_domain:
            from urllib.parse import urlparse
            host = urlparse(url_or_domain).netloc.lower()
        else:
            host = url_or_domain.lower()

        host = host.split(":")[0].strip().lstrip("www.")
        parts = host.split(".")

        if len(parts) <= 2:
            return {"root_domain": host, "subdomain": "", "is_resource_subdomain": False, "resource_type": None}

        is_two_part_tld = len(parts) >= 3 and f"{parts[-2]}.{parts[-1]}" in two_part_tlds
        num_tld_parts = 2 if is_two_part_tld else 1

        if len(parts) == num_tld_parts + 1:
            return {"root_domain": host, "subdomain": "", "is_resource_subdomain": False, "resource_type": None}

        root_domain = ".".join(parts[-(num_tld_parts + 1):])
        subdomain = ".".join(parts[:-(num_tld_parts + 1)])

        is_resource = False
        res_type = None
        sub_lower = subdomain.lower()

        # 1. Support / Helpdesk / Service / FAQ / Status
        if re.search(r"(?:^|[.-])(support|help|service|services|faq|ticket|tickets|desk|servicedesk|status|kb|knowledgebase)(?:[.-]|$)", sub_lower):
            is_resource = True
            res_type = "SUPPORT"
        # 2. Locations / Stores / Franchise / Ordering / Delivery
        elif re.search(r"(?:^|[.-])(locations|location|stores|store|branches|branch|find|order|delivery|menu|pizza)(?:[.-]|$)", sub_lower):
            is_resource = True
            res_type = "LOCATION"
        # 3. Content / Forum / Community / Guide / Magazine
        elif re.search(r"(?:^|[.-])(ratgeber|magazin|magazine|forum|community|discussions|wiki|docs|doc|blog|blogs|news)(?:[.-]|$)", sub_lower):
            is_resource = True
            res_type = "CONTENT_RESOURCE"
        # 4. Regional / Language tool subdomains on utility properties (e.g. tw.piliapp.com, cn.piliapp.com)
        elif sub_lower in {"tw", "cn", "en", "de", "fr", "es", "jp", "hk", "us", "uk", "in", "ru", "pt", "it", "nl", "pl", "br", "kr", "ar", "mx"}:
            is_resource = True
            res_type = "LANGUAGE_TOOL"
        # 5. Auth / Account / Internal Infrastructure
        elif re.search(r"(?:^|[.-])(login|signin|auth|account|accounts|billing|signup|register|admin|console|mail|webmail|tracking)(?:[.-]|$)", sub_lower):
            is_resource = True
            res_type = "AUTH_INFRASTRUCTURE"

        return {
            "root_domain": root_domain,
            "subdomain": subdomain,
            "is_resource_subdomain": is_resource,
            "resource_type": res_type
        }

    def qualify_company_candidate(
        self,
        title: str,
        snippet: str,
        url: str
    ) -> dict:
        """
        Pre-crawl company qualification engine.
        Filters out non-companies, subdomain resources, directories, and mega-enterprises before expensive crawling.
        
        Rules:
        - Subdomain resource (support, location, guide, etc.) -> REJECT (SUBDOMAIN_RESOURCE)
        - URL or domain blacklisted / directory / non-company -> REJECT
        - Confirmed >200 employees or Fortune 500 / mega-enterprise / franchise chain -> REJECT / DEPRIORITIZE
        - Confirmed 1-200 employees -> ALLOW (priority: HIGH)
        - UNKNOWN size -> ALLOW (priority: NORMAL) — Do NOT filter out unknown sizes!
        """
        # 0. Subdomain and root domain resolution
        sub_info = self.resolve_subdomain_and_root(url)
        if sub_info["is_resource_subdomain"]:
            return {
                "qualified": False,
                "reason": f"Subdomain resource ({sub_info['resource_type']}): '{sub_info['subdomain']}.{sub_info['root_domain']}' is not an independent company target",
                "candidate_type": "SUBDOMAIN_RESOURCE",
                "root_domain": sub_info["root_domain"],
                "company_size": "UNKNOWN",
                "priority": "REJECTED"
            }

        # 1. URL-level quality check
        keep_url, reason_url = self.filter_url(url)
        if not keep_url:
            return {
                "qualified": False,
                "reason": reason_url,
                "candidate_type": "INVALID_URL",
                "company_size": "UNKNOWN",
                "priority": "REJECTED"
            }

        combined = f"{title or ''} {snippet or ''}".lower()

        # 2. Check for multi-location consumer retail/franchise chain indicators in snippet/title:
        franchise_chain_patterns = [
            r"\bhundreds of locations\b",
            r"\bthousands of locations\b",
            r"\b\d+[\+,]\d*\s*locations\b",
            r"\bover \d+ locations\b",
            r"\bglobal franchise\b",
            r"\binternational franchise\b",
            r"\brestaurant chain\b",
            r"\bfast food chain\b",
            r"\bsupermarket chain\b",
            r"\bretail chain\b",
            r"\bpizza chain\b",
            r"\bfind a store near you\b",
            r"\bfind a location near you\b",
            r"\border delivery or carryout\b",
            r"\bget food delivery\b",
        ]
        for f_pat in franchise_chain_patterns:
            if re.search(f_pat, combined):
                return {
                    "qualified": False,
                    "reason": f"Consumer franchise/retail chain outside B2B startup/SMB target (matched '{f_pat}')",
                    "candidate_type": "FRANCHISE_CHAIN",
                    "company_size": ">200",
                    "priority": "DEPRIORITIZED"
                }

        # 3. Check for mega-enterprises / Fortune 500 / publicly traded corporations
        enterprise_indicators = [
            "fortune 500", "fortune 100", "fortune 1000", "s&p 500",
            "nasdaq:", "nyse:", "lse:", "euronext:",
            "multinational conglomerate", "multinational corporation",
            "publicly traded company", "publicly traded conglomerate",
            "10,000+ employees", "5,000+ employees", "over 1,000 employees",
            "1,000+ employees", "over 500 employees", "500+ employees",
            "20,000+ employees", "50,000+ employees", "tens of thousands of employees"
        ]
        for ind in enterprise_indicators:
            if ind in combined:
                return {
                    "qualified": False,
                    "reason": f"Enterprise outside target (>200 employees): matched '{ind}'",
                    "candidate_type": "LARGE_ENTERPRISE",
                    "company_size": ">200",
                    "priority": "DEPRIORITIZED"
                }

        # 4. Check for specific employee range patterns in snippet
        company_size = "UNKNOWN"
        priority = "NORMAL"

        range_match = re.search(r"\b(\d{1,3}(?:,\d{3})+|\d+)\s*(?:-|to)\s*(\d{1,3}(?:,\d{3})+|\d+)\s*(?:employees|people|staff|team members)\b", combined)
        if range_match:
            low = int(range_match.group(1).replace(",", ""))
            high = int(range_match.group(2).replace(",", ""))
            if high <= 200:
                company_size = f"{low}-{high}"
                priority = "HIGH"
            elif low > 200:
                return {
                    "qualified": False,
                    "reason": f"Exceeds 200 employees ({low}-{high})",
                    "candidate_type": "LARGE_ENTERPRISE",
                    "company_size": f"{low}-{high}",
                    "priority": "DEPRIORITIZED"
                }
            else:
                company_size = f"{low}-{high}"
                priority = "NORMAL"
        else:
            single_match = re.search(r"\b(\d{1,3}(?:,\d{3})+|\d+)\+?\s*(?:employees|people|staff)\b", combined)
            if single_match:
                count = int(single_match.group(1).replace(",", ""))
                if count <= 200:
                    company_size = f"1-{count}" if count > 1 else "1"
                    priority = "HIGH"
                elif count > 200:
                    return {
                        "qualified": False,
                        "reason": f"Exceeds 200 employees ({count}+)",
                        "candidate_type": "LARGE_ENTERPRISE",
                        "company_size": ">200",
                        "priority": "DEPRIORITIZED"
                    }

        # 5. Positive startup / SMB signals boost priority to HIGH
        startup_signals = [
            "startup", "seed round", "series a", "series b", "early stage",
            "growth stage", "bootstrapped", "founded in 20", "incubated",
            "y combinator", "techstars", "smb", "small business"
        ]
        if any(s in combined for s in startup_signals):
            if priority == "NORMAL":
                priority = "HIGH"
            if company_size == "UNKNOWN":
                company_size = "UNKNOWN (Startup Indicator)"

        return {
            "qualified": True,
            "reason": "Passed pre-crawl qualification",
            "candidate_type": "COMPANY",
            "company_size": company_size,
            "priority": priority
        }


quality_filter = QualityFilter()
resolve_subdomain_and_root = quality_filter.resolve_subdomain_and_root

