# OpenDB Extraction Strategy Specification

## Dual Extraction Architecture

OpenDB enforces a hybrid extraction strategy to balance performance, latency, accuracy, and cost.

### Mode 1: Deterministic CSS & HTML Metadata Extraction

Deterministic fields are extracted directly from DOM tree nodes without calling an LLM:

- Page `<title>` and OpenGraph `og:title`
- Meta `<meta name="description">` and `og:description`
- Canonical `<link rel="canonical">`
- Language `<html lang="...">`
- JSON-LD `<script type="application/ld+json">`
- Top level `<h1>` headings

### Mode 2: Schema-Based Domain Semantic Extraction

Semantic fields that cannot be reliably captured with static CSS selectors (e.g. `products`, `services`, `founded_year`, `leadership`) are extracted using:

1. **LiteLLM / OpenAI Structured Output**: When `OPENAI_API_KEY` is provided, standard JSON schema prompt instructions are sent to `gpt-4o-mini` with zero temperature.
2. **Rule-Based Heuristic Extractor**: When offline or without an API key, pattern matching heuristics identify keywords, dates, contact emails, and section headers.

### Strict Missing Data Rules

- Missing fields return explicit `null` (or `[]` for arrays).
- The system never fabricates or hallucinates information.
- Quality confidence scores (0.00 to 1.00) are assigned to every record.
