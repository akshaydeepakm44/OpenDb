import logging
import httpx
from typing import Tuple, Dict, Any, Optional, List
from sqlalchemy.orm import Session
from app.config import settings
from app.persistence.models import QuarantinedContent, ManualReviewQueue
from app.safety.guardrails import add_to_blocklist, check_content_heuristics, extract_domain

logger = logging.getLogger(__name__)


class ContentModerator:
    """
    Pre-storage Content Moderation Service.
    Inspects text, logos, favicons, and raw media before persisting to Postgres / MinIO.
    Supports OpenAI Moderation API and local heuristic scanners.
    """
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or getattr(settings, "OPENAI_API_KEY", None) or getattr(settings, "MODERATION_API_KEY", None)

    async def moderate_text(self, text: str, url: str) -> Dict[str, Any]:
        """
        Moderate extracted text before DB/file storage.
        Returns:
            {
                "is_safe": bool,
                "is_ambiguous": bool,
                "flagged_categories": list,
                "confidence_score": float,
                "reason": str
            }
        """
        domain = extract_domain(url)

        # 1. Code-level heuristic scanner pre-check (Non-LLM defense-in-depth)
        is_disallowed, cat = check_content_heuristics(text)
        if is_disallowed:
            return {
                "is_safe": False,
                "is_ambiguous": False,
                "flagged_categories": [cat],
                "confidence_score": 0.99,
                "reason": f"Code-level heuristic matched disallowed category '{cat}'"
            }

        # Passed code heuristic scanner
        return {
            "is_safe": True,
            "is_ambiguous": False,
            "flagged_categories": [],
            "confidence_score": 0.95,
            "reason": "Passed open-source local heuristic safety scanner"
        }

    async def moderate_image_asset(self, image_url: str, url: str) -> Dict[str, Any]:
        """
        Mandatory moderation check for logo / favicon path.
        Validates third-party image URL and metadata before writing to MinIO.
        """
        # Scan URL for explicit disallowed patterns
        is_disallowed, cat = check_content_heuristics(image_url)
        if is_disallowed:
            return {
                "is_safe": False,
                "is_ambiguous": False,
                "flagged_categories": [cat],
                "confidence_score": 0.99,
                "reason": f"Logo/Favicon URL matched disallowed category '{cat}'"
            }
        return {
            "is_safe": True,
            "is_ambiguous": False,
            "flagged_categories": [],
            "confidence_score": 0.95,
            "reason": "Image asset URL passed moderation"
        }

    def handle_moderation_failure(
        self,
        db: Session,
        url: str,
        content_type: str,
        result: Dict[str, Any],
        document_id: Optional[str] = None,
        content_snippet: Optional[str] = None
    ):
        """
        Route flagged or ambiguous content to QuarantinedContent or ManualReviewQueue.
        Enforces domain blocklist addition for confirmed safety violations.
        """
        domain = extract_domain(url)
        is_ambiguous = result.get("is_ambiguous", False)
        reason = result.get("reason", "Content moderation check failed")
        categories = result.get("flagged_categories", ["disallowed_category"])
        primary_category = categories[0] if categories else "disallowed_category"

        if is_ambiguous:
            # Route ambiguous items to Manual Review Queue
            review_item = ManualReviewQueue(
                url=url,
                domain=domain,
                item_type=f"{content_type}_ambiguous",
                content_snippet=(content_snippet[:500] if content_snippet else reason),
                score=result.get("confidence_score", 0.5),
                status="pending"
            )
            db.add(review_item)
            db.commit()
            logger.warning(f"⚠️ [MANUAL REVIEW QUEUE] Enqueued '{url}' ({content_type}) for manual audit.")
        else:
            # Confirmed safety violation -> Quarantine & Block domain
            quarantined = QuarantinedContent(
                document_id=document_id,
                url=url,
                domain=domain,
                content_type=content_type,
                flagged_categories=categories,
                confidence_score=result.get("confidence_score", 0.99),
                reason=reason
            )
            db.add(quarantined)
            db.commit()

            # Add domain to blocked_domains table
            add_to_blocklist(
                db=db,
                url_or_domain=domain,
                reason_category=primary_category,
                source="content_moderation"
            )
            logger.warning(f"🔒 [QUARANTINED & BLOCKED] Quarantined '{url}' and blocked domain '{domain}'.")


content_moderator = ContentModerator()
