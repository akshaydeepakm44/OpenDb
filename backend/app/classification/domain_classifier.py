import re
from typing import Dict, Any, Tuple, Optional

# Mapping Schema.org @type values to standardized industry sectors
SCHEMA_ORG_TYPE_MAP = {
    "supermarket": "Retail, Supermarkets & E-Commerce",
    "grocerystore": "Retail, Supermarkets & E-Commerce",
    "store": "Retail, Supermarkets & E-Commerce",
    "shoppingcenter": "Retail, Supermarkets & E-Commerce",
    "departmentstore": "Retail, Supermarkets & E-Commerce",
    "wholesalestore": "Retail, Supermarkets & E-Commerce",
    "clothingstore": "Retail, Supermarkets & E-Commerce",
    "outletstore": "Retail, Supermarkets & E-Commerce",
    "hardwarestore": "Retail, Supermarkets & E-Commerce",
    "restaurant": "Food, Beverage & Hospitality",
    "foodestablishment": "Food, Beverage & Hospitality",
    "hotel": "Food, Beverage & Hospitality",
    "lodgingbusiness": "Food, Beverage & Hospitality",
    "bakery": "Food, Beverage & Hospitality",
    "barorpub": "Food, Beverage & Hospitality",
    "cafeorcoffeeshop": "Food, Beverage & Hospitality",
    "winery": "Food, Beverage & Hospitality",
    "brewery": "Food, Beverage & Hospitality",
    "bankorcreditunion": "Financial Services & Banking",
    "financialservice": "Financial Services & Banking",
    "insuranceagency": "Financial Services & Banking",
    "accountingbusiness": "Financial Services & Banking",
    "hospital": "Healthcare, Medical & Life Sciences",
    "medicalorganization": "Healthcare, Medical & Life Sciences",
    "medicalbusiness": "Healthcare, Medical & Life Sciences",
    "pharmacy": "Healthcare, Medical & Life Sciences",
    "physician": "Healthcare, Medical & Life Sciences",
    "dentist": "Healthcare, Medical & Life Sciences",
    "diagnosticlab": "Healthcare, Medical & Life Sciences",
    "softwareapplication": "Software, SaaS & Cloud Computing",
    "techarticle": "Software, SaaS & Cloud Computing",
    "computerservice": "Software, SaaS & Cloud Computing",
    "educationalorganization": "Education & EdTech",
    "school": "Education & EdTech",
    "collegeoruniversity": "Education & EdTech",
    "preschool": "Education & EdTech",
    "legalservice": "Professional, Legal & Business Services",
    "attorney": "Professional, Legal & Business Services",
    "notary": "Professional, Legal & Business Services",
    "employmentagency": "Professional, Legal & Business Services",
    "realestateagent": "Real Estate, Architecture & Construction",
    "generalcontractor": "Real Estate, Architecture & Construction",
    "homeandconstructionbusiness": "Real Estate, Architecture & Construction",
    "autodealer": "Manufacturing, Industrial & Hardware",
    "autorepair": "Manufacturing, Industrial & Hardware",
    "automotivebusiness": "Manufacturing, Industrial & Hardware",
}

class DomainClassifier:
    def __init__(self):
        self.domain_keywords = {
            "Retail, Supermarkets & E-Commerce": [
                "supermarket", "supermarkets", "hypermarket", "grocery", "groceries",
                "retail", "retailer", "store", "stores", "shop", "shopping", "ecommerce",
                "e-commerce", "cart", "checkout", "merchandise", "apparel", "wholesale",
                "consumer goods", "outlet", "mall", "fresh produce", "bakery", "butchery",
                "promotions", "loyalty program", "club card", "discount store", "catalogue"
            ],
            "Food, Beverage & Hospitality": [
                "restaurant", "dining", "hotel", "resort", "hospitality", "food",
                "beverage", "cafe", "coffee", "bar", "catering", "cuisine", "pub",
                "brewery", "winery", "lodging", "accommodations", "gourmet", "chef", "menu"
            ],
            "Financial Services & Banking": [
                "bank", "banking", "finance", "financial", "fintech", "wealth",
                "investment", "asset management", "insurance", "loan", "lending",
                "credit", "mortgage", "payment", "payments", "crypto", "trading",
                "capital", "equity", "fund", "venture capital", "securities"
            ],
            "Healthcare, Medical & Life Sciences": [
                "hospital", "clinic", "clinical", "doctor", "medical", "patient",
                "health", "healthcare", "treatment", "care", "medicine", "pharmacy",
                "pharmaceutical", "pharma", "biotech", "surgery", "diagnostic", "therapy",
                "dental", "laboratory", "biotechnology", "life sciences", "physician"
            ],
            "Software, SaaS & Cloud Computing": [
                "software", "app", "api", "cloud", "saas", "platform", "developer",
                "code", "database", "cybersecurity", "tech", "artificial intelligence",
                "ai", "machine learning", "devops", "sdk", "backend", "frontend",
                "infrastructure", "open source", "microservices", "kubernetes", "hosting"
            ],
            "Manufacturing, Industrial & Hardware": [
                "manufacturing", "manufacturer", "factory", "industrial", "machinery",
                "automotive", "aerospace", "hardware", "fabrication", "assembly",
                "electronics", "equipment", "plant", "metals", "plastics", "robotics",
                "oem", "engineering", "tooling", "automation"
            ],
            "Logistics, Transport & Supply Chain": [
                "logistics", "freight", "shipping", "transport", "transportation",
                "cargo", "warehousing", "supply chain", "delivery", "fleet",
                "courier", "distribution", "trucking", "maritime", "aviation", "fulfillment"
            ],
            "Real Estate, Architecture & Construction": [
                "real estate", "realty", "property", "properties", "construction",
                "architecture", "architect", "building", "contractor", "builder",
                "residential", "commercial real estate", "leasing", "rental",
                "developer", "mortgage", "land", "apartments", "civil engineering"
            ],
            "Education & EdTech": [
                "university", "college", "school", "course", "courses", "degree",
                "education", "student", "students", "academic", "faculty", "admission",
                "tuition", "campus", "edtech", "academy", "training", "learning", "curriculum"
            ],
            "Professional, Legal & Business Services": [
                "consulting", "consultant", "advisory", "legal", "law firm",
                "attorney", "lawyer", "accounting", "audit", "tax", "recruitment",
                "staffing", "human resources", "compliance", "management consulting",
                "b2b services", "litigation"
            ],
            "Media, Entertainment & Telecommunications": [
                "media", "entertainment", "telecom", "telecommunications", "publishing",
                "broadcasting", "news", "streaming", "music", "film", "gaming", "game",
                "advertising", "marketing", "pr", "creative agency", "production studio"
            ],
            "Energy, Utilities & Cleantech": [
                "energy", "solar", "wind", "renewable", "cleantech", "oil",
                "gas", "power", "utility", "electricity", "battery", "grid",
                "petroleum", "clean energy", "environmental", "sustainability", "nuclear"
            ]
        }

    def classify(
        self,
        text_content: str,
        title: str = "",
        url: str = "",
        user_domain: str | None = None,
        schema_org_type: str | None = None
    ) -> Tuple[str, str, float]:
        """
        Classify page content into a precise Industry Sector, Subdomain, with confidence score.
        Multi-source resolution: Schema.org @type -> explicit user override -> keyword token scoring -> TLD fallback.
        """
        if user_domain and user_domain.strip():
            norm_user = user_domain.strip().capitalize()
            return norm_user, "General", 1.0

        # 1. High-confidence Schema.org @type match
        if schema_org_type:
            clean_type = re.sub(r"[^a-zA-Z]", "", schema_org_type.lower())
            if clean_type in SCHEMA_ORG_TYPE_MAP:
                return SCHEMA_ORG_TYPE_MAP[clean_type], "Schema.org Verified", 0.95

        comb_text = f"{url} {title} {text_content}".lower()

        # 2. Keyword token scoring across 12 sectors
        scores: Dict[str, int] = {dom: 0 for dom in self.domain_keywords}

        url_tokens = set(re.findall(r"[a-zA-Z0-9]+", url.lower()))
        title_tokens = set(re.findall(r"[a-zA-Z0-9]+", title.lower()))

        for dom, keywords in self.domain_keywords.items():
            for kw in keywords:
                matches = len(re.findall(r"\b" + re.escape(kw) + r"\b", comb_text))
                # Boost if keyword appears in URL or title as distinct token or multi-word phrase
                if " " in kw:
                    if kw in url.lower():
                        matches += 5
                    if kw in title.lower():
                        matches += 3
                else:
                    if kw in url_tokens:
                        matches += 5
                    if kw in title_tokens:
                        matches += 3
                scores[dom] += matches

        best_domain = max(scores, key=scores.get)
        max_score = scores[best_domain]

        if max_score > 0:
            total_score = sum(scores.values())
            confidence = min(0.98, max(0.60, round(max_score / max(1, total_score), 2)))
            return best_domain, "Corporate", confidence

        # 3. Domain extension / URL fallback heuristics
        url_lower = url.lower()
        if any(ext in url_lower for ext in [".store", ".shop", "supermarket", "mart", "grocery", "mall"]):
            return "Retail, Supermarkets & E-Commerce", "E-Commerce", 0.70
        if any(ext in url_lower for ext in [".ai", ".io", ".dev", ".tech", ".app", "software"]):
            return "Software, SaaS & Cloud Computing", "Tech Infrastructure", 0.70
        if any(ext in url_lower for ext in [".edu", ".ac."]):
            return "Education & EdTech", "Higher Education", 0.85
        if any(ext in url_lower for ext in [".bank", ".fin"]):
            return "Financial Services & Banking", "Banking", 0.80

        # General Commercial Fallback
        return "Professional, Legal & Business Services", "Commercial Enterprise", 0.50

domain_classifier = DomainClassifier()
