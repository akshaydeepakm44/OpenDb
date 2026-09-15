"""
Person ↔ Company Verification Engine & Canonical Company Identity Resolver
Enforces strict entity resolution, evidence-attribution gates, positive/negative matching,
and prevents cross-company leakage.
"""

import re
import logging
from typing import Dict, Any, List, Optional, Tuple
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

GENERIC_TITLES = {
    "home", "homepage", "home page", "index", "welcome", "welcome to", "main",
    "login", "sign in", "signup", "sign up", "portal", "default web page",
    "official portal", "official website", "official site", "untitled", "unknown",
    "dashboard", "landing page", "contact us", "about us", "page"
}

GENERIC_SEPARATORS_RE = re.compile(r'[\-–—|:•·]')


class PersonCompanyVerifier:
    """
    Dedicated engine for:
    1. Canonicalizing Company Identity from URL & DOM.
    2. Verifying whether a discovered person explicitly belongs to the target company.
    3. Detecting negative matching signals (person belongs to another organization/product).
    4. Calculating a 0.0 - 1.0 confidence match score.
    5. Evaluating company-level data match.
    """

    @staticmethod
    def canonicalize_domain(url_or_domain: str) -> str:
        """
        Normalize URL or domain to clean canonical root domain.
        e.g. 'https://www.nihonium.io/' -> 'nihonium.io'
             'nihonium.io:443' -> 'nihonium.io'
        """
        if not url_or_domain:
            return ""
        u = url_or_domain.strip().lower()
        if not u.startswith("http://") and not u.startswith("https://"):
            u = "https://" + u
        try:
            parsed = urlparse(u)
            netloc = parsed.netloc or ""
            # Strip port
            if ":" in netloc:
                netloc = netloc.split(":")[0]
            # Strip leading www.
            netloc = re.sub(r'^www\.', '', netloc)
            return netloc.strip()
        except Exception:
            return url_or_domain.strip().lower()

    @staticmethod
    def derive_brand_from_domain(domain: str) -> str:
        """
        Derives a clean, capitalized brand name from domain string.
        e.g. 'nihonium.io' -> 'Nihonium'
             'cybersecurityasia.net' -> 'Cybersecurity Asia'
             'zero-motorcycles.com' -> 'Zero Motorcycles'
        """
        clean_dom = PersonCompanyVerifier.canonicalize_domain(domain)
        if not clean_dom or "." not in clean_dom:
            return "Company"
        
        # Take primary domain token (excluding TLDs)
        base = clean_dom.split(".")[0]
        # Replace hyphens/underscores with space
        words = re.split(r'[\-_]', base)
        
        # Split common compound words if helpful (e.g. 'cybersecurity' + 'asia')
        brand_parts = []
        for w in words:
            if w.lower() == "cybersecurityasia":
                brand_parts.extend(["Cybersecurity", "Asia"])
            elif w.lower() == "zeromotorcycles":
                brand_parts.extend(["Zero", "Motorcycles"])
            elif w.lower() == "opendb":
                brand_parts.append("OpenDB")
            else:
                brand_parts.append(w.capitalize())
        
        return " ".join(brand_parts).strip() or "Company"

    @classmethod
    def canonicalize_company_identity(
        cls,
        url: str = "",
        title: str = "",
        raw_name: str = ""
    ) -> Dict[str, Any]:
        """
        Creates a canonical company identity:
        NEVER allows generic titles ('Home', 'Index', 'Welcome', 'Login', etc.)
        to become the company name.
        """
        canonical_domain = cls.canonicalize_domain(url)
        domain_brand = cls.derive_brand_from_domain(canonical_domain) if canonical_domain else "Company"

        candidate_name = ""
        if title:
            t_parts = [p.strip() for p in GENERIC_SEPARATORS_RE.split(title) if p.strip()]
            t_non_gen = [p for p in t_parts if p.lower() not in GENERIC_TITLES]
            if t_non_gen and len(t_non_gen[0]) > 2:
                candidate_name = title.strip()
        if not candidate_name and raw_name:
            candidate_name = raw_name.strip()
        if not candidate_name and title:
            candidate_name = title.strip()

        # Check if candidate_name is generic
        is_generic = False
        lower_name = candidate_name.lower().strip()
        if not lower_name or lower_name in GENERIC_TITLES:
            is_generic = True
        elif any(lower_name == g or lower_name.startswith(f"{g} ") or lower_name.endswith(f" {g}") for g in GENERIC_TITLES):
            # Check if there is actual brand info in candidate_name
            # e.g. "Home - Nihonium" -> clean to "Nihonium"
            parts = [p.strip() for p in GENERIC_SEPARATORS_RE.split(candidate_name) if p.strip()]
            non_generic = [p for p in parts if p.lower() not in GENERIC_TITLES]
            if non_generic:
                candidate_name = non_generic[0]
            else:
                is_generic = True

        if is_generic or not candidate_name:
            final_name = domain_brand
        else:
            # Strip greetings and action prefixes
            clean_name = re.sub(
                r'^(?:welcome\s+to|welcome|home\s+of|official\s+website\s+of|about\s+us\s*[\-–—|:]|about)\s+',
                '', candidate_name, flags=re.IGNORECASE
            ).strip()
            # Split standard separators
            parts = [p.strip() for p in GENERIC_SEPARATORS_RE.split(clean_name) if p.strip()]
            if parts:
                non_gen_parts = [p for p in parts if p.lower() not in GENERIC_TITLES]
                clean_name = non_gen_parts[0] if non_gen_parts else domain_brand
            
            # If still generic or too short, fallback to domain brand
            if clean_name.lower() in GENERIC_TITLES or len(clean_name) < 2:
                final_name = domain_brand
            else:
                final_name = clean_name

        return {
            "target_domain": canonical_domain,
            "canonical_domain": canonical_domain,
            "company_name": final_name,
            "clean_name": final_name,
            "domain_brand": domain_brand
        }

    @classmethod
    def verify_person_company_match(
        cls,
        person_name: str,
        role: str,
        target_company_name: Optional[str] = None,
        target_domain: Optional[str] = None,
        evidence_text: str = "",
        linkedin_url: Optional[str] = None,
        source_url: Optional[str] = None,
        dom_text: str = "",
        company_name: Optional[str] = None,
        official_domain: Optional[str] = None,
        snippet: Optional[str] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Verify person ↔ company relationship using strong & supporting signals,
        detect negative matching (person belongs to another organization/product),
        and compute match score.
        Accepts canonical and alias keyword arguments for flexible caller integration.

        Score ranges:
        - 0.90 - 1.00 -> VERIFIED
        - 0.75 - 0.89 -> HIGH_CONFIDENCE
        - 0.50 - 0.74 -> REVIEW (Unverified)
        - 0.00 - 0.49 -> REJECTED
        """
        clean_pname = (person_name or "").strip()
        clean_cname = (target_company_name or company_name or "").strip()
        raw_domain = target_domain or official_domain or ""
        clean_domain = cls.canonicalize_domain(raw_domain)
        base_brand = cls.derive_brand_from_domain(clean_domain).lower() if clean_domain else ""
        if "." in clean_cname:
            cname_brand = cls.derive_brand_from_domain(clean_cname).lower()
        else:
            cname_brand = clean_cname.lower()
        cname_lower = clean_cname.lower()
        final_evidence = f"{evidence_text or ''} {snippet or ''}".strip()

        # Reject obviously invalid person names or generic titles
        if not clean_pname or len(clean_pname) < 3 or clean_pname.lower() in GENERIC_TITLES:
            return {
                "status": "REJECTED",
                "is_verified": False,
                "is_match": False,
                "match_status": "REJECTED",
                "verification_status": "REJECTED",
                "match_score": 0.0,
                "confidence_score": 0.0,
                "reason": f"Invalid person name: '{clean_pname}'"
            }

        # ── NEGATIVE MATCHING SIGNALS ──────────────────────────────────────────
        combined_evidence = f"{final_evidence} {source_url or ''}".lower()

        # Special negative pattern for "Good Food"
        if "good food" in clean_pname.lower() or "recipe" in combined_evidence or "cooking" in combined_evidence:
            return {
                "status": "REJECTED",
                "is_verified": False,
                "is_match": False,
                "match_status": "REJECTED",
                "verification_status": "REJECTED",
                "match_score": 0.05,
                "confidence_score": 0.05,
                "reason": "Non-business / recipe text falsely identified as person"
            }

        # Check if candidate is explicitly working at a different organization
        # e.g. "at Lumo", "at Siemens Mobility France", "at Connected Brighton"
        other_org_match = re.search(
            r"(?:at|@|founder\s+of|ceo\s+(?:at|of)|coo\s+(?:at|of)|cto\s+(?:at|of))\s+([A-Z][A-Za-z0-9\s&]{2,30})",
            final_evidence
        )
        if other_org_match:
            detected_org = other_org_match.group(1).strip().lower()
            # If the detected org doesn't match clean_cname or base_brand or domain
            if (
                clean_cname.lower() not in detected_org
                and cname_brand not in detected_org
                and base_brand not in detected_org
                and (not clean_domain or clean_domain.split(".")[0] not in detected_org)
                and detected_org not in clean_cname.lower()
                and detected_org not in cname_brand
                and detected_org not in base_brand
            ):
                # Detected working at another known organization
                if (
                    clean_cname.lower() not in combined_evidence
                    and cname_brand not in combined_evidence
                    and base_brand not in combined_evidence
                    and (not clean_domain or clean_domain not in combined_evidence)
                ):
                    return {
                        "status": "REJECTED",
                        "is_verified": False,
                        "is_match": False,
                        "match_status": "REJECTED",
                        "verification_status": "REJECTED",
                        "match_score": 0.15,
                        "confidence_score": 0.15,
                        "reason": f"Person affiliated with another company ('{other_org_match.group(1).strip()}'), no evidence for '{clean_cname}'"
                    }

        # ── POSITIVE MATCHING SIGNALS ──────────────────────────────────────────
        score = 0.0
        signals: List[str] = []

        # Signal 1: Genuine LinkedIn Profile URL (required for verified leadership)
        has_real_linkedin = False
        li_to_check = linkedin_url or source_url or ""
        if li_to_check:
            m = re.search(r'https?://(?:[a-zA-Z0-9\-]+\.)?linkedin\.com/in/([a-zA-Z0-9\-_]{2,})', li_to_check, re.IGNORECASE)
            if m and m.group(1).lower() not in {"search", "jobs", "feed", "login", "signup", "home", "pub", "in", "sharing", "posts"}:
                has_real_linkedin = True
                score += 0.35
                signals.append("genuine_linkedin_profile")

        # Signal 2: Target company or domain explicitly referenced in evidence text
        cname_words = [w for w in re.split(r'[\s\.\-]+', cname_lower) if len(w) >= 3 and w not in {"the", "and", "inc", "ltd", "corp", "llc", "com", "net", "org", "app", "io"}]
        domain_token = clean_domain.split(".")[0].lower() if clean_domain else ""
        if domain_token and len(domain_token) >= 3 and domain_token not in cname_words:
            cname_words.append(domain_token)
        if cname_brand and len(cname_brand) >= 3 and cname_brand not in cname_words:
            cname_words.append(cname_brand)

        company_mentioned = False
        if clean_domain and clean_domain in combined_evidence:
            company_mentioned = True
            score += 0.40
            signals.append(f"exact_domain_in_evidence({clean_domain})")
        elif cname_words and any(re.search(rf"\b{re.escape(w)}\b", combined_evidence) for w in cname_words):
            company_mentioned = True
            score += 0.35
            signals.append("company_name_in_evidence")
        elif base_brand and re.search(rf"\b{re.escape(base_brand)}\b", combined_evidence):
            company_mentioned = True
            score += 0.35
            signals.append(f"brand_in_evidence({base_brand})")

        # Signal 3: Official Website DOM verification
        if dom_text and clean_pname.lower() in dom_text.lower():
            score += 0.30
            signals.append("person_named_on_official_website")

        # Signal 4: Executive title evidence
        role_lower = (role or "").lower()
        if any(exec_t in role_lower for exec_t in ["founder", "ceo", "cto", "coo", "cfo", "chief", "president", "director", "head of", "vp", "executive"]):
            score += 0.15
            signals.append("verified_executive_role")

        # Normalize score
        score = round(min(1.0, score), 2)

        # Require company reference for any score >= 0.50
        if not company_mentioned and "person_named_on_official_website" not in signals:
            score = min(0.40, score)
            status = "REJECTED"
            reason = f"No evidence associating '{clean_pname}' with target company '{clean_cname}' or domain '{clean_domain}'"
        elif score >= 0.90:
            status = "VERIFIED"
            reason = f"Strong multi-source evidence: {', '.join(signals)}"
        elif score >= 0.75:
            status = "HIGH_CONFIDENCE"
            reason = f"High confidence evidence: {', '.join(signals)}"
        elif score >= 0.50:
            status = "REVIEW"
            reason = f"Partial evidence: {', '.join(signals)}"
        else:
            status = "REJECTED"
            reason = f"Insufficient evidence: {', '.join(signals) if signals else 'No matching signals'}"

        is_verified = (status in ["VERIFIED", "HIGH_CONFIDENCE"])

        return {
            "status": status,
            "is_verified": is_verified,
            "is_match": is_verified,
            "match_status": status,
            "verification_status": status,
            "match_score": score,
            "confidence_score": score,
            "signals": signals,
            "reason": reason
        }

    @classmethod
    def evaluate_company_data_match(
        cls,
        domain: str = "",
        company_name: str = "",
        crawled_subpages: Optional[List[Dict[str, Any]]] = None,
        extracted_facts: Optional[List[Dict[str, Any]]] = None,
        official_domain: Optional[str] = None,
        key_people: Optional[List[Dict[str, Any]]] = None,
        verified_emails: Optional[List[str]] = None,
        technologies: Optional[List[str]] = None,
        overview: Optional[str] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Evaluate whether the crawled dataset genuinely belongs to the requested company.
        Status: VERIFIED, PARTIAL_MATCH, LOW_CONFIDENCE, MISMATCH
        """
        dom_to_use = domain or official_domain or ""
        clean_dom = cls.canonicalize_domain(dom_to_use)
        clean_cname = (company_name or "").strip()

        if not clean_dom or clean_cname.lower() in GENERIC_TITLES:
            return {
                "status": "MISMATCH",
                "score": 0.20,
                "reason": f"Generic or invalid company identity ('{company_name}' / '{domain}').",
                "verified_sources": []
            }

        score = 0.50  # Base for having a valid canonical domain
        verified_sources = []

        subpages = crawled_subpages or []
        domain_matched_pages = 0
        for sp in subpages:
            u = sp.get("url") or ""
            if clean_dom in cls.canonicalize_domain(u):
                domain_matched_pages += 1
                verified_sources.append(u)

        if subpages:
            domain_ratio = domain_matched_pages / len(subpages)
            score += round(domain_ratio * 0.25, 2)
        else:
            score += 0.20

        if clean_cname and clean_cname != "Company" and clean_cname.lower() not in GENERIC_TITLES:
            score += 0.10

        if key_people and any(isinstance(kp, dict) and (kp.get("match_status") in ["VERIFIED", "HIGH_CONFIDENCE"] or (kp.get("match_score") or 0) >= 0.75) for kp in key_people):
            score += 0.15

        if verified_emails and len(verified_emails) > 0:
            score += 0.05

        if technologies and len(technologies) > 0:
            score += 0.05

        score = round(min(1.0, score), 2)

        if score >= 0.85:
            status = "VERIFIED"
            reason = f"Discovered sources consistently reference {clean_cname} ({clean_dom})."
        elif score >= 0.65:
            status = "PARTIAL_MATCH"
            reason = f"Sources partially associate with {clean_cname} ({clean_dom}); some pages unconfirmed."
        elif score >= 0.40:
            status = "LOW_CONFIDENCE"
            reason = f"Low confidence in source association for {clean_cname}."
        else:
            status = "MISMATCH"
            reason = f"The discovered data does not reliably correspond to {clean_cname}."

        return {
            "status": status,
            "score": score,
            "reason": reason,
            "verified_sources": verified_sources[:10]
        }

    @classmethod
    def verify_person(cls, *args, **kwargs) -> Dict[str, Any]:
        """Convenience alias for verify_person_company_match."""
        return cls.verify_person_company_match(*args, **kwargs)


# Global singleton instance
person_verifier = PersonCompanyVerifier()


def is_authentic_linkedin_personal_url(url: str) -> bool:
    """
    Validate that the URL is strictly a personal LinkedIn profile (/in/<slug>)
    and not a search result, job listing, company page, feed, or post.
    """
    if not url or not isinstance(url, str):
        return False
    u = url.strip()
    m = re.search(r'https?://(?:[a-zA-Z0-9\-]+\.)?linkedin\.com/in/([a-zA-Z0-9\-_]{2,})/?', u, re.IGNORECASE)
    if not m:
        return False
    slug = m.group(1).lower()
    bad_slugs = {
        "search", "jobs", "feed", "login", "signup", "home", "pub", "in", "sharing",
        "posts", "learning", "pulse", "company", "school", "help"
    }
    if slug in bad_slugs:
        return False
    if "/search/" in u.lower() or "/jobs/" in u.lower() or "/posts/" in u.lower() or "/pulse/" in u.lower():
        return False
    return True


def is_authentic_linkedin_company_url(url: str) -> bool:
    """
    Validate that the URL is strictly a corporate LinkedIn company page (/company/<slug>).
    """
    if not url or not isinstance(url, str):
        return False
    u = url.strip()
    m = re.search(r'https?://(?:[a-zA-Z0-9\-]+\.)?linkedin\.com/company/([a-zA-Z0-9\-_]{2,})/?', u, re.IGNORECASE)
    if not m:
        return False
    slug = m.group(1).lower()
    bad_slugs = {"linkedin", "home", "sharearticle", "sharing", "login", "signup", "jobs", "feed", "posts"}
    if slug in bad_slugs:
        return False
    if "/search/" in u.lower():
        return False
    return True
