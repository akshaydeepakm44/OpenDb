import sys
import os
sys.path.insert(0, os.path.abspath("."))
import asyncio
from app.crawler.searxng_service import searxng_service
from app.extraction.key_people_extractor import key_people_extractor

async def main():
    queries = [
        'PostHog linkedin people',
        'PostHog CEO site:linkedin.com/in',
        'PostHog founder site:linkedin.com/in',
        '"PostHog" founder site:linkedin.com/in'
    ]
    for q in queries:
        print(f"\n=== Query: {q} ===")
        results, is_fallback, log_msg = await searxng_service.search_with_meta(query=q, max_results=5)
        print(f"Results count: {len(results)}")
        for r in results:
            print(f"  URL: {r.get('url')}")
            print(f"  TITLE: {r.get('title', '').encode('ascii', 'replace').decode()}")
        extracted = key_people_extractor.extract_from_linkedin_search_snippets(results, "PostHog")
        print("  EXTRACTED:", extracted)

if __name__ == "__main__":
    asyncio.run(main())
