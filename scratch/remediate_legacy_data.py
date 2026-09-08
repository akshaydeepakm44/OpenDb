import sys
from pathlib import Path
from datetime import datetime, timezone

# Add backend directory to sys.path
backend_dir = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(backend_dir))

from app.persistence.database import staging_engine
from sqlalchemy.orm import sessionmaker
from app.persistence.models import Base, Company, UniversalRecord, DataCompletenessScore, QuarantineRecord
from app.crawler.global_verifier import global_verifier

def remediate_legacy_data():
    print("[REMEDIATION] Scanning and remediating legacy database records...")

    # Ensure staging DB tables exist
    Base.metadata.create_all(bind=staging_engine)

    SessionLocalStaging = sessionmaker(autocommit=False, autoflush=False, bind=staging_engine)
    db = SessionLocalStaging()

    try:
        companies = db.query(Company).all()
        print(f"Found {len(companies)} company records to audit...")

        dataset_sample = []
        for c in companies:
            c_dom = c.canonical_domain or ""
            c_name = c.company_name or c_dom.capitalize()
            dataset_sample.append({
                "id": c.id,
                "domain": c_dom,
                "company_name": c_name,
                "business_overview": {"text": c.business_overview} if c.business_overview else None,
                "technology_stack": c.technology_stack if isinstance(c.technology_stack, list) else []
            })

        remediated_count = 0
        quarantined_count = 0

        for c in companies:
            c_dom = c.canonical_domain or ""
            c_name = c.company_name or c_dom.capitalize()

            record_dict = {
                "id": c.id,
                "domain": c_dom,
                "company_name": c_name,
                "official_website": c.official_url or f"https://{c_dom}",
                "business_overview": {"text": c.business_overview} if c.business_overview else None,
                "technology_stack": c.technology_stack if isinstance(c.technology_stack, list) else []
            }

            ver_record = global_verifier.verify_record_against_dataset(record_dict, dataset_sample)
            g_ver = ver_record.get("global_verification", {})

            # Check if fields were nullified/rejected
            was_remediated = False
            if g_ver.get("rejected_fields"):
                was_remediated = True
                if "business_overview" in g_ver["rejected_fields"]:
                    c.business_overview = None
                if any("technology_stack" in rf for rf in g_ver["rejected_fields"]):
                    c.technology_stack = []

            completeness = ver_record.get("data_completeness", {})
            c.company_confidence_score = (completeness.get("total_score") or 0.0) / 100.0

            if not g_ver.get("passed"):
                c.status = "QUALIFIED_COMPANY"
                quarantined_count += 1

                # Save to QuarantineRecord
                q_rec = db.query(QuarantineRecord).filter(QuarantineRecord.company_id == c.id).first()
                if not q_rec:
                    q_rec = QuarantineRecord(
                        company_id=c.id,
                        domain=c_dom,
                        canonical_name=c_name,
                        rejection_reasons=g_ver.get("rejection_reasons", []),
                        quarantined_dossier=ver_record
                    )
                    db.add(q_rec)
                else:
                    q_rec.rejection_reasons = g_ver.get("rejection_reasons", [])
                    q_rec.quarantined_dossier = ver_record

            if was_remediated:
                remediated_count += 1

            db.commit()

        print("\n==========================================")
        print("REMEDIATION SUMMARY")
        print("==========================================")
        print(f"Total Companies Audited: {len(companies)}")
        print(f"Records Remediated (Boilerplate/Defaults Cleared): {remediated_count}")
        print(f"Records Quarantined (Dropped to INSUFFICIENT DATA): {quarantined_count}")
        print("==========================================")

    finally:
        db.close()

if __name__ == "__main__":
    remediate_legacy_data()
