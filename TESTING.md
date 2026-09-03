# OpenDB Testing Specification

## Running Backend Tests

```bash
$env:PYTHONPATH="backend"
.\venv\Scripts\pytest backend/tests/test_pipeline.py -v
```

## Test Coverage

1. **Normalization Tests**: URL fragment/tracking parameter removal, country normalization, language normalization.
2. **Domain Classification Tests**: Keyword signal scoring and confidence rating.
3. **Schema Registry Tests**: Universal & domain JSON schema loading.
4. **Deterministic Extractor Tests**: HTML title, meta description, H1 extraction.
5. **Missing Fields & Heuristic Tests**: Verifying null returns for missing fields and rule-based semantic extraction.





Explaiin me these clearly:

1. I need to understand what are the keywords given to searxng by haystack and how it is refining the search process? is it modyfying the keyword search if yes how?
2.explain me clearly that how the searxng is starting the search and how the url's are given ?
3. What is celery and how the urls are stored in celery? why not redis?
4. After the redis queue how crawl4ai is getting them and how the crawling is performed?
5. After the crawl how the data is extracted and filtered?
6. after filteration how the data is stored and where are they stored