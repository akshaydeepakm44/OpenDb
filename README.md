# OpenDB Web Crawling & Ingestion Prototype

OpenDB is a prototype universal, domain-aware database engine designed to collect publicly available web information, extract structured records, normalize facts, track provenance/evidence, and store normalized records in PostgreSQL.

## Architecture Pipeline

```
USER INPUT ➔ CRAWL4AI ➔ PAGE DISCOVERY ➔ RAW RESOURCE COLLECTION ➔ CONTENT EXTRACTION ➔ DOMAIN DETECTION ➔ UNIVERSAL METADATA ➔ DOMAIN-SPECIFIC EXTRACTION ➔ VALIDATION ➔ POSTGRESQL ➔ WEB UI
```

## Quick Start

### Option 1: Docker Compose (Recommended)

```bash
docker compose up -d
```

- **Frontend Dashboard**: `http://localhost:3000` (or `http://localhost:5173`)
- **Backend API**: `http://localhost:8000`
- **Swagger Docs**: `http://localhost:8000/docs`
- **PostgreSQL**: `localhost:5433` (`admin` / `password123`)

### Option 2: Local Python & Vite Setup

1. **Start PostgreSQL**:
   ```bash
   docker compose -f db/docker-compose.yml up -d
   ```

2. **Start Backend**:
   ```bash
   cd backend
   ..\venv\Scripts\uvicorn app.main:app --reload --port 8000
   ```

3. **Start Frontend**:
   ```bash
   cd frontend
   npm install
   npm run dev
   ```

4. **Run Unit & Integration Tests**:
   ```bash
   pytest backend/tests/test_pipeline.py
   ```

## Documentation Files

- [ARCHITECTURE.md](ARCHITECTURE.md) - High level system design & design decisions
- [DATABASE.md](DATABASE.md) - PostgreSQL schema, tables, and replacement strategy
- [EXTRACTION.md](EXTRACTION.md) - Dual extraction mode (Deterministic + LLM/Heuristic)
- [SCHEMA_DESIGN.md](SCHEMA_DESIGN.md) - Universal and Domain JSON schemas
- [API.md](API.md) - REST API specification
- [TESTING.md](TESTING.md) - Test suite execution and coverage
- [CHECKPOINTS.md](CHECKPOINTS.md) - Debug checklist and verification checkpoints
