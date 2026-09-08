"""
Autonomous Feedback Engine — §12 of Master Rules

Ingests batch evaluation metrics (yield rate, duplicate rate, invalid site rate, geographic coverage)
and computes updated search strategy weights for the Haystack agent.
"""
import logging
from typing import Dict, Any, List
from app.persistence.database import SessionLocal
from app.persistence.models import UniversalRecord, SearchHistory, CrawlActivityLog, BatchResult, utc_now

logger = logging.getLogger(__name__)

class FeedbackEngine:
    def compute_batch_feedback(self, batch_size: int = 100) -> Dict[str, Any]:
        """
        Analyse recent batch performance and generate strategy adjustments.
        """
        db = SessionLocal()
        try:
            recent_records = (
                db.query(UniversalRecord)
                .order_by(UniversalRecord.created_at.desc())
                .limit(batch_size)
                .all()
            )
            if not recent_records:
                return {"status": "NO_DATA", "recommendation": "Maintain current baseline search strategy"}

            total = len(recent_records)
            verified_count = sum(1 for r in recent_records if r.status == "VERIFIED")
            duplicate_count = sum(1 for r in recent_records if r.status in ("DUPLICATE", "FILTERED"))
            rejected_count = sum(1 for r in recent_records if r.status in ("REJECTED", "INVALID", "FAILED"))

            verified_yield = verified_count / total
            duplicate_rate = duplicate_count / total
            rejection_rate = rejected_count / total

            # Formulate strategic feedback directive
            directive = "MAINTAIN"
            strategy_adjustment = ""

            if duplicate_rate > 0.30:
                directive = "MODIFY_QUERY_PATTERNS"
                strategy_adjustment = "High duplicate rate detected. Injecting geographic qualifiers and specific B2B sub-industry terms."
            elif rejection_rate > 0.25:
                directive = "STRICTER_PRE_FILTER"
                strategy_adjustment = "High invalid rate detected. Enforcing strict root domain candidate filtering."
            elif verified_yield > 0.60:
                directive = "EXPAND_TAXONOMY"
                strategy_adjustment = "High verified yield achieved. Expanding query taxonomy to adjacent subdomains."

            logger.info(
                f"📊 [Feedback Engine] Batch Evaluation ({total} records) | "
                f"Yield: {verified_yield:.2%} | Dup Rate: {duplicate_rate:.2%} | "
                f"Directive: {directive}"
            )

            return {
                "batch_size": total,
                "verified_yield": round(verified_yield, 4),
                "duplicate_rate": round(duplicate_rate, 4),
                "rejection_rate": round(rejection_rate, 4),
                "directive": directive,
                "strategy_adjustment": strategy_adjustment,
            }

        except Exception as err:
            logger.error(f"[Feedback Engine] Exception computing feedback: {err}")
            return {"status": "ERROR", "error": str(err)}
        finally:
            db.close()


feedback_engine = FeedbackEngine()
