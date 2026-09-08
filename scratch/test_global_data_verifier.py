import sys
from pathlib import Path
from datetime import datetime, timezone

# Add backend directory to sys.path
backend_dir = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(backend_dir))

from app.crawler.global_verifier import global_verifier

def test_global_data_verifier():
    print("[TEST] Running 7 CI Smell Tests for Global Data Verification Layer...")

    # Sample dataset representing N >= 30 records
    dataset_sample = []
    for i in range(35):
        dataset_sample.append({
            "id": f"comp_{i}",
            "domain": f"domain{i}.com",
            "company_name": f"Company{i}",
            "official_website": f"https://domain{i}.com",
            "business_overview": {"text": f"Company{i} provides cloud automation for enterprise workflows."},
            "technology_stack": [{"value": "Python", "source_url": f"https://domain{i}.com"}]
        })

    # Test 1: Boilerplate overview rejection
    print("\n--- Test 1: Template Boilerplate Overview Rejection ---")
    bad_record_1 = {
        "id": "bad_1",
        "domain": "testbad1.com",
        "company_name": "TestBad1",
        "business_overview": {"text": "TestBad1 enterprise lead record."},
        "technology_stack": []
    }
    res_1 = global_verifier.verify_record_against_dataset(bad_record_1, dataset_sample)
    pass_1 = res_1["global_verification"]["passed"]
    print(f"Result for boilerplate record: passed={pass_1}, rejections={res_1['global_verification']['rejection_reasons']}")
    assert not pass_1, "FAIL: Boilerplate record should be rejected by Checkpoint A"
    assert res_1["business_overview"]["text"] is None, "FAIL: Overview text should be nullified"

    # Test 2: SimHash duplicate overview rejection
    print("\n--- Test 2: SimHash Duplicate Overview Rejection ---")
    bad_record_2 = {
        "id": "bad_2",
        "domain": "testbad2.com",
        "company_name": "TestBad2",
        "business_overview": {"text": "Company0 provides cloud automation for enterprise workflows."}, # Identical to comp_0
        "technology_stack": []
    }
    res_2 = global_verifier.verify_record_against_dataset(bad_record_2, dataset_sample)
    pass_2 = res_2["global_verification"]["passed"]
    print(f"Result for SimHash duplicate record: passed={pass_2}, rejections={res_2['global_verification']['rejection_reasons']}")
    assert not pass_2, "FAIL: SimHash duplicate overview should be rejected by Checkpoint A"

    # Test 3: Checkpoint B Uniform Default Frequency Disambiguation
    print("\n--- Test 3: Checkpoint B Uniform Default Disambiguation ---")
    # Add fake high-frequency tech tag "Cloud Platform" to >15% dataset records WITHOUT verbatim page match
    sample_with_defaults = list(dataset_sample)
    for r in sample_with_defaults[:10]: # 10/35 = 28% > 15%
        r["technology_stack"].append({"value": "Cloud Platform", "source_url": "https://unmatched-page.org"})

    bad_record_3 = {
        "id": "bad_3",
        "domain": "testbad3.com",
        "company_name": "TestBad3",
        "business_overview": {"text": "TestBad3 is a specialized security platform for fintech."},
        "technology_stack": [{"value": "Cloud Platform", "source_url": "https://unmatched-page.org"}]
    }
    res_3 = global_verifier.verify_record_against_dataset(bad_record_3, sample_with_defaults)
    tech_3 = res_3.get("technology_stack", [])
    print(f"Result for unbacked high-frequency default: remaining tech stack={tech_3}")
    assert len(tech_3) == 0, "FAIL: Unbacked high-frequency default 'Cloud Platform' should be nullified"

    # Test 4: Checkpoint C Timestamp Anomaly Detection
    print("\n--- Test 4: Checkpoint C Batch Timestamp Anomaly ---")
    identical_batch = []
    same_ts = "2026-09-08T00:00:00Z"
    for i in range(10):
        identical_batch.append({
            "id": f"b_{i}",
            "domain": f"b{i}.com",
            "extraction_audit": {"crawl_finished_at": same_ts}
        })
    batch_res = global_verifier.verify_batch(identical_batch)
    batch_accepted = batch_res["report"]["batch_accepted"]
    print(f"Batch verification result for identical timestamps: accepted={batch_accepted}")
    assert not batch_accepted, "FAIL: Identical timestamp batch should fail Checkpoint C"

    # Test 5: Checkpoint D Aggregator Domain Denylist
    print("\n--- Test 5: Checkpoint D Aggregator Domain Denylist ---")
    agg_record = {
        "id": "agg_1",
        "domain": "softonic.com",
        "company_name": "Instagram",
        "business_overview": {"text": "Instagram app download page on Softonic."}
    }
    res_5 = global_verifier.verify_record_against_dataset(agg_record, dataset_sample)
    pass_5 = res_5["global_verification"]["passed"]
    print(f"Result for aggregator domain softonic.com: passed={pass_5}, rejections={res_5['global_verification']['rejection_reasons']}")
    assert not pass_5, "FAIL: Softonic.com should be rejected by Checkpoint D"

    # Test 6: Checkpoint G Score Gate & Forced INSUFFICIENT DATA Cap
    print("\n--- Test 6: Checkpoint G Forced INSUFFICIENT DATA Cap ---")
    multi_fail_record = {
        "id": "mf_1",
        "domain": "softonic.com", # Fails D
        "company_name": "TestMultiFail",
        "business_overview": {"text": "TestMultiFail enterprise lead record."}, # Fails A
        "technology_stack": []
    }
    res_6 = global_verifier.verify_record_against_dataset(multi_fail_record, dataset_sample)
    badge_level = res_6["data_completeness"]["badge_level"]
    badge_clean = str(badge_level).encode('ascii', 'ignore').decode('ascii').strip()
    print(f"Result for multi-fail record: badge={badge_clean}, score={res_6['data_completeness']['total_score']}")
    assert "INSUFFICIENT DATA" in badge_level, "FAIL: Multi-fail record must be capped at INSUFFICIENT DATA"


    # Test 7: Valid Unique Record Passes Verification
    print("\n--- Test 7: Valid Verified Record Pass ---")
    valid_record = {
        "id": "valid_1",
        "domain": "valid-enterprise-tech.org",
        "company_name": "ValidEnterpriseTech",
        "official_website": "https://valid-enterprise-tech.org",
        "business_overview": {"text": "ValidEnterpriseTech builds secure microservices infrastructure for healthcare systems."},
        "technology_stack": [{"value": "Rust", "source_url": "https://valid-enterprise-tech.org"}]
    }
    res_7 = global_verifier.verify_record_against_dataset(valid_record, dataset_sample)
    pass_7 = res_7["global_verification"]["passed"]
    print(f"Result for valid record: passed={pass_7}")
    assert pass_7, "FAIL: Valid unique record should pass all global checkpoints"

    print("\n==========================================")
    print("[SUCCESS] ALL 7 CI SMELL TESTS PASSED PERFECTLY!")
    print("==========================================")

if __name__ == "__main__":
    test_global_data_verifier()
