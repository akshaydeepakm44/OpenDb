# Agent 2 Phase 2 Extraction Repair Report

## Root Cause
Phase 2 fields were frequently marked `NOT_FOUND_AFTER_SEARCH` because the existing investigation engine relied solely on search engine snippets (from SearXNG or DuckDuckGo) rather than actively crawling and extracting information from the actual web pages. Truncated snippets resulted in missing emails, incorrect company sizes, and hallucinated data during synthesis (because raw HTML was sent instead of clean text). Additionally, the `ResourceGovernor` frequently rejected deep crawl requests due to `HIGH_MEMORY`, completely blocking evidence acquisition.

## Before
The Phase 2 flow operated as follows:
`SearXNG Query -> Read 100-character Snippet -> Attempt Regex Extraction -> If missing, mark NOT_FOUND_AFTER_SEARCH`.
This bypassed proper crawling and resulted in false negatives. Furthermore, LinkedIn fallback results were strictly discarded by a hardcoded condition (`if results and not is_fb`), crippling leadership discovery when SearXNG was unstable.

## After
The architecture has been completely rewritten to mandate a rigorous `SEARCH → URL → CRAWL → EXTRACT → VALIDATE` pipeline.
1. **URL Discovery**: Targeted domain queries (e.g., `site:domain.com/contact email`) are executed.
2. **Governed Fetching**: The returned URLs are actively crawled using a new lightweight, `ResourceGovernor`-compliant `httpx` fetcher that bypasses heavy Playwright memory constraints but strictly enforces rate limits and robots.txt.
3. **Clean Extraction**: The resulting HTML is aggressively stripped of scripts/styles using `BeautifulSoup`, producing a clean text corpus.
4. **Validation**: Field-specific regex and heuristic rules extract the target data from the *entire page text*, generating accurate `EVIDENCE`.

## Field Results
- **Verified Contact Email**: Discovered URLs are crawled. Emails are extracted from full-page body text, avoiding generic domain mismatch.
- **Company Size & Headquarters**: Extracts from `/about` or `/team` full pages instead of snippets.
- **Final Status Guarantee**: `can_mark_not_found` now strictly requires `urls_crawled > 0`. If a crawl is blocked by infrastructure or `ResourceGovernor`, the status degrades to `RETRY_PENDING` / `UNVERIFIED` rather than fabricating a `NOT_FOUND_AFTER_SEARCH` state.

## LinkedIn
- Removed the `not is_fb` restriction in `discover_people`.
- DuckDuckGo fallback results now correctly surface LinkedIn candidate URLs.
- Candidates are matched deterministically based on parsed name and company verification without artificially demanding Playwright rendering of LinkedIn profiles (which often return 999 blocks).

## Resource Safety
A new `governed_lightweight_fetch` utility was created in `app/crawler/lightweight_fetcher.py`. This fetcher:
1. Calls `governor.can_start_crawl("lightweight")`.
2. Validates against `reputation_checker.is_robots_allowed()`.
3. Ensures strict 10s timeouts.
Targeted acquisition remains fully governed and will never bypass system health checks to force a result.

## Tests
- Command: `python -m scratch.test_e2e_local` on `fotmob.com`.
- Results: The logs correctly demonstrate the `_fetch_and_extract` helper fetching candidate URLs, stripping HTML, and applying regex extraction on the clean text corpus. Business synthesis returns a human-readable summary.

## Remaining Limitations
- Single Page Applications (SPAs) that require heavy JavaScript rendering to display contact information will return blank pages to the lightweight `httpx` fetcher. In these edge cases, extraction relies on the initial Playwright-crawled corpus (if successful).
- Anti-bot scraping protections (Cloudflare, Distil) may return 403 Forbidden errors to the lightweight fetcher, resulting in infrastructure failure retries.
