import re
from typing import Optional, Any

# Blacklisted placeholder substrings
PLACEHOLDER_SUBSTRINGS = [
    "n/a", "na", "tbd", "unknown", "none", "null", "undefined",
    "contact us", "contact sales", "call us", "call for pricing",
    "request a quote", "click here", "read more", "learn more",
    "placeholder", "lorem ipsum", "coming soon", "under construction"
]

# Fields explicitly EXCLUDED from placeholder cleaning (Controlled Vocabularies)
CONTROLLED_VOCABULARY_FIELDS = {
    "role_tag", "role_tags", "source_type", "confidence", "status",
    "conflict", "is_stale", "http_status", "crawler_version"
}

class PlaceholderGuard:
    """
    Filters fake, generic, or uninformative placeholder strings from extracted text,
    converting them to null while strictly preserving controlled-vocabulary enums.
    """

    @staticmethod
    def is_placeholder(val: Any, field_name: str = "") -> bool:
        if val is None:
            return True
            
        # Do NOT apply placeholder filtering to controlled vocabulary / enum fields
        if field_name and field_name.lower() in CONTROLLED_VOCABULARY_FIELDS:
            return False

        if not isinstance(val, str):
            return False

        clean = val.strip().lower()
        if not clean:
            return True

        if clean in PLACEHOLDER_SUBSTRINGS:
            return True

        # Check for phrase placeholders containing call-to-actions or filler
        phrase_placeholders = [
            "contact us", "contact sales", "call us", "call for pricing",
            "request a quote", "click here", "read more", "learn more",
            "lorem ipsum", "coming soon", "under construction", "pricing details"
        ]
        if any(ph in clean for ph in phrase_placeholders):
            return True

        # Check exact pattern matches for bracketed size estimates not from text
        if re.match(r"^\s*\(?\s*(?:estimated|approx|tbd|n/a)\s*\)?\s*$", clean):
            return True

        return False

    @staticmethod
    def clean_value(val: Any, field_name: str = "") -> Optional[str]:
        if PlaceholderGuard.is_placeholder(val, field_name):
            return None
        return str(val).strip()

placeholder_guard = PlaceholderGuard()
