"""
Company Data Completeness Engine — Phase 9 & 10 of Master Architecture
Calculates exact weighted 100-point Data Completeness score & assign 5 Badge Tiers.
Enforces bounded re-crawl stop rules (MAX_RECRAWL_ROUNDS = 3, MAX_PAGES = 15, DEPTH = 2).
"""
import logging
from typing import Dict, Any, List

logger = logging.getLogger(__name__)

# Stop criteria for re-crawl controller
MAX_RECRAWL_ROUNDS = 3
MAX_PAGES_PER_COMPANY = 15
MAX_CRAWL_DEPTH = 2

class CompanyCompletenessEngine:
    """
    Weighted 100-Point Data Completeness Formula Engine.
    Emails (25) + Leadership (25) + Firmographics (30) + Vault Storage (20) = 100 max.
    """

    def calculate_completeness(
        self,
        dossier: Dict[str, Any],
        crawled_pages: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Calculates exact deterministic Data Completeness score and Badge Level.
        """
        breakdown = {}

        # 1. VERIFIED EMAILS (25 pts)
        emails = dossier.get("verified_emails") or []
        verified_count = sum(1 for e in emails if isinstance(e, dict) and e.get("status") == "verified")
        if verified_count >= 2:
            email_score = 25.0
        elif verified_count == 1 or len(emails) >= 1:
            email_score = 15.0
        else:
            email_score = 0.0
        breakdown["verified_emails"] = email_score

        # 2. LEADERSHIP (25 pts)
        people = dossier.get("decision_makers") or []
        has_founder = any("founder" in (p.get("title") or "").lower() for p in people)
        if has_founder and len(people) >= 2:
            leadership_score = 25.0
        elif len(people) >= 2:
            leadership_score = 20.0
        elif len(people) == 1:
            leadership_score = 10.0
        else:
            leadership_score = 0.0
        breakdown["leadership"] = leadership_score

        # 3. FIRMOGRAPHICS (30 pts)
        fg = dossier.get("firmographics") or {}
        hq_pts = 10.0 if (fg.get("headquarters") or {}).get("value") else 0.0
        ind_pts = 5.0 if (fg.get("industry") or {}).get("value") else 0.0
        size_pts = 5.0 if (fg.get("company_size") or {}).get("value") else 0.0
        rev_pts = 10.0 if (fg.get("revenue_funding") or {}).get("value") else 0.0
        firmographics_score = hq_pts + ind_pts + size_pts + rev_pts
        breakdown["firmographics"] = firmographics_score

        # 4. VAULT STORAGE (20 pts)
        subpages = dossier.get("crawled_subpages") or []
        urls_lower = [(p.get("url") or "").lower() for p in (crawled_pages or [])]

        has_home = len(crawled_pages) >= 1
        has_about = any("about" in u for u in urls_lower)
        has_prods = any(kw in u for u in urls_lower for kw in ["product", "service", "solution", "feature"])
        has_extra = len(crawled_pages) >= 4 or len(subpages) >= 4

        vault_score = (5.0 if has_home else 0.0) + \
                      (5.0 if has_about else 0.0) + \
                      (5.0 if has_prods else 0.0) + \
                      (5.0 if has_extra else 0.0)
        breakdown["vault_storage"] = vault_score

        total_score = round(sum(breakdown.values()), 1)

        # 5 Badge Tiers
        if total_score >= 90.0:
            badge = "VERIFIED COMPLETE"
            color = "🔵"
            status_code = "VERIFIED_COMPLETE"
        elif total_score >= 75.0:
            badge = "HIGH QUALITY"
            color = "🟢"
            status_code = "HIGH_QUALITY_COMPANY"
        elif total_score >= 60.0:
            badge = "QUALIFIED"
            color = "🟡"
            status_code = "QUALIFIED_COMPANY"
        elif total_score >= 40.0:
            badge = "BASIC"
            color = "🟠"
            status_code = "BASIC_COMPANY"
        else:
            badge = "INSUFFICIENT"
            color = "🔴"
            status_code = "INSUFFICIENT_DATA"

        return {
            "total_score": total_score,
            "badge": badge,
            "color_dot": color,
            "status_code": status_code,
            "breakdown": breakdown,
            "is_sync_eligible": total_score >= 60.0,
            "max_recrawl_rounds": MAX_RECRAWL_ROUNDS,
            "max_pages_per_company": MAX_PAGES_PER_COMPANY,
            "max_crawl_depth": MAX_CRAWL_DEPTH
        }


company_completeness_engine = CompanyCompletenessEngine()
