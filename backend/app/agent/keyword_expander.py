"""
Keyword Expander — §5 of Master Prompt
Dynamically generates semantic search variations for each domain/subdomain,
using a rich global taxonomy + geo/intent/entity-expansion modifiers.
"""
import logging
import random
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

# ─── Global 8-Domain Taxonomy ─────────────────────────────────────────────────
GLOBAL_TAXONOMY: Dict[str, Dict[str, List[str]]] = {
    "Information Technology": {
        "SaaS & Cloud": [
            "SaaS startups B2B", "enterprise SaaS platforms", "cloud-native companies",
            "cloud computing vendors", "multi-cloud infrastructure providers",
            "PaaS platform companies", "IaaS providers", "SaaS marketplace tools",
        ],
        "Cybersecurity": [
            "cybersecurity companies", "endpoint protection vendors", "zero-trust security firms",
            "SIEM providers", "network security startups", "identity access management companies",
            "data privacy compliance tools", "penetration testing firms",
        ],
        "AI & Machine Learning": [
            "AI machine learning startups", "generative AI companies", "LLM infrastructure vendors",
            "MLOps platforms", "computer vision companies", "NLP AI tools",
            "AI model deployment solutions", "autonomous AI agent platforms",
        ],
        "Developer Tools": [
            "developer tool companies", "DevOps platforms", "CI/CD pipeline tools",
            "code review automation companies", "API management platforms",
            "low-code no-code platforms", "open source developer tooling",
        ],
        "Data & Analytics": [
            "data analytics platforms", "business intelligence companies",
            "real-time data streaming companies", "data warehouse vendors",
            "data lakehouse platforms", "ETL pipeline tools", "data governance companies",
        ],
        "IT Consulting": [
            "IT consulting firms", "digital transformation agencies",
            "enterprise IT solutions integrators", "managed IT service providers",
            "technology advisory companies",
        ],
    },
    "Healthcare & Life Sciences": {
        "Biotech & Pharma": [
            "biotech companies", "pharmaceutical tech solutions", "drug discovery AI companies",
            "genomics startups", "clinical trial technology providers",
            "precision medicine companies", "cell gene therapy organizations",
        ],
        "Digital Health": [
            "digital health startups", "telehealth platforms", "remote patient monitoring companies",
            "mental health technology companies", "wearable health tech firms",
            "patient engagement platforms", "health data interoperability solutions",
        ],
        "Medical Devices": [
            "medical device manufacturers", "surgical robotics companies",
            "medical imaging AI companies", "IoT medical devices", "diagnostics technology firms",
        ],
        "Healthcare IT": [
            "electronic health record companies", "health information management companies",
            "healthcare analytics firms", "revenue cycle management solutions",
            "medical coding automation companies",
        ],
    },
    "Education & EdTech": {
        "K-12 & Higher Education": [
            "EdTech platforms K-12", "higher education technology companies",
            "learning management system companies", "online university platforms",
            "education analytics companies",
        ],
        "Corporate Learning": [
            "corporate training providers", "employee upskilling platforms",
            "professional development companies", "microlearning solutions",
            "compliance training technology", "workforce learning platforms",
        ],
        "Language & Tutoring": [
            "language learning apps", "online tutoring marketplaces",
            "AI tutoring companies", "STEM education platforms", "coding bootcamps",
        ],
    },
    "Financial Services & FinTech": {
        "FinTech Startups": [
            "FinTech startups", "neobank companies", "digital banking platforms",
            "challenger banks", "embedded finance companies",
            "open banking API providers", "banking-as-a-service platforms",
        ],
        "Payments": [
            "B2B payment platforms", "payment processing companies",
            "cross-border payment solutions", "real-time payment infrastructure",
            "crypto payment companies", "payroll technology companies",
        ],
        "Investment & Wealth": [
            "investment management firms", "robo-advisor platforms",
            "wealth management technology companies", "algorithmic trading firms",
            "quantitative hedge funds", "DeFi protocol companies",
        ],
        "Insurance Tech": [
            "insurtech companies", "insurance analytics firms",
            "parametric insurance startups", "insurance claims automation companies",
            "embedded insurance providers",
        ],
        "Accounting & Compliance": [
            "accounting software vendors", "tax technology companies",
            "regulatory compliance solutions", "audit automation companies",
            "financial reporting platforms",
        ],
    },
    "E-Commerce & Retail": {
        "D2C & Brands": [
            "direct-to-consumer brands", "e-commerce technology companies",
            "headless commerce platforms", "digital-first consumer brands",
            "subscription box companies",
        ],
        "Marketplace & Platforms": [
            "e-commerce marketplace platforms", "B2B wholesale marketplaces",
            "product discovery platforms", "social commerce companies",
            "live shopping technology firms",
        ],
        "Logistics & Supply Chain": [
            "supply chain technology companies", "last-mile delivery startups",
            "logistics automation companies", "inventory management software",
            "3PL technology providers", "cold chain logistics companies",
        ],
        "Retail Tech": [
            "retail technology companies", "point of sale systems",
            "customer loyalty platforms", "retail analytics companies",
            "store automation firms", "smart checkout technology",
        ],
    },
    "Manufacturing & Industrial": {
        "Industry 4.0": [
            "Industry 4.0 companies", "smart factory technology firms",
            "IIoT industrial IoT companies", "digital twin companies",
            "predictive maintenance platforms", "manufacturing analytics firms",
        ],
        "Automation & Robotics": [
            "industrial automation companies", "robotics manufacturers",
            "collaborative robot companies", "autonomous mobile robot companies",
            "robotic process automation vendors", "warehouse automation companies",
        ],
        "Clean Energy": [
            "clean energy technology companies", "solar energy companies",
            "wind energy technology firms", "energy storage companies",
            "smart grid technology providers", "EV charging infrastructure companies",
        ],
    },
    "Real Estate & PropTech": {
        "PropTech": [
            "proptech companies", "real estate technology startups",
            "property management software companies", "real estate analytics platforms",
            "construction technology companies", "smart building technology firms",
        ],
        "Commercial Real Estate": [
            "commercial real estate technology", "CRE data analytics companies",
            "lease management platforms", "facility management software companies",
        ],
    },
    "Media, Marketing & AdTech": {
        "AdTech & MarTech": [
            "adtech companies", "marketing technology platforms",
            "programmatic advertising companies", "customer data platform companies",
            "marketing automation vendors", "account-based marketing tools",
        ],
        "Content & Media": [
            "digital media companies", "content marketing platforms",
            "video streaming technology companies", "podcast technology companies",
            "creator economy platforms", "influencer marketing technology",
        ],
        "PR & Communications": [
            "public relations technology companies", "media monitoring platforms",
            "brand intelligence companies", "digital PR agencies",
        ],
    },
}

# ─── Geographic Modifiers ──────────────────────────────────────────────────────
GEO_MODIFIERS = [
    "", "", "",  # Empty = global search (3x weight)
    "United States", "Europe", "United Kingdom", "Germany", "India",
    "Southeast Asia", "Singapore", "Australia", "Canada", "Israel",
    "Brazil", "France", "Netherlands", "Nordic countries", "Japan",
    "South Korea", "Middle East", "Africa", "Latin America",
]

# ─── Intent Modifiers ─────────────────────────────────────────────────────────
INTENT_MODIFIERS = [
    "companies list", "top companies", "leading firms", "directory",
    "startups", "vendors", "providers", "solutions", "platforms",
    "organizations site:linkedin.com/company",
    "site:crunchbase.com",
    "site:tracxn.com",
    "site:g2.com categories",
    "site:ycombinator.com",
    "emerging companies", "notable companies 2024", "notable companies 2025",
    "funded startups", "best companies", "enterprise solutions",
    "B2B companies", "global companies",
]

# ─── Source Discovery Queries (listing pages) ─────────────────────────────────
LISTING_SOURCE_TEMPLATES = [
    "list of {domain_kw} companies",
    "top {domain_kw} startups directory",
    "best {domain_kw} vendors comparison",
    "{domain_kw} companies site:clutch.co",
    "{domain_kw} companies site:g2.com",
    "{domain_kw} companies site:capterra.com",
    "{domain_kw} companies site:crunchbase.com",
    "{domain_kw} companies site:angellist.com",
    "category:{domain_kw} site:producthunt.com",
    "{domain_kw} companies database",
    "{domain_kw} industry leaders {geo}",
    "{domain_kw} market map {geo}",
]


class KeywordExpander:
    """
    Generates diverse global discovery queries.
    - Round-robin through domains/subdomains
    - Mixes base keywords, geo modifiers, intent modifiers
    - Expands from discovered entity names to find related companies
    """

    def __init__(self):
        self._domain_list = list(GLOBAL_TAXONOMY.keys())
        self._subdomain_ptr: Dict[str, int] = {}  # tracks rotation per domain
        self._keyword_ptr: Dict[str, int] = {}    # tracks rotation per subdomain

    def get_next_query(
        self,
        domain: str,
        subdomain: Optional[str] = None,
        skip_geos: Optional[List[str]] = None,
    ) -> Dict[str, str]:
        """
        Return the next search query for a given domain.
        Returns: { "query": "...", "domain": "...", "subdomain": "...", "keyword": "..." }
        """
        domain_data = GLOBAL_TAXONOMY.get(domain, {})
        if not domain_data:
            domain = self._domain_list[0]
            domain_data = GLOBAL_TAXONOMY[domain]

        # Select subdomain (round-robin)
        subdomain_keys = list(domain_data.keys())
        ptr = self._subdomain_ptr.get(domain, 0)
        selected_subdomain = subdomain or subdomain_keys[ptr % len(subdomain_keys)]
        self._subdomain_ptr[domain] = ptr + 1

        keywords = domain_data.get(selected_subdomain, [])
        if not keywords:
            keywords = [f"{selected_subdomain} companies"]

        # Select keyword (round-robin)
        kw_ptr = self._keyword_ptr.get(f"{domain}:{selected_subdomain}", 0)
        base_keyword = keywords[kw_ptr % len(keywords)]
        self._keyword_ptr[f"{domain}:{selected_subdomain}"] = kw_ptr + 1

        # Randomly pick a geo modifier (weighted towards global)
        available_geos = [g for g in GEO_MODIFIERS if g not in (skip_geos or [])]
        geo = random.choice(available_geos)

        # Randomly pick an intent modifier
        intent = random.choice(INTENT_MODIFIERS)

        # Build query
        if geo:
            query = f"{base_keyword} {intent} {geo}".strip()
        else:
            query = f"{base_keyword} {intent}".strip()

        return {
            "query": query,
            "domain": domain,
            "subdomain": selected_subdomain,
            "keyword": base_keyword,
            "geo": geo,
            "intent": intent,
        }

    def get_listing_discovery_queries(
        self, domain: str, subdomain: str, keyword: str, count: int = 3
    ) -> List[str]:
        """Generate listing-page-focused queries to find directory/catalog sources."""
        geo = random.choice(GEO_MODIFIERS)
        queries = []
        templates = random.sample(LISTING_SOURCE_TEMPLATES, min(count, len(LISTING_SOURCE_TEMPLATES)))
        for tmpl in templates:
            queries.append(tmpl.format(domain_kw=keyword, geo=geo).strip())
        return queries

    def expand_from_entity(self, entity_name: str, domain: str) -> List[str]:
        """
        Given a discovered entity name, generate search queries to find similar/competing companies.
        §5 — semantic expansion from extracted results.
        """
        expansions = [
            f"{entity_name} competitors",
            f"companies like {entity_name}",
            f"{entity_name} alternatives",
            f"{domain} companies similar to {entity_name}",
            f"{entity_name} industry peers",
        ]
        return expansions

    def all_domains(self) -> List[str]:
        return self._domain_list

    def all_subdomains(self, domain: str) -> List[str]:
        return list(GLOBAL_TAXONOMY.get(domain, {}).keys())


keyword_expander = KeywordExpander()
