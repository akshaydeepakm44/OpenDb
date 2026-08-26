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
