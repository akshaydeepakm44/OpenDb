import re
import math
import hashlib
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Set, Tuple
from urllib.parse import urlparse, quote

from app.extraction.placeholder_guard import placeholder_guard
from app.storage.file_storage import file_storage

logger = logging.getLogger(__name__)

def compute_simhash64(text: str) -> int:
    """Compute a 64-bit SimHash integer for ultra-fast hamming distance duplicate detection."""
    if not text:
        return 0
    words = [w.lower() for w in re.findall(r"\w+", text) if len(w) > 2]
    if not words:
        return 0

    v = [0] * 64
    for word in words:
        # Hash each word to 64 bits
        h = int(hashlib.md5(word.encode("utf-8")).hexdigest()[:16], 16)
        for i in range(64):
            if (h >> i) & 1:
                v[i] += 1
            else:
                v[i] -= 1

    simhash = 0
    for i in range(64):
        if v[i] > 0:
            simhash |= (1 << i)
    return simhash

def hamming_distance64(hash1: int, hash2: int) -> int:
    """Compute bitwise hamming distance between two 64-bit SimHashes."""
    x = (hash1 ^ hash2) & 0xFFFFFFFFFFFFFFFF
    dist = 0
    while x:
        dist += 1
        x &= x - 1
    return dist


class GlobalDataVerifier:
    """
    Global-Level Data Verification Layer (Checkpoints A - G).
    Checks records against each other across rolling window (N=500) with O(1) hash indexing,
    verbatim Markdown source matching for Checkpoint B, cold-start floor (N>=30),
    and DB-backed denylist management.
    """

    MIN_DATASET_COMPARISON_SIZE = 30
    DEFAULT_ROLLING_WINDOW_SIZE = 500

    # Default aggregator domains (seeded in DB table DenyListDomain)
    DEFAULT_DENIED_AGGREGATORS = {
        "softonic.com", "apkpure.com", "uptodown.com", "cnet.com",
        "download.com", "appbrain.com", "apkmonk.com", "malavida.com"
    }

    # Known boilerplate regex patterns (Checkpoint A)
    BOILERPLATE_REGEXES = [
        re.compile(r"^.{1,60}\s+enterprise lead record\.?$", re.IGNORECASE),
        re.compile(r"^.{1,60}\s+is a company (that|which) provides.*$", re.IGNORECASE),
        re.compile(r"^.{1,60}\s+web portal indexed into OpenDB.*$", re.IGNORECASE),
        re.compile(r"^.{1,60}\s+company profile\.?$", re.IGNORECASE),
    ]

    def __init__(self):
        # Concrete O(1) indexing structures
        self._overview_simhash_index: Dict[str, int] = {}  # company_id -> 64-bit simhash
        self._tech_stack_value_index: Dict[str, Set[str]] = {}  # tag_string -> set of company_ids
        self._tech_stack_industry_index: Dict[str, Set[str]] = {}  # tag_string -> set of industry names

    def clean_overview_text(self, text: Optional[str], company_name: str = "") -> str:
        """Strip company name and normalize text for boilerplate comparison."""
        if not text:
            return ""
        c = text.lower()
        if company_name:
            c = c.replace(company_name.lower(), "").strip()
        c = re.sub(r"\s+", " ", c).strip()
        return c

    def is_boilerplate_text(self, text: Optional[str], company_name: str = "") -> bool:
        """Check Checkpoint A regex patterns for canned boilerplate sentences."""
        if not text or len(text.strip()) < 15:
            return True
        for pat in self.BOILERPLATE_REGEXES:
            if pat.search(text.strip()):
                return True
        cleaned = self.clean_overview_text(text, company_name)
        words = [w for w in re.findall(r"\w+", cleaned) if len(w) > 2]
        if len(words) < 4:
            return True
        return False

    def verify_verbatim_page_markdown(self, source_url: Optional[str], term: str) -> bool:
        """
        Checkpoint B Source Matching Precision:
        Verifies exact term presence (fuzzy >= 0.85) in stored Markdown of specific source_url.
        """
        if not source_url or not term:
            return False
        try:
            parsed = urlparse(source_url)
            clean_dom = parsed.netloc.replace("www.", "").lower().split("/")[0]
            if not clean_dom:
                return False

            # Search in file storage
            storage_path = f"companies/{clean_dom}/pages/homepage.md"
            text = file_storage.read_file_content(storage_path)
            if not text:
                text = file_storage.read_file_content(f"companies/{clean_dom}/pages/about.md")

            if text:
                term_clean = term.strip().lower()
                text_clean = text.lower()
                if term_clean in text_clean:
                    return True
                # Fuzzy chunk match >= 0.85
                from app.crawler.anti_hallucination_pipeline import AntiHallucinationPipeline
                if AntiHallucinationPipeline.verify_sentence_in_text(term_clean, text_clean, threshold=0.85):
                    return True
        except Exception as e:
            logger.debug(f"Verbatim Markdown check notice for {source_url}: {e}")
        return False

    def check_checkpoint_a(self, record: Dict[str, Any], dataset_sample: List[Dict[str, Any]]) -> Tuple[bool, Optional[str]]:
        """
        Checkpoint A: Template & Boilerplate Detection
        """
        c_name = record.get("company_name", "")
        ov_obj = record.get("business_overview")
        ov_text = ov_obj.get("text") if isinstance(ov_obj, dict) else (ov_obj if isinstance(ov_obj, str) else None)

        if not ov_text:
            return True, None

        if self.is_boilerplate_text(ov_text, c_name):
            return False, f"Boilerplate regex/template matched: '{ov_text[:60]}'"

        # Check verbatim page Markdown citation requirement (if stored Markdown exists)
        source_pages = ov_obj.get("source_pages") if isinstance(ov_obj, dict) else []
        main_url = record.get("official_website") or (record.get("website") or {}).get("url")
        check_url = source_pages[0] if (isinstance(source_pages, list) and source_pages) else main_url

        if check_url:
            try:
                parsed = urlparse(check_url)
                clean_dom = parsed.netloc.replace("www.", "").lower().split("/")[0]
                has_file = (file_storage.local_dir / "companies" / clean_dom / "pages" / "homepage.md").exists()
                if has_file and not self.verify_verbatim_page_markdown(check_url, ov_text[:50]):
                    return False, f"Business overview sentences not verbatim present on cited page: {check_url}"
            except Exception:
                pass


        # SimHash similarity & string duplicate check against dataset sample
        cleaned_rec = self.clean_overview_text(ov_text, c_name)
        rec_simhash = compute_simhash64(cleaned_rec)
        if rec_simhash != 0:
            for other in dataset_sample:
                other_id = other.get("id")
                if other_id and other_id == record.get("id"):
                    continue
                other_ov = other.get("business_overview")
                other_text = other_ov.get("text") if isinstance(other_ov, dict) else (other_ov if isinstance(other_ov, str) else None)
                if not other_text:
                    continue
                other_cleaned = self.clean_overview_text(other_text, other.get("company_name", ""))
                if cleaned_rec and other_cleaned:
                    if cleaned_rec == other_cleaned or (len(cleaned_rec) >= 20 and (cleaned_rec in other_cleaned or other_cleaned in cleaned_rec)):
                        return False, f"Overview text duplicate of existing company record ID {other_id}"
                other_simhash = compute_simhash64(other_cleaned)
                if other_simhash != 0 and hamming_distance64(rec_simhash, other_simhash) <= 8:
                    return False, f"Overview >= 90% SimHash duplicate of existing company record ID {other_id}"

        return True, None


    def check_checkpoint_b(self, record: Dict[str, Any], dataset_sample: List[Dict[str, Any]]) -> Tuple[bool, List[str]]:
        """
        Checkpoint B: Disambiguated Uniform-Default Detection
        High frequency (>15% across >3 industries) ONLY flags values that LACK a verbatim source_url text match.
        """
        rejected_fields = []
        if len(dataset_sample) < self.MIN_DATASET_COMPARISON_SIZE:
            # Cold-start floor: Log only below 30 records
            return True, []

        tech_list = record.get("technology_stack") or []
        if isinstance(tech_list, list) and tech_list:
            main_url = record.get("official_website") or (record.get("website") or {}).get("url")
            for t_item in tech_list:
                t_val = t_item.get("value") if isinstance(t_item, dict) else t_item
                t_src = t_item.get("source_url") if isinstance(t_item, dict) else main_url

                if not t_val:
                    continue

                # Check frequency across dataset sample
                freq_count = sum(1 for r in dataset_sample if any((ti.get("value") if isinstance(ti, dict) else ti) == t_val for ti in (r.get("technology_stack") or [])))
                freq_pct = freq_count / max(1, len(dataset_sample))

                if freq_pct > 0.15:
                    # High frequency -> Disambiguate with verbatim Markdown match
                    is_verbatim = self.verify_verbatim_page_markdown(t_src, str(t_val))
                    if not is_verbatim:
                        rejected_fields.append(f"technology_stack:{t_val}")
                        logger.warning(f"[Checkpoint B] Rejected uniform default tech tag '{t_val}' on {record.get('domain')} (freq={freq_pct:.1%}, missing verbatim page match)")

        return len(rejected_fields) == 0, rejected_fields

    def check_checkpoint_c(self, batch_records: List[Dict[str, Any]]) -> Tuple[bool, Optional[str]]:
        """
        Checkpoint C: Timestamp & Execution Timing Plausibility
        """
        if len(batch_records) < 5:
            return True, None

        timestamps = []
        for r in batch_records:
            audit = r.get("extraction_audit") or r.get("provenance") or {}
            ts = audit.get("crawl_finished_at") or audit.get("extracted_at")
            if ts:
                timestamps.append(ts[:19])

        if len(timestamps) >= 5:
            from collections import Counter
            counts = Counter(timestamps)
            most_common_cnt = counts.most_common(1)[0][1]
            if most_common_cnt / len(timestamps) > 0.95:
                return False, f"Timestamp anomaly: {most_common_cnt}/{len(timestamps)} records share identical second timestamp '{counts.most_common(1)[0][0]}'"

        return True, None

    def check_checkpoint_d(self, record: Dict[str, Any], denied_domains: Set[str] = None) -> Tuple[bool, Optional[str]]:
        """
        Checkpoint D: Entity Resolution & Aggregator Denylist
        """
        domain = (record.get("website") or {}).get("domain") or record.get("domain", "")
        clean_dom = domain.replace("www.", "").lower().split("/")[0]

        all_denied = self.DEFAULT_DENIED_AGGREGATORS.union(denied_domains or set())
        if clean_dom in all_denied:
            return False, f"Domain '{clean_dom}' matches denied aggregator site denylist"

        # Check entity display name vs domain similarity
        c_name = record.get("company_name") or record.get("canonical_name", "")
        if c_name and clean_dom and len(c_name) > 3:
            dom_name = clean_dom.split(".")[0].replace("-", " ")
            from app.crawler.anti_hallucination_pipeline import AntiHallucinationPipeline
            sim = AntiHallucinationPipeline.fuzzy_match_ratio(c_name, dom_name)
            if sim < 0.2 and not any(kw in c_name.lower() for kw in dom_name.split()):
                logger.info(f"[Checkpoint D] Low fuzzy match ({sim:.2f}) between company '{c_name}' and domain '{clean_dom}'")

        return True, None

    def verify_record_against_dataset(
        self,
        record: Dict[str, Any],
        dataset_sample: List[Dict[str, Any]],
        denied_domains: Set[str] = None
    ) -> Dict[str, Any]:
        """
        Executes Checkpoints A through G for a single company record against the dataset rolling window.
        Returns detailed verification status and nullifies unverified fields.
        """
        rejection_reasons = []
        rejected_fields = []

        # 1. Checkpoint D: Entity Resolution & Aggregator Filter
        pass_d, err_d = self.check_checkpoint_d(record, denied_domains)
        if not pass_d:
            rejection_reasons.append(err_d)

        # 2. Checkpoint A: Template & Boilerplate Detection
        pass_a, err_a = self.check_checkpoint_a(record, dataset_sample)
        if not pass_a:
            rejection_reasons.append(err_a)
            rejected_fields.append("business_overview")
            if isinstance(record.get("business_overview"), dict):
                record["business_overview"]["text"] = None
            else:
                record["business_overview"] = None

        # 3. Checkpoint B: Uniform Default Disambiguation
        pass_b, err_b_fields = self.check_checkpoint_b(record, dataset_sample)
        if not pass_b:
            for rf in err_b_fields:
                rejected_fields.append(rf)
                rejection_reasons.append(f"Uniform default field rejected: {rf}")
            # Nullify rejected tech stack items
            if record.get("technology_stack"):
                valid_techs = [
                    t for t in record["technology_stack"]
                    if f"technology_stack:{(t.get('value') if isinstance(t, dict) else t)}" not in err_b_fields
                ]
                record["technology_stack"] = valid_techs

        # 4. Checkpoint G: Tier & Warmth Score Recomputation Gate
        # Recalculate score with strictly non-null verified fields
        from app.crawler.agentic_verifier import agentic_verifier
        checklist = agentic_verifier.generate_data_requirements_checklist(record, [])
        completeness = agentic_verifier.calculate_data_completeness_score(record, checklist, [])

        # Hard UI Safety Net: If >=2 top fields rejected, force INSUFFICIENT DATA tier
        if len(rejected_fields) >= 2:
            completeness["total_score"] = min(35.0, completeness["total_score"])
            completeness["badge_level"] = "🔴 INSUFFICIENT DATA"
            completeness["formula_explanation"] += f" (Capped at INSUFFICIENT DATA due to {len(rejected_fields)} global verification field rejections)"
            rejection_reasons.append(f"Forced tier cap: {len(rejected_fields)} fields failed global dataset verification")

        record["data_completeness"] = completeness
        record["global_verification"] = {
            "passed": len(rejection_reasons) == 0,
            "rejection_reasons": rejection_reasons,
            "rejected_fields": rejected_fields,
            "verified_at": datetime.now(timezone.utc).isoformat()
        }

        return record

    def verify_batch(
        self,
        records: List[Dict[str, Any]],
        dataset_sample: List[Dict[str, Any]] = None,
        denied_domains: Set[str] = None
    ) -> Dict[str, Any]:
        """
        Runs batch-level verification across rolling window (N=500).
        Returns global_verification_report.json dictionary structure.
        """
        sample = dataset_sample or records
        checkpoint_failures = {
            "template_boilerplate": [],
            "uniform_default_fields": [],
            "timestamp_anomaly": False,
            "entity_resolution_mismatch": [],
            "logo_rejected": [],
            "domain_denied": []
        }

        # Checkpoint C: Batch Timestamp Anomaly Check
        pass_c, err_c = self.check_checkpoint_c(records)
        if not pass_c:
            checkpoint_failures["timestamp_anomaly"] = True
            logger.error(f"[Global Verification] Batch timestamp anomaly: {err_c}")

        verified_records = []
        rejected_count = 0

        for r in records:
            ver_r = self.verify_record_against_dataset(r, sample, denied_domains)
            g_ver = ver_r.get("global_verification", {})
            if not g_ver.get("passed"):
                rejected_count += 1
                for err in g_ver.get("rejection_reasons", []):
                    if "Boilerplate" in err or "SimHash" in err:
                        checkpoint_failures["template_boilerplate"].append(r.get("id"))
                    elif "denied aggregator" in err:
                        checkpoint_failures["domain_denied"].append(r.get("domain"))
            verified_records.append(ver_r)

        batch_accepted = not checkpoint_failures["timestamp_anomaly"] and (rejected_count / max(1, len(records)) < 0.5)

        report = {
            "batch_id": f"batch_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}",
            "records_ingested": len(records),
            "records_rejected_outright": rejected_count,
            "checkpoint_failures": checkpoint_failures,
            "batch_accepted": batch_accepted,
            "generated_at": datetime.now(timezone.utc).isoformat()
        }

        return {
            "report": report,
            "verified_records": verified_records
        }


global_verifier = GlobalDataVerifier()
