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

    MAX_QUERY_BUDGET = 13

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
    ) -> List[Dict[str, str]]:
        """
        Generates grouped queries up to MAX_QUERY_BUDGET (13 queries max).
        Returns a list of dicts with keys: 'query', 'group', 'priority'.
        """
        clean_company = re.sub(r'[^\w\s\-\.]', '', company_name).strip()
        if not clean_company:
            return []

        domain_str = extract_domain(official_domain) if official_domain else None

        queries = []

        # ── Group D: Official Website Discovery (Highest Trust) ────────────────
        if domain_str:
            queries.extend([
                {"query": f"site:{domain_str} founder", "group": "D_OFFICIAL", "priority": 1},
                {"query": f"site:{domain_str} CEO", "group": "D_OFFICIAL", "priority": 1},
                {"query": f"site:{domain_str} leadership", "group": "D_OFFICIAL", "priority": 1},
            ])

        # ── Group A: Founder Discovery ─────────────────────────────────────────
        queries.extend([
            {"query": f'"{clean_company}" founder', "group": "A_FOUNDER", "priority": 2},
            {"query": f'"{clean_company}" founders', "group": "A_FOUNDER", "priority": 2},
            {"query": f'"{clean_company}" co-founder', "group": "A_FOUNDER", "priority": 2},
        ])

        # ── Group B: Executive Discovery ───────────────────────────────────────
        queries.extend([
            {"query": f'"{clean_company}" CEO', "group": "B_EXECUTIVE", "priority": 2},
            {"query": f'"{clean_company}" CTO', "group": "B_EXECUTIVE", "priority": 2},
            {"query": f'"{clean_company}" executive team', "group": "B_EXECUTIVE", "priority": 3},
            {"query": f'"{clean_company}" leadership team', "group": "B_EXECUTIVE", "priority": 3},
        ])

        # ── Group C: LinkedIn Reference Discovery ──────────────────────────────
        queries.extend([
            {"query": f'"{clean_company}" CEO site:linkedin.com/in', "group": "C_LINKEDIN", "priority": 3},
            {"query": f'"{clean_company}" founder site:linkedin.com/in', "group": "C_LINKEDIN", "priority": 3},
        ])

        # ── Group E: External Evidence ─────────────────────────────────────────
        queries.extend([
            {"query": f'"{clean_company}" executive director', "group": "E_EXTERNAL", "priority": 4},
        ])

        # Sanitize and truncate to MAX_QUERY_BUDGET
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
