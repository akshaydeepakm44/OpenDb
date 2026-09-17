# Agent 2 Production Repair Documentation

## Executive Summary
Agent 2's backend pipeline has been hardened into a true durable state machine that strictly adheres to production environment constraints and the Authoritative Verification Contract.

## 1. Celery Worker Enforced Routing
**Root Cause D (Local Thread Fallback) Fixed.**
Previously, `tasks.py::_safe_dispatch` would silently fall back to running the Agent 2 pipeline locally in a daemon thread if a Celery worker was not active. This violated the requirement that `execute_full_verification` only run on a dedicated, long-running worker.
- **Fix:** In `tasks.py`, if the task name contains `agent2` and the environment is `production`, `_safe_dispatch` will now raise a `RuntimeError` (`QUEUE_FAILED`) to explicitly fail closed if the worker is unavailable. This ensures the verification state correctly stalls and waits for the infrastructure, rather than running in non-resumable ephemeral threads.

## 2. Durable State Machine Refactoring
**Root Cause E (Sequential Script Execution) Fixed.**
The monolithic `execute_full_verification` orchestrator function was rewritten as a while-loop evaluating the `session.status`.
- **Fix:** Each execution phase explicitly transitions the status state (e.g. `PHASE1_RANKED` -> `PHASE1_VERIFIED` -> `PHASE2_SYNTHESIS` -> `PERSON_MATCHING`).
- **Benefit:** If the Celery worker restarts midway through a dossier, the pipeline will resume precisely at the interrupted state (e.g. `PEOPLE_DISCOVERY`), avoiding wasteful regeneration of Phase 1 and 2 tasks.

## 3. Website-First People Discovery
**Root Causes A & C (Over-reliance on LinkedIn & Dropped Subpages) Fixed.**
- **Fix:** `agent2_orchestrator.discover_people` (formerly `discover_and_verify_linkedin`) now executes a two-stage approach:
  1. Checks for `/about`, `/team` pages in the crawled subpages metadata and queries SearXNG specifically for the official domain team pages.
  2. Falls back to targeted LinkedIn multi-round queries only if insufficient candidates are found.
- **Fix:** Relaxed the hard-coding in `agent2_haystack_components.evaluate_person_company_match` to accept generic `source_url` evidence from official domain pages, no longer strictly mandating an `in/` LinkedIn profile.

## 4. Preservation of NOT_FOUND Evidence
**Root Cause B (Investigation JSON Loss) Fixed.**
- **Fix:** The `agent2_investigation.py` correctly stores the `investigation_record` (containing queries attempted, sources examined, and search rounds) in the `Agent2Evidence` DB rows even if the field's resulting status is `NOT_FOUND_AFTER_SEARCH`.
- **Fix:** Updated `frontend/src/App.jsx` to parse and render this `investigation_record` whenever the evidence status is `NOT_FOUND_AFTER_SEARCH`. This provides the user with an "Investigation Audit" trail detailing exactly why the field was exhausted.

## 5. Optional Fields Logic Validated
Missing optional fields (e.g., Company Size, Verified Contact Email, LinkedIn URL, Founded Year) correctly resolve to `NOT_FOUND_AFTER_SEARCH` and lower the Completeness Score, but they do **NOT** block the lead from being `VERIFIED` as per `verification_contract.py`.

## Testing
- **Unit tests written:** `test_agent2_state_machine.py`, `test_agent2_people_discovery.py`, `test_agent2_dispatch.py`.
