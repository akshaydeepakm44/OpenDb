# OpenDB Autonomous Web Discovery & Ingestion Engine

OpenDB is an autonomous, 24/7 self-sustaining web discovery and lead intelligence pipeline. It continuously searches the web, crawls targeted business entities using Crawl4AI & Playwright, extracts structured schema payloads via LiteLLM/heuristics, and persists verified data into PostgreSQL or thread-safe SQLite WAL storage.

## 🏗️ Architecture Overview

```
REACT DASHBOARD (5173) ➔ VITE PROXY ➔ FASTAPI BACKEND (8000) ➔ HAYSTACK 2.X AGENT
      │                                                                  │
      ▼                                                                  ▼
PERSISTED UI METRICS ◄── SQLITE WAL / POSTGRES ◄── QUALITY FILTER ◄── CRAWL4AI + LLM
```

## 🚀 Quick Start

### 1. Backend Server (FastAPI on Port 8000)
```bash
cd backend
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```
- **Backend API**: `http://127.0.0.1:8000`
- **Interactive Swagger Docs**: `http://127.0.0.1:8000/docs`

### 2. Frontend Dashboard (Vite + React on Port 5173)
```bash
cd frontend
npm install
npm run dev
```
- **Dashboard UI**: `http://localhost:5173`

---

## ⚡ Key Infrastructure & Capabilities

- **Autonomous Agent Loop**: 24/7 continuous discovery orchestrator powered by Haystack 2.x reasoning patterns and dynamic keyword expansion.
- **Fault-Tolerant Task Queue**: `_safe_dispatch()` dynamically detects active Celery/Redis workers and seamlessly falls back to non-blocking background daemon threads.
- **SQLite WAL Mode**: Thread-safe database engine (`PRAGMA journal_mode=WAL; PRAGMA busy_timeout=30000;`) ensuring zero API request blocking during background web crawling.
- **Instant Service Health Check**: Micro socket probes (<5ms latency) monitoring PostgreSQL/SQLite, Redis, MinIO, SearXNG, Crawl4AI, and LLM engines.

---

## 📖 Documentation Index

- [CODE_FLOW_EXPLANATION.md](CODE_FLOW_EXPLANATION.md) — Complete code walkthrough from input trigger to output UI rendering.
- [ARCHITECTURE.md](ARCHITECTURE.md) — High-level system design & modular component boundaries.
- [DATABASE.md](DATABASE.md) — Schema design, relational models, and dual DB strategy.
- [EXTRACTION.md](EXTRACTION.md) — Multi-stage extraction (CSS + LiteLLM + Heuristic Rule Engine).
- [API.md](API.md) — Complete REST API endpoint specification.
- [PIPELINE_WALKTHROUGH.md](PIPELINE_WALKTHROUGH.md) — Step-by-step pipeline execution breakdown.
- [TESTING.md](TESTING.md) — Test suite execution and validation setup.

