import os
import sys
import shutil
from pathlib import Path

# Add backend directory to sys.path
backend_dir = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(backend_dir))

from app.persistence.database import staging_engine
from sqlalchemy.orm import sessionmaker
from app.persistence.models import (
    Base, GlobalLeadSubpage, GlobalLeadPerson, GlobalLead, OpenLakeRecord,
    ResourceLink, Resource, ExtractionRun, DocumentVersion,
    Evidence, ExtractedFact, VerificationRecord, DomainRecord,
    UniversalRecord, Document, CrawlJob, CrawlError,
    CrawlActivityLog, SearchHistory, BatchResult, AgentState,
    Company, SearchCandidate, VerificationRun, VerificationRequirement,
    VerificationCrawlRequest, VerificationCrawlResult, CompanyEvidence,
    DataCompletenessScore, QuarantineRecord, GlobalVerificationReport,
    DenyListDomain, DenyListCategory
)

def clear_all_data():
    print("[RESET] Clearing all crawled and verified data from OpenDB staging database...")

    # Ensure tables exist
    Base.metadata.create_all(bind=staging_engine)

    SessionLocalStaging = sessionmaker(autocommit=False, autoflush=False, bind=staging_engine)
    db = SessionLocalStaging()

    models_to_clear = [
        GlobalLeadSubpage, GlobalLeadPerson, GlobalLead, OpenLakeRecord,
        ResourceLink, Resource, ExtractionRun, DocumentVersion,
        Evidence, ExtractedFact, VerificationRecord, DomainRecord,
        UniversalRecord, Document, CrawlJob, CrawlError,
        CrawlActivityLog, SearchHistory, BatchResult, AgentState,
        Company, SearchCandidate, VerificationRun, VerificationRequirement,
        VerificationCrawlRequest, VerificationCrawlResult, CompanyEvidence,
        DataCompletenessScore, QuarantineRecord, GlobalVerificationReport
    ]

    cleared_count = 0
    for m in models_to_clear:
        try:
            num = db.query(m).delete()
            cleared_count += num
            db.commit()
            print(f"  - Cleared {m.__tablename__}: {num} rows")
        except Exception as e:
            db.rollback()
            print(f"  - Warning clearing {m.__tablename__}: {e}")

    # Clean local storage directories
    data_dir = backend_dir / "data"
    if data_dir.exists():
        for sub in ["raw", "processed", "manifests", "markdown", "text", "extracted", "pages", "companies"]:
            sub_path = data_dir / sub
            if sub_path.exists():
                try:
                    for f in os.listdir(sub_path):
                        fp = sub_path / f
                        if fp.is_file():
                            fp.unlink()
                        elif fp.is_dir():
                            shutil.rmtree(fp, ignore_errors=True)
                    print(f"  - Cleaned storage directory: {sub}")
                except Exception as e:
                    print(f"  - Warning cleaning {sub}: {e}")

    db.close()
    print("[RESET] Database and disk storage completely reset and cleared of all records.")

if __name__ == "__main__":
    clear_all_data()
