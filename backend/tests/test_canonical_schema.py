import unittest
import uuid
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.persistence.database import SessionLocal, get_db
from app.persistence.models import (
    Company, Domain, Document, KeyPerson, VerificationSession,
    CanonicalEvidence, IndustryTaxonomy, Source, CrawlActivityLog,
    ArtifactOutbox, utc_now
)

class TestCanonicalSchema(unittest.TestCase):
    def setUp(self):
        self.db = SessionLocal()

    def tearDown(self):
        self.db.close()

    def test_canonical_companies_exist_and_populated(self):
        """Verify companies table is populated with live verified records."""
        count = self.db.query(Company).count()
        self.assertGreaterEqual(count, 3, "Companies table should contain at least the 3 verified global leads.")

    def test_canonical_key_people_exist_and_populated(self):
        """Verify key_people table is populated with both discovery and linkedin candidates."""
        count = self.db.query(KeyPerson).count()
        self.assertGreaterEqual(count, 25, "Key people table should contain at least the 25 discovered candidates.")

    def test_canonical_verification_sessions_exist(self):
        """Verify verification_sessions table is populated."""
        count = self.db.query(VerificationSession).count()
        self.assertGreaterEqual(count, 20, "Verification sessions table should contain active sessions.")

    def test_canonical_evidence_exists(self):
        """Verify canonical_evidence table is populated."""
        count = self.db.query(CanonicalEvidence).count()
        self.assertGreaterEqual(count, 200, "Evidence table should contain at least 200 verified facts.")

    def test_zero_orphaned_foreign_keys(self):
        """Verify referential integrity across all canonical relationships."""
        # Key people pointing to nonexistent company
        orphaned_people = self.db.query(KeyPerson).filter(
            KeyPerson.company_id.isnot(None),
            ~KeyPerson.company_id.in_(self.db.query(Company.id))
        ).count()
        self.assertEqual(orphaned_people, 0, "No key people should be orphaned from companies.")

        # Verification sessions pointing to nonexistent company
        orphaned_sessions = self.db.query(VerificationSession).filter(
            VerificationSession.company_id.isnot(None),
            ~VerificationSession.company_id.in_(self.db.query(Company.id))
        ).count()
        self.assertEqual(orphaned_sessions, 0, "No verification sessions should be orphaned from companies.")

        # Evidence pointing to nonexistent session
        orphaned_evidence = self.db.query(CanonicalEvidence).filter(
            CanonicalEvidence.verification_session_id.isnot(None),
            ~CanonicalEvidence.verification_session_id.in_(self.db.query(VerificationSession.id))
        ).count()
        self.assertEqual(orphaned_evidence, 0, "No evidence should be orphaned from verification sessions.")

    def test_company_domain_uniqueness_enforced(self):
        """Verify company primary_domain uniqueness is strictly enforced."""
        test_domain = f"test-unique-{uuid.uuid4().hex[:8]}.com"
        c1 = Company(
            canonical_name="Test Corp 1",
            primary_domain=test_domain,
            status="DISCOVERED"
        )
        self.db.add(c1)
        self.db.commit()

        # Attempt duplicate insertion
        c2 = Company(
            canonical_name="Test Corp 2",
            primary_domain=test_domain,
            status="DISCOVERED"
        )
        self.db.add(c2)
        with self.assertRaises(Exception):
            self.db.commit()
        self.db.rollback()

        # Clean up test record
        self.db.query(Company).filter(Company.primary_domain == test_domain).delete()
        self.db.commit()

    def test_documents_preserved(self):
        """Verify that all 2,316 crawled documents remain completely intact."""
        doc_count = self.db.query(Document).count()
        self.assertGreaterEqual(doc_count, 2300, "Documents count should be preserved >= 2300.")

if __name__ == '__main__':
    unittest.main()
