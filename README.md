# OpenDB — Autonomous Company Intelligence & Crawler Platform

OpenDB is an autonomous, dual-agent company discovery and verified intelligence platform.

---

## 1. Project Architecture & Directory Structure

```text
OpenDb/
├── backend/                # FastAPI backend + Celery distributed workers
│   ├── app/                # Core Python application modules
│   │   ├── agent/          # Autonomous Discovery (Agent 1) & Verification (Agent 2)
│   │   ├── api/            # REST API endpoints (FastAPI routers)
│   │   ├── audit/          # Checkpoints, structured logs, and observability
│   │   ├── cache/          # Redis and local LRU caching
│   │   ├── classification/ # Domain and industry classifier
│   │   ├── crawler/        # Crawl4AI, distributed slot manager, SearXNG service
│   │   ├── extraction/     # Person verification, BeautifulSoup & regex extractors
│   │   ├── haystack/       # RAG business synthesis pipeline
│   │   ├── persistence/    # SQLAlchemy models, database sessions, outbox sync
│   │   ├── safety/         # Resource governor and circuit breaker
│   │   ├── schemas/        # Pydantic request/response schemas
│   │   ├── storage/        # MinIO S3 object storage client
│   │   ├── verification/   # Authoritative Verification Contract
│   │   └── worker/         # Celery task definitions and queue routing
│   ├── Dockerfile          # Backend container specification
│   ├── requirements.txt    # Python dependencies
│   └── reset_data.py       # Administrative database clearing utility
│
├── frontend/               # React / Vite Web Application
│   ├── src/                # UI components, cards, verification modals
│   ├── package.json        # Frontend dependencies
│   └── Dockerfile          # Frontend container specification
│
├── schemas/                # Universal JSON validation schemas (mounted read-only)
│   ├── domains/            # Domain classification contracts
│   └── universal/          # Resource and lead validation schemas
│
├── searxng/                # SearXNG private meta-search service configuration
│   └── settings.yml        # Search engine weights and category definitions
│
├── data/                   # Persistent host storage volume (mounted to /app/data)
├── init.sql                # PostgreSQL + pgvector schema initialization
├── docker-compose.yml      # Primary Docker Compose specification (development & production)
├── docker-compose.prod.yml # Production image-based override
├── .env.example            # Canonical environment variable template
└── Makefile                # Development lifecycle shortcuts
```

### Why are `schemas/`, `searxng/`, `data/`, and `init.sql` at the Root Level?
- **`searxng/` and `init.sql`:** These configure shared infrastructure services (PostgreSQL and SearXNG meta-search). Following standard microservices practices, infrastructure configs that belong to neither frontend nor backend live at the project root alongside `docker-compose.yml`.
- **`schemas/`:** Contains shared JSON data validation contracts. It is mounted read-only into `/app/schemas:ro` in Docker so contracts can be validated without coupling schemas into application code.
- **`data/`:** Serves as the host bind-mount directory for raw and processed artifact caching.

---

## 2. Environment Configuration

Copy the template file to create your environment configuration:
```bash
cp .env.example .env
```

Key environment variables:
- `APP_ENV`: Set to `production` on server, or `development` on workstation.
- `SEARXNG_URL`: Internal Docker URL for search (`http://searxng:8080`).
- `DATABASE_URL`: PostgreSQL connection string (`postgresql://admin:password123@postgres:5432/opendb`).
- `MINIO_ENDPOINT`: MinIO S3 endpoint (`minio:9000`).
- `OPENAI_BASE_URL` / `QWEN_API_KEY`: Remote LLM endpoint for business synthesis.

---

## 3. Standard 5-Step Development & Deployment Workflow

```text
Local Workstation
    │
    ▼
1. Edit code in backend/ or frontend/
    │
    ▼
2. Git Commit & Push:
   git add .
   git commit -m "fix(agent2): describe your change"
   git push origin main
    │
    ▼
Server (e.g. 57.128.27.215)
    │
    ▼
3. Pull Latest Changes:
   git pull origin main
    │
    ▼
4. Restart Containers:
   sudo docker compose restart backend celery_discovery
    │
    ▼
5. Verify Live Application:
   Check UI:  http://<SERVER_IP>:3001/
   Check API: http://<SERVER_IP>:8000/api/health/services
```

---

## 4. Useful Docker Commands

### View Logs in Real-Time
```bash
# Backend API logs
sudo docker compose logs -f --tail=100 backend

# Celery Worker logs
sudo docker compose logs -f --tail=100 celery_discovery

# Both combined
sudo docker compose logs -f --tail=100 backend celery_discovery
```

### Clean Docker Space & Duplicate Images
```bash
# Inspect disk usage
sudo docker system df

# Remove dangling build cache and stopped containers
sudo docker system prune -f

# Remove unused / duplicate images
sudo docker image prune -a
```

### Restart Entire OpenDB Stack
```bash
sudo docker compose restart
```

---

## 5. Verification & Health Endpoints

- **Frontend UI:** `http://<SERVER_IP>:3001/`
- **Backend Health Check:** `http://<SERVER_IP>:8000/api/health`
- **Services Health Matrix:** `http://<SERVER_IP>:8000/api/health/services`
- **Agent 2 Verification Queue:** `http://<SERVER_IP>:8000/api/agent2/status`
- **pgAdmin Database Console:** `http://<SERVER_IP>:5050/`
- **MinIO S3 Console:** `http://<SERVER_IP>:9001/`
