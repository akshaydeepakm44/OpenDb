# Dashboard Architecture & Live Data Flow

The OpenDB user interface is built as a Single Page Application (SPA) using **React** and **Vite** ([`frontend/src/App.jsx`](file:///e:/crawl/frontend/src/App.jsx)). It communicates with FastAPI REST endpoints to display real-time crawl logs, lead repositories, company dossiers, and key decision-maker cards.

---

## 1. Dashboard Data Flow Diagram

```mermaid
flowchart TD
    UI["React App Component (frontend/src/App.jsx)"]
    
    subgraph POLLING_TIMERS ["3-Second Interval Poller (Promise.allSettled)"]
        Poll_Status["GET /api/agent/status"]
        Poll_Health["GET /api/health/services"]
        Poll_Ops["GET /api/agent/operations"]
    end

    subgraph USER_FILTERS ["User Filter Triggers"]
        Fetch_Entities["GET /api/agent/entities?domain=...&country=..."]
        Fetch_Docs["GET /api/agent/documents?page=..."]
        Fetch_People["GET /api/agent/entities/{id}/people"]
    end

    subgraph DB_SERVERS ["Backend API & Database Tier"]
        API_Server["FastAPI Backend Server"]
        Postgres_DB[("PostgreSQL Database")]
        SQLite_Vault[("SQLite Master Vault")]
    end

    UI --> POLLING_TIMERS
    UI --> USER_FILTERS

    POLLING_TIMERS --> API_Server
    USER_FILTERS --> API_Server

    API_Server <--> Postgres_DB
    API_Server <--> SQLite_Vault

    API_Server -->> UI: Live Telemetry & Entity Dossiers
```

---

## 2. Dynamic UI Components

### 1. Operations Telemetry Bar
- **Data Source**: `/api/agent/operations` & `/api/health/services`
- **Displays**: Total companies discovered, active crawl count, database record count, Redis health, MinIO health, PostgreSQL health.

### 2. Live Crawl Activity Stream
- **Data Source**: `/api/agent/operations` (reading `crawl_activity_log` table)
- **Displays**: Real-time log of URLs being processed, stage (`SEARCH`, `CRAWL`, `EXTRACT`, `VERIFY`), HTTP status code, and execution duration.

### 3. Lead Repository Tab (Crawled vs Verified)
- **Crawled Docs View**: Displays crawled web pages, markdown paths, title, word count, and extracted keywords.
- **Verified Dossiers View**: Displays verified company profile cards, completeness score badge ($0-100$), technology stack tags, headquarters, and key decision makers.

### 4. Key People Discovered Cards Section
- **Data Source**: `/api/agent/entities/{id}/people`
- **Displays**:
  - Person Full Name & Executive Role (e.g. `John Smith — Founder & CEO`).
  - Verification Status Badge (`✓ Verified`, `✓ High Confidence`, `⏳ Pending Verification`).
  - Confidence Score ($\%$) & Source Type (`OFFICIAL_WEBSITE`, `PUBLIC_REFERENCE`).
  - Clickable `[View Source ↗]` hyperlink to origin web page.

---

## 3. Dashboard Semantics & Queue vs Crawled Clarification

To prevent confusion when monitoring metrics:
- **Discovered URLs**: Number of candidate links extracted from SearXNG search.
- **Crawled Pages**: Number of pages successfully fetched by Crawl4AI browser and saved to MinIO.
- **Verified Entities**: Number of company candidates that passed domain safety, canonical resolution, firmographic extraction, and multi-signal verification.
