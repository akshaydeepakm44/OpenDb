"""
Keyword Expander — §5 of Master Prompt & Phase 1 Architecture
Dynamically generates semantic search variations for each domain/subdomain,
attaching explicit DiscoveryQueryIntent metadata and third-party domain safety rules.
"""
import logging
import random
from enum import Enum
from typing import List, Dict, Optional, Any
from app.safety.search_query_guard import search_query_guard

logger = logging.getLogger(__name__)


class DiscoveryQueryIntent(str, Enum):
    OFFICIAL_COMPANY_DISCOVERY = "official_company_discovery"
    DIRECTORY_DISCOVERY = "directory_discovery"
    COMPANY_EXPANSION = "company_expansion"
    OFFICIAL_DOMAIN_VERIFICATION = "official_domain_verification"
    RECRAWL = "recrawl"


# List of third-party domains that may be used for intelligence discovery
# but MUST NEVER directly enter the crawl queue as company candidate websites.
RESTRICTED_THIRD_PARTY_DOMAINS = [
    "linkedin.com", "crunchbase.com", "tracxn.com", "g2.com", "capterra.com",
    "clutch.co", "producthunt.com", "angellist.com", "wellfound.com", "ycombinator.com",
    "github.com", "wikipedia.org", "reddit.com", "medium.com", "substack.com",
    "quora.com", "facebook.com", "instagram.com", "x.com", "twitter.com", "youtube.com"
]

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

GEO_MODIFIERS = [
    "", "", "",  # Empty = global search (3x weight)
    "United States", "Europe", "United Kingdom", "Germany", "India",
    "Southeast Asia", "Singapore", "Australia", "Canada", "Israel",
    "Brazil", "France", "Netherlands", "Nordic countries", "Japan",
    "South Korea", "Middle East", "Africa", "Latin America",
]

# Safe discovery intent patterns targeting official company websites directly
CLEAN_DISCOVERY_INTENT_MODIFIERS = [
    "official website",
    "technology provider official site",
    "enterprise software official page",
    "software solutions company",
    "business platform official website",
    "industry leaders official site",
]

# Directory discovery intent patterns (requires company domain resolution)
DIRECTORY_DISCOVERY_INTENT_MODIFIERS = [
    "site:linkedin.com/company",
    "site:crunchbase.com",
    "site:g2.com categories",
    "site:ycombinator.com",
    "site:tracxn.com"
]


class KeywordExpander:
    """
    Generates diverse global discovery queries with Intent Metadata.
    Phase 1 — Structural Query Classification.
    """

    def __init__(self):
        self._domain_list = list(GLOBAL_TAXONOMY.keys())
        self._subdomain_ptr: Dict[str, int] = {}
        self._keyword_ptr: Dict[str, int] = {}

    def get_next_query(
        self,
        domain: str,
        subdomain: Optional[str] = None,
        skip_geos: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Return the next search query with explicit intent metadata.
        Returns:
        {
            "query": "...",
            "intent": DiscoveryQueryIntent.value,
            "can_crawl_result_directly": bool,
            "requires_company_domain_resolution": bool,
            "domain": "...",
            "subdomain": "...",
            "keyword": "..."
        }
        """
        domain_data = GLOBAL_TAXONOMY.get(domain, {})
        if not domain_data:
            domain = self._domain_list[0]
            domain_data = GLOBAL_TAXONOMY[domain]

        subdomain_keys = list(domain_data.keys())
        ptr = self._subdomain_ptr.get(domain, 0)
        selected_subdomain = subdomain or subdomain_keys[ptr % len(subdomain_keys)]
        self._subdomain_ptr[domain] = ptr + 1

        keywords = domain_data.get(selected_subdomain, [])
        if not keywords:
            keywords = [f"{selected_subdomain} companies"]

        kw_ptr = self._keyword_ptr.get(f"{domain}:{selected_subdomain}", 0)
        base_keyword = keywords[kw_ptr % len(keywords)]
        self._keyword_ptr[f"{domain}:{selected_subdomain}"] = kw_ptr + 1

        available_geos = [g for g in GEO_MODIFIERS if g not in (skip_geos or [])]
        geo = random.choice(available_geos)

        # 80% official company discovery, 20% directory discovery
        is_directory_search = random.random() < 0.20

        if is_directory_search:
            intent = DiscoveryQueryIntent.DIRECTORY_DISCOVERY
            modifier = random.choice(DIRECTORY_DISCOVERY_INTENT_MODIFIERS)
            can_crawl_directly = False
            requires_resolution = True
        else:
            intent = DiscoveryQueryIntent.OFFICIAL_COMPANY_DISCOVERY
            modifier = random.choice(CLEAN_DISCOVERY_INTENT_MODIFIERS)
            can_crawl_directly = True
            requires_resolution = False

        if geo:
            raw_query = f"{base_keyword} {modifier} {geo}".strip()
        else:
            raw_query = f"{base_keyword} {modifier}".strip()

        # Sanitize query with negative operators
        final_query = search_query_guard.sanitize_query_with_negative_operators(raw_query)

        return {
            "query": final_query,
            "intent": intent.value,
            "can_crawl_result_directly": can_crawl_directly,
            "requires_company_domain_resolution": requires_resolution,
            "domain": domain,
            "subdomain": selected_subdomain,
            "keyword": base_keyword,
            "geo": geo,
        }

    def expand_from_entity(self, entity_name: str, domain: str) -> List[Dict[str, Any]]:
        """
        Given a discovered entity name, generate search queries to find similar/competing companies.
        Phase 1 — Intent: COMPANY_EXPANSION.
        """
        expansions = [
            {
                "query": search_query_guard.sanitize_query_with_negative_operators(f"{entity_name} competitors official website"),
                "intent": DiscoveryQueryIntent.COMPANY_EXPANSION.value,
                "can_crawl_result_directly": True,
                "requires_company_domain_resolution": False
            },
            {
                "query": search_query_guard.sanitize_query_with_negative_operators(f"companies like {entity_name} official website"),
                "intent": DiscoveryQueryIntent.COMPANY_EXPANSION.value,
                "can_crawl_result_directly": True,
                "requires_company_domain_resolution": False
            },
            {
                "query": search_query_guard.sanitize_query_with_negative_operators(f"{entity_name} alternatives official site"),
                "intent": DiscoveryQueryIntent.COMPANY_EXPANSION.value,
                "can_crawl_result_directly": True,
                "requires_company_domain_resolution": False
            }
        ]
        return expansions

    def all_domains(self) -> List[str]:
        return self._domain_list

    def all_subdomains(self, domain: str) -> List[str]:
        return list(GLOBAL_TAXONOMY.get(domain, {}).keys())


keyword_expander = KeywordExpander()
