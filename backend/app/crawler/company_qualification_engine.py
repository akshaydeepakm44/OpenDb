"""
Company Qualification Engine — Phase 6 of Master Architecture
Implements Stage 1 Lightweight Qualification Crawl (1-3 pages max: Homepage, About, Contact).
Evaluates exact positive scoring signals (+15 to +5) and negative penalties (-50 to -20).
Enforces Immediate Rejection for unsafe / parked domains.
"""
import re
import logging
from typing import Dict, Any, List, Tuple
from app.safety.domain_safety_guard import domain_safety_guard

logger = logging.getLogger(__name__)

# Minimum qualification threshold to proceed from Stage 1 (Lightweight) to Stage 2 (Deep Crawl)
STAGE_1_QUALIFICATION_THRESHOLD = 35.0


class CompanyQualificationEngine:
    """
    Two-Stage Qualification Controller.
    Stage 1: Lightweight inspection (Homepage, About, Contact — 1 to 3 pages max).
    Stage 2: Trigger deep crawl only if Stage 1 Score >= 35.0 and no hard blocks exist.
    """

    def evaluate_stage1_qualification(
        self,
        domain: str,
        pages: List[Dict[str, Any]],
        dossier: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Calculates exact Stage 1 Qualification Score.
        Returns:
        {
            "qualified": bool,
            "stage1_score": float,
            "positive_signals": List[str],
            "penalties": List[str],
            "hard_blocked": bool,
            "block_reason": Optional[str],
            "recommended_stage2_pages": List[str]
        }
        """
        # 1. Hard Domain Safety Check
        safety = domain_safety_guard.evaluate_domain_safety(domain)
        if not safety["allowed"]:
            return {
                "qualified": False,
                "stage1_score": -100.0,
                "positive_signals": [],
                "penalties": [f"Hard Safety Block: {safety['reason']}"],
                "hard_blocked": True,
                "block_reason": safety["reason"],
                "recommended_stage2_pages": []
            }

        combined_text = "\n".join(p.get("text", "") or "" for p in pages).lower()
        combined_html = "\n".join(p.get("html", "") or "" for p in pages).lower()

        # 2. Check for Immediate Rejection Signals
        if domain_safety_guard.is_parked_page_content(combined_text):
            return {
                "qualified": False,
                "stage1_score": -100.0,
                "positive_signals": [],
                "penalties": ["Parked domain or domain-for-sale page detected"],
                "hard_blocked": True,
                "block_reason": "Parked domain content",
                "recommended_stage2_pages": []
            }

        positive_signals = []
        penalties = []
        score = 0.0

        # ─── Positive Signal Scoring ──────────────────────────────────────────
        # Clear company identity (+15)
        c_name = dossier.get("company_name")
        if c_name and len(c_name) > 2:
            score += 15.0
            positive_signals.append("Clear company identity (+15)")

        # Official company description (+10)
        ov = (dossier.get("business_overview") or {}).get("text")
        if ov and len(ov.strip()) > 30:
            score += 10.0
            positive_signals.append("Official business description (+10)")

        # Business products / services (+10)
        if any(kw in combined_text for kw in ["products", "services", "solutions", "platform", "features", "pricing"]):
            score += 10.0
            positive_signals.append("Business products/services identified (+10)")

        # About page present (+10)
        has_about = any("about" in (p.get("url", "") or "").lower() for p in pages) or "about us" in combined_text
        if has_about:
            score += 10.0
            positive_signals.append("About page present (+10)")

        # Corporate contact email (+10)
        emails = dossier.get("verified_emails") or []
        if emails:
            score += 10.0
            positive_signals.append("Corporate contact email (+10)")

        # Physical address (+10)
        fg = dossier.get("firmographics") or {}
        hq = (fg.get("headquarters") or {}).get("value")
        if hq or re.search(r'\b(?:headquarters|office|inc\.|ltd\.|address|located in)\b', combined_text):
            score += 10.0
            positive_signals.append("Physical address / headquarters info (+10)")

        # Leadership / founder information (+10)
        people = dossier.get("decision_makers") or []
        if people or any(kw in combined_text for kw in ["ceo", "founder", "leadership", "management team"]):
            score += 10.0
            positive_signals.append("Leadership / founder information (+10)")

        # Privacy policy / legal (+5)
        if "privacy policy" in combined_text or "privacy" in combined_text:
            score += 5.0
            positive_signals.append("Privacy policy / legal information (+5)")

        # Terms of service (+5)
        if "terms of service" in combined_text or "terms & conditions" in combined_text or "terms" in combined_text:
            score += 5.0
            positive_signals.append("Terms of service (+5)")

        # LinkedIn company reference (+5)
        if "linkedin.com/company" in combined_html or "linkedin" in combined_text:
            score += 5.0
            positive_signals.append("LinkedIn company reference (+5)")

        # Multiple official company subpages (+5)
        if len(pages) >= 2:
            score += 5.0
            positive_signals.append("Multiple official company pages (+5)")

        # Branding consistency (+5)
        if c_name and c_name.lower() in combined_text:
            score += 5.0
            positive_signals.append("Company branding consistency (+5)")

        # ─── Negative Penalties ───────────────────────────────────────────────
        # Recipe content (-50)
        if any(kw in combined_text for kw in ["prep time", "cook time", "ingredients", "tablespoon", "preheat oven"]):
            score -= 50.0
            penalties.append("Recipe content detected (-50)")

        # News / article structure (-40)
        if any(kw in combined_text for kw in ["published on", "written by", "breaking news", "journalism", "editorial staff"]):
            score -= 40.0
            penalties.append("News / article structure (-40)")

        # Calculator / utility only (-40)
        if any(kw in combined_text for kw in ["bmi calculator", "unit converter", "loan payment calculator", "percentage calculator"]):
            score -= 40.0
            penalties.append("Calculator / utility only (-40)")

        # Personal blog (-40)
        if "my personal blog" in combined_text or "welcome to my blog" in combined_text or "posted by admin" in combined_text:
            score -= 40.0
            penalties.append("Personal blog content (-40)")

        # Blog / tutorial structure (-35)
        if "tutorial" in combined_text and "how to" in combined_text and not any(kw in combined_text for kw in ["our platform", "our services", "contact us"]):
            score -= 35.0
            penalties.append("Blog / tutorial structure (-35)")

        # Thin / empty website (-30)
        if len(combined_text.strip()) < 150:
            score -= 30.0
            penalties.append("Thin / empty website content (-30)")

        # Cloudflare challenge (-20)
        if "just a moment..." in combined_text or "enable javascript" in combined_text:
            score -= 20.0
            penalties.append("Cloudflare challenge with missing content (-20)")

        final_score = max(0.0, round(score, 1))
        qualified = final_score >= STAGE_1_QUALIFICATION_THRESHOLD

        # Dynamic subpage recommendation for Stage 2 Deep Crawl
        recommended_stage2_pages = []
        if qualified:
            base_url = f"https://{domain.replace('www.', '')}"
            recommended_stage2_pages = [
                f"{base_url}/about",
                f"{base_url}/contact",
                f"{base_url}/products",
                f"{base_url}/team",
                f"{base_url}/technology"
            ]

        return {
            "qualified": qualified,
            "stage1_score": final_score,
            "positive_signals": positive_signals,
            "penalties": penalties,
            "hard_blocked": False,
            "block_reason": None,
            "recommended_stage2_pages": recommended_stage2_pages
        }


company_qualification_engine = CompanyQualificationEngine()
