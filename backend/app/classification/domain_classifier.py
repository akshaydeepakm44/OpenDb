import re
from typing import Dict, Any, Tuple

class DomainClassifier:
    def __init__(self):
        self.domain_keywords = {
            "Technology": [
                "software", "app", "api", "cloud", "ai", "platform", "saas",
                "developer", "code", "database", "cybersecurity", "tech", "hardware"
            ],
            "Healthcare": [
                "hospital", "clinic", "doctor", "medical", "patient", "health",
                "treatment", "care", "medicine", "pharma", "clinical", "surgery"
            ],
            "Education": [
                "university", "college", "school", "course", "degree", "education",
                "student", "academic", "faculty", "admission", "tuition", "campus"
            ],
            "Business": [
                "company", "services", "solutions", "corporate", "enterprise",
                "market", "consulting", "finance", "business", "industry", "b2b"
            ]
        }

    def classify(
        self,
        text_content: str,
        title: str = "",
        url: str = "",
        user_domain: str | None = None
    ) -> Tuple[str, str, float]:
        """Classify page content into a domain, subdomain, with confidence score."""
        if user_domain and user_domain.strip():
            norm_user = user_domain.strip().capitalize()
            return norm_user, "General", 1.0

        comb_text = f"{url} {title} {text_content}".lower()

        scores: Dict[str, int] = {dom: 0 for dom in self.domain_keywords}

        for dom, keywords in self.domain_keywords.items():
            for kw in keywords:
                matches = len(re.findall(r"\b" + re.escape(kw) + r"\b", comb_text))
                scores[dom] += matches

        best_domain = max(scores, key=scores.get)
        max_score = scores[best_domain]

        if max_score == 0:
            return "Technology", "General", 0.50

        total_score = sum(scores.values())
        confidence = min(0.99, max(0.50, round(max_score / max(1, total_score), 2)))

        return best_domain, "General", confidence

domain_classifier = DomainClassifier()
