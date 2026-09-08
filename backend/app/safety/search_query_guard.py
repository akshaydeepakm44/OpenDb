import logging
import re
from typing import Tuple, Optional, Dict, Any, List

logger = logging.getLogger(__name__)

# Configurable prohibited web categories
PROHIBITED_WEB_CATEGORIES: Dict[str, List[str]] = {
    "adult_content": ["porn", "xxx", "adult", "sex", "escort", "nsfw", "camgirl", "erotica", "hentai", "xhamster", "pornhub", "onlyfans"],
    "gambling": ["casino", "betting", "poker", "slots", "sportsbook", "gambling", "roulette", "jackpot", "baccarat", "bet365", "stake"],
    "illegal_drugs": ["illicit drugs", "narcotics", "darknet marketplace", "silkroad", "dispensary", "psychedelics", "buy weed online", "buy cocaine online"],
    "weapons_sales": ["buy firearms online", "unregistered ammo", "buy weapons online", "ghost gun kit"],
    "piracy": ["crack download", "keygen", "warez", "torrent", "pirated", "leaked movie", "thepiratebay", "1337x", "free download mod"],
    "malware": ["ransomware download", "keylogger tool", "botnet rental", "exploit kit", "stealer log"],
    "scam": ["phishing template", "credit card dump", "pyramid scheme join", "crypto scam pool", "fake identity generator"],
    "hate_content": ["extremist site", "white supremacy", "terrorist propaganda", "hate speech portal"]
}

# Standard negative operators automatically appended to generic discovery queries
DEFAULT_NEGATIVE_OPERATORS = [
    "-porn", "-adult", "-casino", "-betting", "-gambling",
    "-torrent", "-piracy", "-download", "-crack", "-mod", "-hack", "-drugs"
]

class SearchQueryGuard:
    """
    Search Query Safety Guard — Phase 2 of Master Architecture.
    Inspects queries before SearXNG receives them and injects safe negative search operators.
    """

    @staticmethod
    def is_query_safe(query: str) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Inspect query for explicit prohibited keywords.
        Returns: (is_safe, category, matched_keyword)
        """
        if not query or not query.strip():
            return False, "empty_query", "query was empty"

        q_lower = query.lower()

        for category, keywords in PROHIBITED_WEB_CATEGORIES.items():
            for kw in keywords:
                if re.search(r'\b' + re.escape(kw) + r'\b', q_lower) or kw in q_lower:
                    logger.warning(f"🚫 [SEARCH QUERY GUARD] Rejected query containing prohibited keyword '{kw}' (Category: {category})")
                    return False, category, kw

        return True, None, None

    @staticmethod
    def sanitize_query_with_negative_operators(query: str) -> str:
        """
        Appends negative search operators to generic discovery queries.
        Does not duplicate operators if already present.
        """
        if not query:
            return ""

        existing_tokens = set(query.lower().split())
        operators_to_add = [op for op in DEFAULT_NEGATIVE_OPERATORS if op not in existing_tokens]

        if not operators_to_add:
            return query.strip()

        sanitized = f"{query.strip()} {' '.join(operators_to_add)}"
        return sanitized

search_query_guard = SearchQueryGuard()
