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
        Order: Founder -> CEO -> CTO -> Official Team/Leadership -> LinkedIn In-Profile.
        Execution stops early when EARLY_STOP_PEOPLE_COUNT (3) verified people are found.
        """
        clean_company = re.sub(r'[^\w\s\-\.]', '', company_name).strip()
        if not clean_company:
            return []

        domain_str = extract_domain(official_domain) if official_domain else None

        queries = []

        # 1. Founder Discovery (Highest priority)
        queries.append({
            "query": f'"{clean_company}" founder site:linkedin.com/in',
            "group": "FOUNDER_LINKEDIN",
            "priority": 1
        })

        # 2. CEO Discovery
        queries.append({
            "query": f'"{clean_company}" CEO site:linkedin.com/in',
            "group": "CEO_LINKEDIN",
            "priority": 2
        })

        # 3. CTO Discovery
        queries.append({
            "query": f'"{clean_company}" CTO site:linkedin.com/in',
            "group": "CTO_LINKEDIN",
            "priority": 3
        })

        # 4. Official Website Leadership / Team (if domain available)
        if domain_str:
            queries.append({
                "query": f'site:{domain_str} (founder OR CEO OR leadership OR team)',
                "group": "OFFICIAL_DOMAIN_TEAM",
                "priority": 4
            })
        else:
            queries.append({
                "query": f'"{clean_company}" leadership team site:linkedin.com/in',
                "group": "LEADERSHIP_LINKEDIN",
                "priority": 4
            })

        # 5. General Founder / Executive query
        queries.append({
            "query": f'"{clean_company}" founder OR co-founder',
            "group": "GENERAL_FOUNDER",
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

key_people_agent = KeyPeopleDiscoveryAgent()

