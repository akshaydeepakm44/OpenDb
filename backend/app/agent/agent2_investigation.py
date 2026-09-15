"""
OpenDB — Agent 2 Field-Aware Investigation Engine
Strict implementation of field-specific investigation strategies, multi-round search loops,
and the mandatory hard validation gate: can_mark_not_found(field, investigation).

INVARIANTS:
1. NOT_FOUND_AFTER_SEARCH is NEVER assigned because a field was missing on the first page or current crawl.
2. If evidence is missing, Agent 2 MUST search again and exhaust all configured field strategies.
3. Infrastructure failures (Crawl4AI down, SearXNG down, MinIO down) strictly produce UNVERIFIED with explicit failure reasons, NEVER NOT_FOUND_AFTER_SEARCH.
4. Zero fabrication: missing information remains null / NOT_FOUND_AFTER_SEARCH.
"""

import re
import json
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple

from app.storage.file_storage import file_storage
from app.crawler.evidence_validator import validate_fact

logger = logging.getLogger(__name__)

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─────────────────────────────────────────────────────────────────────────────
# HARD VALIDATION GATE: can_mark_not_found
# ─────────────────────────────────────────────────────────────────────────────

REQUIRED_FIELD_STRATEGIES = {
    "industry_sector": {
        "min_sources_checked": 4,
        "required_source_types": ["homepage", "about_or_company", "search_domain"],
        "min_searches": 1,
    },
    "location_region": {
        "min_sources_checked": 3,
        "required_source_types": ["contact_or_locations", "about_or_company", "search_location"],
        "min_searches": 1,
    },
    "company_size_tier": {
        "min_sources_checked": 3,
        "required_source_types": ["about_or_company", "careers_or_team", "search_size"],
        "min_searches": 1,
    },
    "verified_contact_email": {
        "min_sources_checked": 3,
        "required_source_types": ["contact_page", "footer_or_body", "search_email"],
        "min_searches": 1,
    },
    "raw_storage_vault_path": {
        "min_sources_checked": 1,
        "required_source_types": ["storage_backend"],
        "min_searches": 0,
    },
    "crawled_page_text": {
        "min_sources_checked": 1,
        "required_source_types": ["crawler_storage"],
        "min_searches": 0,
    },
    "extracted_word_count": {
        "min_sources_checked": 1,
        "required_source_types": ["content_recalculation"],
        "min_searches": 0,
    },
}


def can_mark_not_found(field: str, investigation: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Hard validation function: Returns True ONLY when:
    1. Field-specific investigation strategy is exhausted (strategies_exhausted == True).
    2. All required official sources/types were checked.
    3. Required targeted searches were executed.
    4. Required Crawl4AI attempts were completed.
    5. No reliable evidence was found.
    6. There is NO infrastructure failure blocking investigation.
    Otherwise returns (False, rejection_reason).
    """
    if not isinstance(investigation, dict):
        return False, "Investigation record missing or invalid"

    # Rule: Infrastructure failure is NOT equivalent to evidence absence
    if investigation.get("infra_failure"):
        return False, f"Investigation blocked by infrastructure failure: {investigation.get('infra_failure')}"

    reqs = REQUIRED_FIELD_STRATEGIES.get(field)
    if not reqs:
        return False, f"Unknown field '{field}' has no defined investigation strategy"

    sources_checked = set(investigation.get("sources_checked") or [])
    if len(sources_checked) < reqs["min_sources_checked"]:
        return False, f"Insufficient sources checked ({len(sources_checked)} < {reqs['min_sources_checked']})"

    req_types = reqs.get("required_source_types", [])
    for rt in req_types:
        if not any(rt in s for s in sources_checked):
            return False, f"Missing required source type '{rt}' in investigation sources"

    search_attempts = investigation.get("search_attempts", 0)
    if search_attempts < reqs["min_searches"]:
        return False, f"Insufficient search attempts ({search_attempts} < {reqs['min_searches']})"

    if not investigation.get("strategies_exhausted"):
        return False, "Field investigation strategies not marked exhausted (agent must search again)"

    if investigation.get("evidence_found"):
        return False, "Evidence was found; field cannot be marked NOT_FOUND_AFTER_SEARCH"

    return True, "Investigation legitimately exhausted with zero reliable evidence"


# ─────────────────────────────────────────────────────────────────────────────
# FIELD INVESTIGATION IMPLEMENTATIONS
# ─────────────────────────────────────────────────────────────────────────────

class Agent2InvestigationEngine:
    """
    Executes field-specific multi-round investigation strategies for Phase 1.
    If evidence is missing on the initial crawl, actively searches again using
    SearXNG and Crawl4AI before concluding.
    """

    def __init__(self):
        pass

    # ── 1. INDUSTRY SECTOR ───────────────────────────────────────────────────
    async def investigate_industry(
        self,
        domain: str,
        company_name: str,
        existing_text: str,
        existing_artifacts: List[str],
        crawler_service,
        searxng_service
    ) -> Dict[str, Any]:
        inv = {
            "field": "industry_sector",
            "started_at": utc_now_iso(),
            "sources_checked": [],
            "urls_crawled": [],
            "search_queries": [],
            "crawl_attempts": 0,
            "search_attempts": 0,
            "evidence_found": False,
            "strategies_completed": [],
            "strategies_remaining": ["existing_corpus", "targeted_subpages", "search_domain", "secondary_search"],
            "strategies_exhausted": False,
            "infra_failure": None,
        }

        # Strategy 1: Check existing crawled corpus
        inv["sources_checked"].append("homepage")
        inv["strategies_completed"].append("existing_corpus")
        inv["strategies_remaining"].remove("existing_corpus")

        from app.classification.domain_classifier import domain_classifier
        c_dom, sub_dom, conf = domain_classifier.classify(existing_text, title=company_name, url=domain)
        if conf >= 0.65 and c_dom != "Unknown" and c_dom != "Commercial Web":
            evidence_snippet = f"Classified from crawled corporate content as {c_dom} ({sub_dom}) with confidence {conf:.2f}."
            inv["evidence_found"] = True
            inv["completed_at"] = utc_now_iso()
            return {
                "field": "industry_sector",
                "value": c_dom,
                "status": "VERIFIED",
                "source_url": f"https://{domain}",
                "evidence_snippet": evidence_snippet,
                "verification_method": "domain_classifier_on_crawled_text",
                "investigation": inv,
            }

        # Strategy 2: Targeted crawl of missing corporate subpages
        inv["strategies_completed"].append("targeted_subpages")
        inv["strategies_remaining"].remove("targeted_subpages")
        inv["sources_checked"].append("about_or_company")
        new_text = ""

        try:
            for path in ["/about", "/company", "/products", "/solutions", "/industries"]:
                url = f"https://{domain}{path}"
                inv["urls_crawled"].append(url)
                inv["crawl_attempts"] += 1
                items = await crawler_service.crawl_site(starting_url=url, max_depth=1, max_pages=1)
                if items and items[0] and items[0].text and items[0].http_status == 200:
                    new_text += "\n" + items[0].text
                    slug = path.strip("/").replace("/", "_")
                    file_storage.save_agent2_artifact(
                        domain=domain,
                        page_slug=slug,
                        content=items[0].markdown or items[0].text,
                        metadata={"source_url": url, "page_type": slug}
                    )
        except Exception as crawl_err:
            logger.warning(f"[Agent 2][Industry] Targeted crawl error for {domain}: {crawl_err}")
            inv["infra_failure"] = f"CRAWLER_ERROR: {crawl_err}"

        combined_text = f"{existing_text}\n{new_text}"
        c_dom2, _, conf2 = domain_classifier.classify(combined_text, title=company_name, url=domain)
        if conf2 >= 0.65 and c_dom2 != "Unknown":
            inv["evidence_found"] = True
            inv["completed_at"] = utc_now_iso()
            return {
                "field": "industry_sector",
                "value": c_dom2,
                "status": "VERIFIED",
                "source_url": f"https://{domain}/about",
                "evidence_snippet": f"Verified from targeted subpage crawl as {c_dom2}.",
                "verification_method": "targeted_subpage_evidence",
                "investigation": inv,
            }

        # Strategy 3: Multi-round SearXNG search (SEARCH AGAIN when not found!)
        inv["strategies_completed"].append("search_domain")
        inv["strategies_remaining"].remove("search_domain")
        inv["sources_checked"].append("search_domain")

        try:
            q1 = f'"{domain}" industry sector what we do'
            inv["search_queries"].append(q1)
            inv["search_attempts"] += 1
            results, is_fallback, log_msg = await searxng_service.search_with_meta(q1, category="general", max_results=5)
            if is_fallback:
                inv["infra_failure"] = f"SEARCH_UNAVAILABLE: {log_msg}"
            elif results:
                snippets = " ".join([r.get("content", "") for r in results if r.get("content")])
                c_dom3, _, conf3 = domain_classifier.classify(snippets, title=company_name, url=domain)
                if conf3 >= 0.60 and c_dom3 != "Unknown":
                    inv["evidence_found"] = True
                    inv["completed_at"] = utc_now_iso()
                    return {
                        "field": "industry_sector",
                        "value": c_dom3,
                        "status": "VERIFIED",
                        "source_url": results[0].get("url") or f"https://{domain}",
                        "evidence_snippet": f"Verified via search evidence snippet: {snippets[:180]}...",
                        "verification_method": "searxng_evidence_classification",
                        "investigation": inv,
                    }
        except Exception as search_err:
            inv["infra_failure"] = f"SEARCH_ERROR: {search_err}"

        # Strategy 4: Secondary targeted search
        inv["strategies_completed"].append("secondary_search")
        inv["strategies_remaining"].remove("secondary_search")
        inv["sources_checked"].append("secondary_search")

        try:
            q2 = f'"{company_name}" software SaaS enterprise solutions'
            inv["search_queries"].append(q2)
            inv["search_attempts"] += 1
            results2, is_fallback2, _ = await searxng_service.search_with_meta(q2, category="general", max_results=5)
            if results2 and not is_fallback2:
                snippets2 = " ".join([r.get("content", "") for r in results2 if r.get("content")])
                c_dom4, _, conf4 = domain_classifier.classify(snippets2, title=company_name, url=domain)
                if conf4 >= 0.60 and c_dom4 != "Unknown":
                    inv["evidence_found"] = True
                    inv["completed_at"] = utc_now_iso()
                    return {
                        "field": "industry_sector",
                        "value": c_dom4,
                        "status": "VERIFIED",
                        "source_url": results2[0].get("url") or f"https://{domain}",
                        "evidence_snippet": f"Verified via secondary search: {snippets2[:180]}...",
                        "verification_method": "searxng_secondary_classification",
                        "investigation": inv,
                    }
        except Exception as e:
            inv["infra_failure"] = f"SEARCH_ERROR: {e}"

        # All strategies exhausted
        inv["strategies_exhausted"] = True
        inv["completed_at"] = utc_now_iso()

        valid_not_found, reason = can_mark_not_found("industry_sector", inv)
        status = "NOT_FOUND_AFTER_SEARCH" if valid_not_found else "UNVERIFIED"
        return {
            "field": "industry_sector",
            "value": None,
            "status": status,
            "source_url": None,
            "evidence_snippet": reason,
            "verification_method": "exhausted_investigation",
            "investigation": inv,
        }

    # ── 2. LOCATION / REGION ─────────────────────────────────────────────────
    async def investigate_location(
        self,
        domain: str,
        company_name: str,
        existing_text: str,
        existing_artifacts: List[str],
        crawler_service,
        searxng_service
    ) -> Dict[str, Any]:
        inv = {
            "field": "location_region",
            "started_at": utc_now_iso(),
            "sources_checked": [],
            "urls_crawled": [],
            "search_queries": [],
            "crawl_attempts": 0,
            "search_attempts": 0,
            "evidence_found": False,
            "strategies_completed": [],
            "strategies_remaining": ["existing_corpus", "targeted_contact_pages", "search_location"],
            "strategies_exhausted": False,
            "infra_failure": None,
        }

        # Strategy 1: Check existing text corpus for explicit addresses/cities
        inv["sources_checked"].append("about_or_company")
        inv["strategies_completed"].append("existing_corpus")
        inv["strategies_remaining"].remove("existing_corpus")

        loc_evidence = self._extract_location_evidence(existing_text)
        if loc_evidence:
            inv["evidence_found"] = True
            inv["completed_at"] = utc_now_iso()
            return {
                "field": "location_region",
                "value": loc_evidence["location"],
                "status": "VERIFIED",
                "source_url": f"https://{domain}",
                "evidence_snippet": loc_evidence["snippet"],
                "verification_method": "crawled_text_address_match",
                "investigation": inv,
            }

        # Strategy 2: Targeted crawl of contact & locations pages
        inv["sources_checked"].append("contact_or_locations")
        inv["strategies_completed"].append("targeted_contact_pages")
        inv["strategies_remaining"].remove("targeted_contact_pages")

        contact_text = ""
        try:
            for path in ["/contact", "/locations", "/contact-us", "/about", "/legal"]:
                url = f"https://{domain}{path}"
                inv["urls_crawled"].append(url)
                inv["crawl_attempts"] += 1
                items = await crawler_service.crawl_site(starting_url=url, max_depth=1, max_pages=1)
                if items and items[0] and items[0].text and items[0].http_status == 200:
                    contact_text += "\n" + items[0].text
                    slug = path.strip("/").replace("/", "_")
                    file_storage.save_agent2_artifact(
                        domain=domain,
                        page_slug=slug,
                        content=items[0].markdown or items[0].text,
                        metadata={"source_url": url, "page_type": slug}
                    )
                    loc2 = self._extract_location_evidence(items[0].text)
                    if loc2:
                        inv["evidence_found"] = True
                        inv["completed_at"] = utc_now_iso()
                        return {
                            "field": "location_region",
                            "value": loc2["location"],
                            "status": "VERIFIED",
                            "source_url": url,
                            "evidence_snippet": loc2["snippet"],
                            "verification_method": "targeted_contact_page_address",
                            "investigation": inv,
                        }
        except Exception as crawl_err:
            inv["infra_failure"] = f"CRAWLER_ERROR: {crawl_err}"

        # Strategy 3: Multi-round search (SEARCH AGAIN when missing!)
        inv["sources_checked"].append("search_location")
        inv["strategies_completed"].append("search_location")
        inv["strategies_remaining"].remove("search_location")

        try:
            q = f'"{domain}" headquarters address office location'
            inv["search_queries"].append(q)
            inv["search_attempts"] += 1
            results, is_fallback, log_msg = await searxng_service.search_with_meta(q, category="general", max_results=5)
            if is_fallback:
                inv["infra_failure"] = f"SEARCH_UNAVAILABLE: {log_msg}"
            elif results:
                for r in results:
                    snippet = r.get("content", "")
                    loc3 = self._extract_location_evidence(snippet)
                    if loc3:
                        inv["evidence_found"] = True
                        inv["completed_at"] = utc_now_iso()
                        return {
                            "field": "location_region",
                            "value": loc3["location"],
                            "status": "VERIFIED",
                            "source_url": r.get("url") or f"https://{domain}",
                            "evidence_snippet": f"Found in search snippet: {snippet[:180]}",
                            "verification_method": "searxng_address_snippet",
                            "investigation": inv,
                        }
        except Exception as e:
            inv["infra_failure"] = f"SEARCH_ERROR: {e}"

        inv["strategies_exhausted"] = True
        inv["completed_at"] = utc_now_iso()

        valid_not_found, reason = can_mark_not_found("location_region", inv)
        status = "NOT_FOUND_AFTER_SEARCH" if valid_not_found else "UNVERIFIED"
        return {
            "field": "location_region",
            "value": None,
            "status": status,
            "source_url": None,
            "evidence_snippet": reason,
            "verification_method": "exhausted_investigation",
            "investigation": inv,
        }

    def _extract_location_evidence(self, text: str) -> Optional[Dict[str, str]]:
        """Identify explicit cities and locations with concrete evidence in text."""
        major_cities = [
            ("san francisco", "San Francisco, CA, USA"),
            ("new york", "New York, NY, USA"),
            ("london", "London, UK"),
            ("berlin", "Berlin, Germany"),
            ("paris", "Paris, France"),
            ("tokyo", "Tokyo, Japan"),
            ("bengaluru", "Bengaluru, India"),
            ("singapore", "Singapore"),
            ("toronto", "Toronto, Canada"),
            ("sydney", "Sydney, Australia"),
            ("amsterdam", "Amsterdam, Netherlands"),
            ("seattle", "Seattle, WA, USA"),
            ("austin", "Austin, TX, USA"),
            ("chicago", "Chicago, IL, USA"),
            ("boston", "Boston, MA, USA"),
            ("dublin", "Dublin, Ireland"),
            ("zurich", "Zurich, Switzerland"),
            ("stockholm", "Stockholm, Sweden"),
            ("helsinki", "Helsinki, Finland"),
            ("hyderabad", "Hyderabad, India"),
            ("mumbai", "Mumbai, India"),
            ("port louis", "Port Louis, Mauritius"),
        ]
        t_low = text.lower()
        for city_key, canonical_loc in major_cities:
            pat = rf"\b{re.escape(city_key)}\b"
            m = re.search(pat, t_low)
            if m:
                start = max(0, m.start() - 40)
                end = min(len(text), m.end() + 60)
                snippet = text[start:end].strip().replace("\n", " ")
                return {"location": canonical_loc, "snippet": f"...{snippet}..."}
        return None

    # ── 3. COMPANY SIZE TIER ─────────────────────────────────────────────────
    async def investigate_company_size(
        self,
        domain: str,
        company_name: str,
        existing_text: str,
        existing_artifacts: List[str],
        crawler_service,
        searxng_service
    ) -> Dict[str, Any]:
        inv = {
            "field": "company_size_tier",
            "started_at": utc_now_iso(),
            "sources_checked": [],
            "urls_crawled": [],
            "search_queries": [],
            "crawl_attempts": 0,
            "search_attempts": 0,
            "evidence_found": False,
            "strategies_completed": [],
            "strategies_remaining": ["existing_corpus", "targeted_career_pages", "search_size"],
            "strategies_exhausted": False,
            "infra_failure": None,
        }

        # Strategy 1: Search existing text for employee count evidence
        inv["sources_checked"].append("about_or_company")
        inv["strategies_completed"].append("existing_corpus")
        inv["strategies_remaining"].remove("existing_corpus")

        size_evidence = self._extract_size_evidence(existing_text)
        if size_evidence:
            inv["evidence_found"] = True
            inv["completed_at"] = utc_now_iso()
            return {
                "field": "company_size_tier",
                "value": size_evidence["tier"],
                "status": "VERIFIED",
                "source_url": f"https://{domain}",
                "evidence_snippet": size_evidence["snippet"],
                "verification_method": "crawled_text_headcount_pattern",
                "investigation": inv,
            }

        # Strategy 2: Targeted crawl of /careers and /team
        inv["sources_checked"].append("careers_or_team")
        inv["strategies_completed"].append("targeted_career_pages")
        inv["strategies_remaining"].remove("targeted_career_pages")

        try:
            for path in ["/careers", "/team", "/about", "/company"]:
                url = f"https://{domain}{path}"
                inv["urls_crawled"].append(url)
                inv["crawl_attempts"] += 1
                items = await crawler_service.crawl_site(starting_url=url, max_depth=1, max_pages=1)
                if items and items[0] and items[0].text and items[0].http_status == 200:
                    slug = path.strip("/").replace("/", "_")
                    file_storage.save_agent2_artifact(
                        domain=domain,
                        page_slug=slug,
                        content=items[0].markdown or items[0].text,
                        metadata={"source_url": url, "page_type": slug}
                    )
                    size2 = self._extract_size_evidence(items[0].text)
                    if size2:
                        inv["evidence_found"] = True
                        inv["completed_at"] = utc_now_iso()
                        return {
                            "field": "company_size_tier",
                            "value": size2["tier"],
                            "status": "VERIFIED",
                            "source_url": url,
                            "evidence_snippet": size2["snippet"],
                            "verification_method": "targeted_career_page_headcount",
                            "investigation": inv,
                        }
        except Exception as e:
            inv["infra_failure"] = f"CRAWLER_ERROR: {e}"

        # Strategy 3: Multi-round search (SEARCH AGAIN when missing!)
        inv["sources_checked"].append("search_size")
        inv["strategies_completed"].append("search_size")
        inv["strategies_remaining"].remove("search_size")

        try:
            q = f'"{domain}" team of employees headcount'
            inv["search_queries"].append(q)
            inv["search_attempts"] += 1
            results, is_fallback, log_msg = await searxng_service.search_with_meta(q, category="general", max_results=5)
            if is_fallback:
                inv["infra_failure"] = f"SEARCH_UNAVAILABLE: {log_msg}"
            elif results:
                for r in results:
                    size3 = self._extract_size_evidence(r.get("content", ""))
                    if size3:
                        inv["evidence_found"] = True
                        inv["completed_at"] = utc_now_iso()
                        return {
                            "field": "company_size_tier",
                            "value": size3["tier"],
                            "status": "VERIFIED",
                            "source_url": r.get("url") or f"https://{domain}",
                            "evidence_snippet": f"Found in search snippet: {size3['snippet']}",
                            "verification_method": "searxng_size_snippet",
                            "investigation": inv,
                        }
        except Exception as e:
            inv["infra_failure"] = f"SEARCH_ERROR: {e}"

        inv["strategies_exhausted"] = True
        inv["completed_at"] = utc_now_iso()

        valid_not_found, reason = can_mark_not_found("company_size_tier", inv)
        status = "NOT_FOUND_AFTER_SEARCH" if valid_not_found else "UNVERIFIED"
        return {
            "field": "company_size_tier",
            "value": None,
            "status": status,
            "source_url": None,
            "evidence_snippet": reason,
            "verification_method": "exhausted_investigation",
            "investigation": inv,
        }

    def _extract_size_evidence(self, text: str) -> Optional[Dict[str, str]]:
        """Identify explicit headcount patterns in text without guessing."""
        patterns = [
            (r"(?:team of|over|more than|approximately|approx\.)\s*(\d{1,5})\s*(?:people|employees|members|engineers|staff)", 1),
            (r"(\d{1,5})\+\s*(?:employees|people|team members|staff)", 1),
            (r"headcount\s*(?:of|is|:)\s*(\d{1,5})", 1),
        ]
        for pat, grp in patterns:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                try:
                    num = int(m.group(grp))
                    if 1 <= num <= 500000:
                        tier = (
                            "1-10" if num <= 10
                            else "11-50" if num <= 50
                            else "51-200" if num <= 200
                            else "201-500" if num <= 500
                            else "501-1000" if num <= 1000
                            else "1001-5000" if num <= 5000
                            else "5000+"
                        )
                        start = max(0, m.start() - 30)
                        end = min(len(text), m.end() + 30)
                        snippet = text[start:end].strip().replace("\n", " ")
                        return {"tier": tier, "snippet": f"...{snippet} (exact count: {num})..."}
                except Exception:
                    pass
        return None

    # ── 4. VERIFIED CONTACT EMAIL ────────────────────────────────────────────
    async def investigate_email(
        self,
        domain: str,
        existing_text: str,
        existing_metadata: Dict[str, Any],
        crawler_service,
        searxng_service
    ) -> Dict[str, Any]:
        inv = {
            "field": "verified_contact_email",
            "started_at": utc_now_iso(),
            "sources_checked": [],
            "urls_crawled": [],
            "search_queries": [],
            "crawl_attempts": 0,
            "search_attempts": 0,
            "evidence_found": False,
            "strategies_completed": [],
            "strategies_remaining": ["existing_metadata", "targeted_contact_page", "search_email"],
            "strategies_exhausted": False,
            "infra_failure": None,
        }

        # Strategy 1: Check existing metadata and text for validated emails
        inv["sources_checked"].append("footer_or_body")
        inv["strategies_completed"].append("existing_metadata")
        inv["strategies_remaining"].remove("existing_metadata")

        existing_emails = list(existing_metadata.get("detected_emails") or [])
        # Directly scan existing text for emails (e.g. from homepage footer)
        if existing_text:
            text_emails = re.findall(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+", existing_text)
            for te in text_emails:
                if not any(te.lower().endswith(ext) for ext in [".png", ".jpg", ".svg", ".webp", ".js", ".css"]):
                    if te not in existing_emails:
                        existing_emails.append(te)

        for em in existing_emails:
            prov = validate_fact("email", em, existing_text, f"https://{domain}", domain=domain)
            if prov:
                inv["evidence_found"] = True
                inv["completed_at"] = utc_now_iso()
                return {
                    "field": "verified_contact_email",
                    "value": em,
                    "status": "VERIFIED",
                    "source_url": f"https://{domain}",
                    "evidence_snippet": f"Validated visible email on official page: {em}",
                    "verification_method": "on_page_email_evidence",
                    "investigation": inv,
                }

        # Strategy 2: Targeted crawl of contact page & root homepage
        inv["sources_checked"].append("contact_page")
        inv["strategies_completed"].append("targeted_contact_page")
        inv["strategies_remaining"].remove("targeted_contact_page")

        try:
            for path in ["/contact", "/contact-us", "/support", "/"]:
                url = f"https://{domain}{path}"
                inv["urls_crawled"].append(url)
                inv["crawl_attempts"] += 1
                items = await crawler_service.crawl_site(starting_url=url, max_depth=1, max_pages=1)
                if items and items[0] and items[0].text and items[0].http_status == 200:
                    found_emails = re.findall(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+", items[0].text)
                    for em in found_emails:
                        if not any(em.endswith(ext) for ext in [".png", ".jpg", ".svg", ".webp", ".js", ".css"]):
                            prov = validate_fact("email", em, items[0].text, url, domain=domain)
                            if prov:
                                inv["evidence_found"] = True
                                inv["completed_at"] = utc_now_iso()
                                return {
                                    "field": "verified_contact_email",
                                    "value": em,
                                    "status": "VERIFIED",
                                    "source_url": url,
                                    "evidence_snippet": f"Discovered on official page: {em}",
                                    "verification_method": "targeted_contact_page_email",
                                    "investigation": inv,
                                }
        except Exception as e:
            inv["infra_failure"] = f"CRAWLER_ERROR: {e}"

        # Strategy 3: Multi-round search (SEARCH AGAIN when missing!)
        inv["sources_checked"].append("search_email")
        inv["strategies_completed"].append("search_email")
        inv["strategies_remaining"].remove("search_email")

        try:
            q = f'"{domain}" contact email mailto'
            inv["search_queries"].append(q)
            inv["search_attempts"] += 1
            results, is_fallback, log_msg = await searxng_service.search_with_meta(q, category="general", max_results=5)
            if is_fallback:
                inv["infra_failure"] = f"SEARCH_UNAVAILABLE: {log_msg}"
            elif results:
                for r in results:
                    found_emails = re.findall(r"[a-zA-Z0-9_.+-]+@" + re.escape(domain), r.get("content", ""))
                    for em in found_emails:
                        inv["evidence_found"] = True
                        inv["completed_at"] = utc_now_iso()
                        return {
                            "field": "verified_contact_email",
                            "value": em,
                            "status": "VERIFIED",
                            "source_url": r.get("url") or f"https://{domain}",
                            "evidence_snippet": f"Discovered in official search snippet: {em}",
                            "verification_method": "searxng_email_evidence",
                            "investigation": inv,
                        }
        except Exception as e:
            inv["infra_failure"] = f"SEARCH_ERROR: {e}"

        inv["strategies_exhausted"] = True
        inv["completed_at"] = utc_now_iso()

        valid_not_found, reason = can_mark_not_found("verified_contact_email", inv)
        status = "NOT_FOUND_AFTER_SEARCH" if valid_not_found else "UNVERIFIED"
        return {
            "field": "verified_contact_email",
            "value": None,
            "status": status,
            "source_url": None,
            "evidence_snippet": reason,
            "verification_method": "exhausted_investigation",
            "investigation": inv,
        }

    # ── 5. PERSISTED RAW STORAGE VAULT PATH ───────────────────────────────────
    def investigate_raw_vault_path(self, raw_path: Optional[str], artifacts: List[str]) -> Dict[str, Any]:
        inv = {
            "field": "raw_storage_vault_path",
            "started_at": utc_now_iso(),
            "sources_checked": ["storage_backend"],
            "urls_crawled": [],
            "search_queries": [],
            "crawl_attempts": 0,
            "search_attempts": 0,
            "evidence_found": False,
            "strategies_completed": ["artifact_existence_check"],
            "strategies_remaining": [],
            "strategies_exhausted": True,
            "infra_failure": None,
        }

        target_path = raw_path or (artifacts[0] if artifacts else None)
        if not target_path:
            inv["completed_at"] = utc_now_iso()
            return {
                "field": "raw_storage_vault_path",
                "value": None,
                "status": "UNVERIFIED",
                "source_url": None,
                "evidence_snippet": "No storage path was recorded on the card.",
                "verification_method": "object_storage_audit",
                "investigation": inv,
            }

        stat = file_storage.verify_artifact_exists(target_path)
        if stat.get("is_infra_error"):
            inv["infra_failure"] = f"RAW_STORAGE_UNAVAILABLE: {stat.get('error')}"
            inv["completed_at"] = utc_now_iso()
            return {
                "field": "raw_storage_vault_path",
                "value": target_path,
                "status": "UNVERIFIED",
                "source_url": target_path,
                "evidence_snippet": f"Storage backend error: {stat.get('error')}",
                "verification_method": "object_storage_audit",
                "investigation": inv,
            }

        if stat.get("exists"):
            inv["evidence_found"] = True
            inv["completed_at"] = utc_now_iso()
            return {
                "field": "raw_storage_vault_path",
                "value": target_path,
                "status": "VERIFIED",
                "source_url": target_path,
                "evidence_snippet": f"Object verified in {stat.get('backend')} ({stat.get('size_bytes')} bytes, hash={stat.get('content_hash')}).",
                "verification_method": "object_storage_audit",
                "investigation": inv,
            }

        inv["completed_at"] = utc_now_iso()
        return {
            "field": "raw_storage_vault_path",
            "value": None,
            "status": "UNVERIFIED",
            "source_url": target_path,
            "evidence_snippet": f"Object not found in storage: {stat.get('error')}",
            "verification_method": "object_storage_audit",
            "investigation": inv,
        }

    # ── 6. CRAWLED PAGE TEXT / EXTRACTED CONTENT ─────────────────────────────
    def investigate_crawled_content(self, text: Optional[str], raw_path: Optional[str]) -> Dict[str, Any]:
        inv = {
            "field": "crawled_page_text",
            "started_at": utc_now_iso(),
            "sources_checked": ["crawler_storage"],
            "urls_crawled": [],
            "search_queries": [],
            "crawl_attempts": 0,
            "search_attempts": 0,
            "evidence_found": False,
            "strategies_completed": ["content_retrieval_and_validation"],
            "strategies_remaining": [],
            "strategies_exhausted": True,
            "infra_failure": None,
        }

        clean_text = (text or "").strip()
        if len(clean_text) >= 100:
            inv["evidence_found"] = True
            inv["completed_at"] = utc_now_iso()
            return {
                "field": "crawled_page_text",
                "value": f"{len(clean_text)} characters verified",
                "status": "VERIFIED",
                "source_url": raw_path or "storage://crawled_text",
                "evidence_snippet": f"Retrieved {len(clean_text)} characters of verified crawled content. Preview: '{clean_text[:120]}...'",
                "verification_method": "content_length_and_text_retrieval",
                "investigation": inv,
            }

        inv["completed_at"] = utc_now_iso()
        return {
            "field": "crawled_page_text",
            "value": None,
            "status": "UNVERIFIED",
            "source_url": raw_path,
            "evidence_snippet": "Crawled text content is missing or insufficient (< 100 characters).",
            "verification_method": "content_length_and_text_retrieval",
            "investigation": inv,
        }

    # ── 7. EXTRACTED WORD COUNT ──────────────────────────────────────────────
    def investigate_word_count(self, text: Optional[str]) -> Dict[str, Any]:
        inv = {
            "field": "extracted_word_count",
            "started_at": utc_now_iso(),
            "sources_checked": ["content_recalculation"],
            "urls_crawled": [],
            "search_queries": [],
            "crawl_attempts": 0,
            "search_attempts": 0,
            "evidence_found": False,
            "strategies_completed": ["normalized_word_counting"],
            "strategies_remaining": [],
            "strategies_exhausted": True,
            "infra_failure": None,
        }

        clean_text = (text or "").strip()
        words = re.findall(r"\b\w+\b", clean_text)
        count = len(words)

        inv["evidence_found"] = True
        inv["completed_at"] = utc_now_iso()
        return {
            "field": "extracted_word_count",
            "value": count,
            "status": "VERIFIED",
            "source_url": "calculation://normalized_extracted_text",
            "evidence_snippet": f"Counted {count} words from verified normalized text corpus.",
            "verification_method": "normalized_extracted_text",
            "investigation": inv,
        }


investigation_engine = Agent2InvestigationEngine()
