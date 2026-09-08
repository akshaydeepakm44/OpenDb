import os
import sys
from pathlib import Path

# Add backend directory to sys.path
backend_dir = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(backend_dir))

from app.crawler.agentic_verifier import agentic_verifier
from app.crawler.anti_hallucination_pipeline import anti_hallucination_pipeline
from app.persistence.database import staging_engine, get_db
from sqlalchemy.orm import sessionmaker
from app.persistence.models import Base, Company, SearchCandidate

def test_agentic_verifier():
    print("[TEST] Testing Agentic Data Completeness Verification Engine...")


    # Ensure staging DB tables exist
    Base.metadata.create_all(bind=staging_engine)

    SessionLocalStaging = sessionmaker(autocommit=False, autoflush=False, bind=staging_engine)
    db = SessionLocalStaging()

    try:
        # Create test company in staging DB
        test_domain = "siteground.com"
        comp = db.query(Company).filter(Company.canonical_domain == test_domain).first()
        if not comp:
            comp = Company(
                company_name="SiteGround",
                canonical_domain=test_domain,
                official_url=f"https://{test_domain}",
                status="QUALIFIED_COMPANY",
                industry="Cloud Infrastructure & Hosting",
                hq_country="Bulgaria",
                employee_size="100-500",
                revenue_range="Private"
            )
            db.add(comp)
            db.commit()

        print(f"Testing execution for domain: {test_domain} (ID: {comp.id})")
        dossier = agentic_verifier.execute_agentic_verification(
            company_id=comp.id,
            domain=test_domain,
            db_session=db
        )

        completeness = dossier.get("data_completeness", {})
        print("\n==========================================")
        print("VERIFICATION SUCCESSFUL")
        print("==========================================")
        print(f"Company: {dossier.get('company_name')}")
        print(f"Domain: {dossier.get('website', {}).get('domain')}")
        print(f"Total Score: {completeness.get('total_score')} / 100")
        badge_clean = str(completeness.get('badge_level')).encode('ascii', 'ignore').decode('ascii').strip()
        print(f"Badge Level: {badge_clean}")
        print(f"Formula Explanation: {completeness.get('formula_explanation')}")

        print("\nChecklist Items:")
        for k, v in (completeness.get("checklist") or {}).items():
            print(f"  - {k}: {v}")

        assert completeness.get("total_score") is not None
        assert completeness.get("badge_level") is not None
        assert len(completeness.get("checklist", {})) == 11
        print("\nALL ASSERTIONS PASSED PERFECTLY!")


    finally:
        db.close()

if __name__ == "__main__":
    test_agentic_verifier()
