"""
Key People Discovery Agent — Query Generation & Search Strategy
Component of Parallel Key People Discovery Pipeline (KP-01 through KP-08)
"""

import logging
import re
from typing import Dict, Any, List, Optional
from urllib.parse import urlparse

from app.safety.guardrails import extract_domain

logger = logging.getLogger(__name__)

# Safety guard for query text
PROHIBITED_QUERY_TERMS = {
    "hack", "crack", "torrent", "warez", "exploit", "nude", "porn",
    "illegal", "phishing", "malware", "scam"
}

class KeyPeopleDiscoveryAgent:
    """
    Intelligent agent responsible for generating domain-aware and company-targeted
    person search queries for SearXNG secondary search execution.
    """

    MAX_QUERY_BUDGET = 5
    EARLY_STOP_PEOPLE_COUNT = 3

    def sanitize_query(self, query: str) -> Optional[str]:
        """Validates query against safety guardrails."""
        if not query or len(query.strip()) < 3:
            return None
        lower_q = query.lower()
        for term in PROHIBITED_QUERY_TERMS:
            if term in lower_q:
                logger.warning(f"[KeyPeopleAgent] Safety guard blocked query: '{query}' (contains '{term}')")
                return None
        return query.strip()

    def generate_queries(
        self,
        company_name: str,
        official_domain: Optional[str] = None,
        industry: Optional[str] = None,
        country: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Generates strictly prioritized queries up to MAX_QUERY_BUDGET (5 queries max).
        When official_domain is available, generates domain-anchored queries only.
        When no domain is available, falls back to exact-phrase brand queries.
        Execution stops early when EARLY_STOP_PEOPLE_COUNT (3) verified people are found.
        """
        clean_company = re.sub(r'[^\w\s\-\.]', '', company_name).strip()
        if not clean_company:
            return []

        domain_str = extract_domain(official_domain) if official_domain else None

        clean_brand = re.sub(r'\.(com|co|io|ai|net|org|de|uk|fr|app|dev|tech|mu)$', '', clean_company.lower()).strip()
        brand_name = clean_brand.replace("-", " ").title() if clean_brand else clean_company

        queries = []

        if domain_str:
            # Domain-anchored queries only (unambiguous targeting)
            queries.append({
                "query": f"{domain_str} linkedin key people",
                "group": "DOMAIN_KEY_PEOPLE_LINKEDIN",
                "priority": 1
            })
            queries.append({
                "query": f"{domain_str} founder linkedin",
                "group": "DOMAIN_FOUNDER_LINKEDIN",
                "priority": 2
            })
            queries.append({
                "query": f"{domain_str} CEO linkedin",
                "group": "DOMAIN_CEO_LINKEDIN",
                "priority": 3
            })
            queries.append({
                "query": f"{domain_str} leadership team linkedin",
                "group": "DOMAIN_LEADERSHIP_LINKEDIN",
                "priority": 4
            })
            queries.append({
                "query": f"site:linkedin.com/in {domain_str}",
                "group": "DOMAIN_LINKEDIN_PROFILE",
                "priority": 5
            })
        else:
            # Brand-only fallback with exact-phrase matching (lower confidence)
            queries.append({
                "query": f'"{brand_name}" linkedin key people',
                "group": "BRAND_KEY_PEOPLE_LINKEDIN",
                "priority": 1
            })
            queries.append({
                "query": f'"{brand_name}" founder linkedin',
                "group": "BRAND_FOUNDER_LINKEDIN",
                "priority": 2
            })
            queries.append({
                "query": f'"{brand_name}" CEO linkedin',
                "group": "BRAND_CEO_LINKEDIN",
                "priority": 3
            })
            queries.append({
                "query": f'"{brand_name}" leadership linkedin',
                "group": "BRAND_LEADERSHIP_LINKEDIN",
                "priority": 4
            })
            queries.append({
                "query": f'site:linkedin.com/in "{brand_name}"',
                "group": "BRAND_LINKEDIN_PROFILE",
                "priority": 5
            })

        # Sanitize and truncate to MAX_QUERY_BUDGET (5)
        validated_queries = []
        seen = set()

        for item in queries:
            q_text = self.sanitize_query(item["query"])
            if q_text and q_text not in seen:
                seen.add(q_text)
                validated_queries.append({
                    "query": q_text,
                    "group": item["group"],
                    "priority": item["priority"]
                })
                if len(validated_queries) >= self.MAX_QUERY_BUDGET:
                    break

        return validated_queries

    def filter_relevant_results(
        self,
        results: List[Dict[str, Any]],
        official_domain: Optional[str] = None,
        company_name: str = ""
    ) -> List[Dict[str, Any]]:
        """
        Filters raw SearXNG search results for relevance to the target company.
        - High confidence: The domain string appears in the title, snippet, or URL.
        - Medium confidence: For LinkedIn URLs specifically, the exact brand name appears in the title or snippet.
        - Drops everything else silently (logger.debug per drop).
        - Returns filtered list annotated with 'match_confidence'.
        """
        filtered = []
        domain_str = extract_domain(official_domain).lower() if official_domain else None

        clean_company = re.sub(r'[^\w\s\-\.]', '', company_name).strip() if company_name else ""
        clean_brand = re.sub(r'\.(com|co|io|ai|net|org|de|uk|fr|app|dev|tech|mu)$', '', clean_company.lower()).strip()
        brand_lower = clean_brand if len(clean_brand) >= 3 else clean_company.lower()

        for item in results:
            url = (item.get("url") or "").lower()
            title = (item.get("title") or "").lower()
            snippet = (item.get("snippet") or "").lower()
            combined_text = f"{title} {snippet}"

            # High confidence: target domain appears in title, snippet, or URL
            if domain_str and (domain_str in url or domain_str in combined_text):
                res_copy = dict(item)
                res_copy["match_confidence"] = "high"
                filtered.append(res_copy)
                continue

            # Medium confidence: For LinkedIn URLs specifically, exact brand name in title or snippet
            is_linkedin = "linkedin.com/in/" in url or "linkedin.com/pub/" in url or "linkedin.com" in url
            if is_linkedin and brand_lower and len(brand_lower) >= 3:
                pattern = r'\b' + re.escape(brand_lower) + r'\b'
                if re.search(pattern, combined_text):
                    res_copy = dict(item)
                    res_copy["match_confidence"] = "medium"
                    filtered.append(res_copy)
                    continue

            logger.debug(f"[KeyPeopleAgent] Dropped candidate result without direct attribution: {item.get('url')} (title: '{item.get('title')}')")

        return filtered

key_people_agent = KeyPeopleDiscoveryAgent()

