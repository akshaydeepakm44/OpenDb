import uuid
import json
import sqlite3
from datetime import datetime, timezone

def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()

SEED_COMPANIES = [
    {
        "name": "Celonis SE",
        "domain": "Information Technology",
        "subdomain": "Enterprise Software",
        "url": "https://www.celonis.com",
        "country": "Germany",
        "location": "Munich, Germany",
        "description": "Global leader in Execution Management Systems (EMS) and process mining software.",
        "employees": "1,000 - 5,000",
        "founded": 2011,
        "status": "Verified",
        "confidence": 0.96,
        "tech_stack": ["Java", "React", "PostgreSQL", "AWS", "Docker", "Kubernetes"],
        "leadership": [
            {"name": "Bastian Nominacher", "title": "Co-CEO & Co-Founder"},
            {"name": "Alexander Rinke", "title": "Co-CEO & Co-Founder"},
            {"name": "Martin Klenk", "title": "CTO & Co-Founder"}
        ],
        "emails": ["info@celonis.com", "press@celonis.com"],
        "funding": "Series D ($1.4B Raised)"
    },
    {
        "name": "Personio GmbH",
        "domain": "Information Technology",
        "subdomain": "SaaS & Cloud",
        "url": "https://www.personio.com",
        "country": "Germany",
        "location": "Munich, Germany",
        "description": "All-in-one HR software platform for small and medium-sized enterprises across Europe.",
        "employees": "500 - 1,000",
        "founded": 2015,
        "status": "Verified",
        "confidence": 0.94,
        "tech_stack": ["Kotlin", "TypeScript", "Node.js", "AWS", "GraphQL"],
        "leadership": [
            {"name": "Hanno Renner", "title": "CEO & Co-Founder"},
            {"name": "Roman Schumacher", "title": "CPO & Co-Founder"}
        ],
        "emails": ["contact@personio.com", "sales@personio.com"],
        "funding": "Series E ($270M Raised)"
    },
    {
        "name": "DeepL SE",
        "domain": "Information Technology",
        "subdomain": "AI & Machine Learning",
        "url": "https://www.deepl.com",
        "country": "Germany",
        "location": "Cologne, Germany",
        "description": "Advanced AI translation and language communication technology powered by neural networks.",
        "employees": "250 - 500",
        "founded": 2017,
        "status": "Verified",
        "confidence": 0.98,
        "tech_stack": ["Python", "PyTorch", "C++", "CUDA", "FastAPI"],
        "leadership": [
            {"name": "Jaroslaw Kutylowski", "title": "CEO & Founder"}
        ],
        "emails": ["support@deepl.com", "press@deepl.com"],
        "funding": "Series B ($100M Raised)"
    },
    {
        "name": "N26 AG",
        "domain": "Financial Services & FinTech",
        "subdomain": "FinTech Startups",
        "url": "https://www.n26.com",
        "country": "Germany",
        "location": "Berlin, Germany",
        "description": "Mobile banking platform providing digital personal and business financial services across Europe.",
        "employees": "1,000 - 5,000",
        "founded": 2013,
        "status": "Verified",
        "confidence": 0.92,
        "tech_stack": ["Java", "Spring Boot", "Swift", "Kotlin", "AWS", "Kafka"],
        "leadership": [
            {"name": "Valentin Stalf", "title": "Co-CEO & Co-Founder"},
            {"name": "Maximilian Tayenthal", "title": "Co-CEO & Co-Founder"}
        ],
        "emails": ["support@n26.com", "press@n26.com"],
        "funding": "Series E ($900M Raised)"
    },
    {
        "name": "Trade Republic Bank GmbH",
        "domain": "Financial Services & FinTech",
        "subdomain": "Investment & Wealth",
        "url": "https://www.traderepublic.com",
        "country": "Germany",
        "location": "Berlin, Germany",
        "description": "European commission-free digital investment platform and licensed neobroker.",
        "employees": "500 - 1,000",
        "founded": 2015,
        "status": "Verified",
        "confidence": 0.95,
        "tech_stack": ["Go", "React Native", "PostgreSQL", "Google Cloud Platform", "Kubernetes"],
        "leadership": [
            {"name": "Christian Hecker", "title": "Co-Founder"},
            {"name": "Thomas Pischke", "title": "Co-Founder"}
        ],
        "emails": ["service@traderepublic.com"],
        "funding": "Series C ($900M Raised)"
    },
    {
        "name": "Contentful GmbH",
        "domain": "Information Technology",
        "subdomain": "Developer Tools",
        "url": "https://www.contentful.com",
        "country": "Germany",
        "location": "Berlin, Germany",
        "description": "Composable content platform enabling digital teams to build and scale headless digital experiences.",
        "employees": "500 - 1,000",
        "founded": 2013,
        "status": "Verified",
        "confidence": 0.91,
        "tech_stack": ["Node.js", "React", "Ruby", "AWS", "Elasticsearch", "Redis"],
        "leadership": [
            {"name": "Karthik Rau", "title": "CEO"},
            {"name": "Sascha Konietzke", "title": "Co-Founder & Strategy"}
        ],
        "emails": ["support@contentful.com", "sales@contentful.com"],
        "funding": "Series F ($175M Raised)"
    },
    {
        "name": "Lilium N.V.",
        "domain": "Manufacturing & Industrial",
        "subdomain": "Automation & Robotics",
        "url": "https://www.lilium.com",
        "country": "Germany",
        "location": "Gauting, Germany",
        "description": "Developer of electric vertical take-off and landing (eVTOL) jet aircraft for sustainable regional air mobility.",
        "employees": "500 - 1,000",
        "founded": 2015,
        "status": "Verified",
        "confidence": 0.89,
        "tech_stack": ["Simulink", "C++", "Python", "CATIA", "Embedded Linux"],
        "leadership": [
            {"name": "Klaus Roewe", "title": "CEO"},
            {"name": "Daniel Wiegand", "title": "Co-Founder & Chief Engineer"}
        ],
        "emails": ["press@lilium.com", "investors@lilium.com"],
        "funding": "Public (NASDAQ: LILM)"
    },
    {
        "name": "FlixMobility GmbH",
        "domain": "E-Commerce & Retail",
        "subdomain": "Logistics & Supply Chain",
        "url": "https://www.flixbus.com",
        "country": "Germany",
        "location": "Munich, Germany",
        "description": "Global mobility provider offering sustainable long-distance bus and train travel networks across Europe & US.",
        "employees": "1,000 - 5,000",
        "founded": 2013,
        "status": "Verified",
        "confidence": 0.93,
        "tech_stack": ["PHP", "Vue.js", "Python", "AWS", "MySQL", "RabbitMQ"],
        "leadership": [
            {"name": "André Schwämmlein", "title": "CEO & Co-Founder"},
            {"name": "Jochen Engert", "title": "Co-Founder"}
        ],
        "emails": ["service@flixbus.com", "media@flixbus.com"],
        "funding": "Series G ($650M Raised)"
    }
]

def seed_db(db_path):
    print(f"Seeding rich firmographic cards into {db_path}...")
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    now = utc_now_iso()

    for comp in SEED_COMPANIES:
        doc_id = str(uuid.uuid4())
        rec_id = str(uuid.uuid4())
        dom_rec_id = str(uuid.uuid4())

        # Insert Document
        raw_path = f"s3://opendb/raw/pages/{str(uuid.uuid4())[:12]}.html"
        cur.execute("""
            INSERT OR REPLACE INTO documents (
                id, url, title, content_type, content_hash, word_count, raw_path, created_at, retrieved_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            doc_id, comp["url"], f"{comp['name']} - Official Homepage", "text/html",
            str(uuid.uuid4())[:16], 1250, raw_path, now, now
        ))

        # Insert Universal Record
        metadata_dict = json.dumps({
            "employees": comp["employees"],
            "founded": comp["founded"],
            "website": comp["url"],
            "description": comp["description"],
            "company_size": comp["employees"],
            "tech_stack": comp["tech_stack"],
            "leadership": comp["leadership"],
            "contact_emails": comp["emails"],
            "funding_stage": comp["funding"]
        })

        cur.execute("""
            INSERT OR REPLACE INTO universal_records (
                id, document_id, canonical_name, title, description, url,
                country, location, status, confidence, metadata_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            rec_id, doc_id, comp["name"], comp["name"], comp["description"], comp["url"],
            comp["country"], comp["location"], comp["status"], comp["confidence"],
            metadata_dict, now, now
        ))

        # Insert Domain Record with Rich Schema Data
        domain_data = json.dumps({
            "company_name": comp["name"],
            "website": comp["url"],
            "description": comp["description"],
            "country": comp["country"],
            "location": comp["location"],
            "employee_count": comp["employees"],
            "company_size": comp["employees"],
            "founded_year": comp["founded"],
            "domain": comp["domain"],
            "subdomain": comp["subdomain"],
            "technologies": comp["tech_stack"],
            "tech_stack": comp["tech_stack"],
            "key_people": comp["leadership"],
            "leadership": comp["leadership"],
            "contact_emails": comp["emails"],
            "emails": comp["emails"],
            "funding_stage": comp["funding"],
            "revenue": comp["funding"]
        })

        cur.execute("""
            INSERT OR REPLACE INTO domain_records (
                id, universal_record_id, schema_version, data, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
        """, (
            dom_rec_id, rec_id, "v1.0.0", domain_data, now, now
        ))

        # Insert Extracted Facts
        facts = [
            ("company_name", comp["name"], "string", 0.99),
            ("website", comp["url"], "string", 0.98),
            ("headquarters", comp["location"], "string", 0.95),
            ("employee_count", comp["employees"], "string", 0.92),
            ("funding_stage", comp["funding"], "string", 0.90),
        ]
        for fname, fval, ftype, fconf in facts:
            fact_id = str(uuid.uuid4())
            cur.execute("""
                INSERT OR REPLACE INTO extracted_facts (
                    id, document_id, universal_record_id, field_name, field_value, value_type, confidence, extractor, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (fact_id, doc_id, rec_id, fname, fval, ftype, fconf, "llm", now))

            # Insert Evidence snippet
            cur.execute("""
                INSERT OR REPLACE INTO evidence (
                    id, fact_id, document_id, source_url, text_snippet, confidence, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                str(uuid.uuid4()), fact_id, doc_id, comp["url"],
                f"Verified official snippet for {fname}: '{fval}' extracted from {comp['url']}",
                fconf, now
            ))

        # Insert Search History
        cur.execute("""
            INSERT INTO search_history (
                id, keyword, domain, sources_found, relevant_sources, entities_discovered, log_message, executed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            str(uuid.uuid4()), f"{comp['subdomain']} companies {comp['country']}", comp["domain"],
            25, 18, 1, f"Successfully discovered entity '{comp['name']}' at {comp['url']}", now
        ))

        # Insert Crawl Activity Log
        cur.execute("""
            INSERT INTO crawl_activity_log (
                id, url, domain, stage, status, message, entity_name, timestamp
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            str(uuid.uuid4()), comp["url"], comp["domain"], "EXTRACTION", "COMPLETED",
            f"Extracted 18 fields for {comp['name']} (Confidence: {comp['confidence']})", comp["name"], now
        ))

    conn.commit()
    conn.close()
    print(f"Successfully seeded {len(SEED_COMPANIES)} rich company dossier cards into {db_path}!")

if __name__ == "__main__":
    import os
    for db in ["opendb_fallback.db"]:
        if os.path.exists(db):
            seed_db(db)
