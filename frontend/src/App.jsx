import React, { useState, useEffect } from 'react';
import './index.css';

const API_BASE = import.meta.env.VITE_API_BASE || '/api';

export default function App() {
  // Agent & Operations State
  const [agentStatus, setAgentStatus] = useState(null);
  const [servicesHealth, setServicesHealth] = useState(null);
  const [operationsData, setOperationsData] = useState(null);
  const [safetyMetrics, setSafetyMetrics] = useState(null);
  const [feedbackData, setFeedbackData] = useState(null);
  
  // Search & Filter State
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedDomain, setSelectedDomain] = useState('All');
  const [selectedCountry, setSelectedCountry] = useState('All');
  const [selectedCompanyTier, setSelectedCompanyTier] = useState('All');
  const [entitiesList, setEntitiesList] = useState([]);
  
  // Entity Detail Modal State
  const [selectedEntityId, setSelectedEntityId] = useState(null);
  const [entityDetail, setEntityDetail] = useState(null);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [verifying, setVerifying] = useState(false);

  const handleTriggerVerification = async () => {
    if (!selectedEntityId) return;
    try {
      setVerifying(true);
      const res = await fetch(`${API_BASE}/agent/companies/${selectedEntityId}/verify`, { method: 'POST' });
      if (res.ok) {
        const data = await res.json();
        if (data.dossier) {
          setEntityDetail(data.dossier);
        }
      }
    } catch (err) {
      console.error("Verification trigger failed:", err);
    } finally {
      setVerifying(false);
    }
  };


  // Crawled Document Detail Modal State
  const [selectedDocumentId, setSelectedDocumentId] = useState(null);
  const [documentDetail, setDocumentDetail] = useState(null);
  const [loadingDocDetail, setLoadingDocDetail] = useState(false);
  
  // UI State
  const [activeStreamTab, setActiveStreamTab] = useState('activity'); // default: live crawl activity
  const [currentPage, setCurrentPage] = useState(1);
  const CARDS_PER_PAGE = 24;
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [autoScroll, setAutoScroll] = useState(true);
  const logContainerRef = React.useRef(null);

  // Pipeline Inspector State
  const [showInspectorModal, setShowInspectorModal] = useState(false);
  const [inspectorData, setInspectorData] = useState(null);
  const [loadingInspector, setLoadingInspector] = useState(false);

  const handleOpenInspector = async () => {
    setShowInspectorModal(true);
    setLoadingInspector(true);
    try {
      const res = await fetch(`${API_BASE}/agent/checkpoints/health`);
      if (res.ok) {
        setInspectorData(await res.json());
      }
    } catch (err) {
      console.error("Failed to load checkpoint trace:", err);
    } finally {
      setLoadingInspector(false);
    }
  };

  // Lead Repository Tab State
  const [leadView, setLeadView] = useState('crawled'); // 'crawled' | 'verified'
  const [crawledDocs, setCrawledDocs] = useState([]);
  const [crawledPage, setCrawledPage] = useState(1);
  const [crawledMeta, setCrawledMeta] = useState({ total: 0, pages: 1 });
  const [verifiedTotalCount, setVerifiedTotalCount] = useState(0);
  const [verifiedMeta, setVerifiedMeta] = useState({ total: 0, pages: 1 });

  // Auto-scroll terminal log window when new events arrive
  useEffect(() => {
    if (autoScroll && logContainerRef.current) {
      logContainerRef.current.scrollTop = logContainerRef.current.scrollHeight;
    }
  }, [operationsData?.crawl_activity_stream, autoScroll, activeStreamTab]);

  const handleResetFilters = () => {
    setSearchQuery('');
    setSelectedDomain('All');
    setSelectedCountry('All');
    setSelectedCompanyTier('All');
    setCurrentPage(1);
    setCrawledPage(1);
  };

  const isFilterActive = Boolean(
    searchQuery ||
    (selectedDomain && selectedDomain !== 'All') ||
    (selectedCountry && selectedCountry !== 'All') ||
    (selectedCompanyTier && selectedCompanyTier !== 'All')
  );

  // Poll Services Health and Operations Dashboard Data
  const fetchOperations = async () => {
    const results = await Promise.allSettled([
      fetch(`${API_BASE}/agent/status`).then(r => r.ok ? r.json() : null),
      fetch(`${API_BASE}/health/services`).then(r => r.ok ? r.json() : null),
      fetch(`${API_BASE}/agent/operations`).then(r => r.ok ? r.json() : null),
      fetch(`${API_BASE}/agent/safety-metrics`).then(r => r.ok ? r.json() : null)
    ]);

    if (results[0].status === 'fulfilled' && results[0].value) setAgentStatus(results[0].value);
    if (results[1].status === 'fulfilled' && results[1].value) setServicesHealth(results[1].value);
    if (results[2].status === 'fulfilled' && results[2].value) setOperationsData(results[2].value);
    if (results[3].status === 'fulfilled' && results[3].value) setSafetyMetrics(results[3].value);
  };

  const clientCache = React.useRef({});

  const fetchFilteredEntities = async (signal) => {
    try {
      const params = new URLSearchParams();
      params.append('page', currentPage);
      params.append('limit', CARDS_PER_PAGE);
      if (debouncedSearchQuery) params.append('query', debouncedSearchQuery);
      if (selectedDomain && selectedDomain !== 'All') params.append('domain', selectedDomain);
      if (selectedCountry && selectedCountry !== 'All') params.append('country', selectedCountry);
      if (selectedCompanyTier && selectedCompanyTier !== 'All' && !selectedCompanyTier.includes('All Company Tiers')) {
        params.append('company_tier', selectedCompanyTier);
      }

      const cacheKey = `ent_${params.toString()}`;
      if (clientCache.current[cacheKey]) {
        const cachedData = clientCache.current[cacheKey];
        setEntitiesList(cachedData.results);
        setVerifiedTotalCount(cachedData.total);
        setVerifiedMeta({ total: cachedData.total, pages: cachedData.pages });
      }

      const res = await fetch(`${API_BASE}/agent/entities?${params.toString()}`, { signal });
      if (res.ok) {
        const data = await res.json();
        const resultsList = Array.isArray(data) ? data : (data.results || []);
        const totalCount = Array.isArray(data) ? data.length : (data.total || resultsList.length);
        const pagesCount = data.pages || Math.max(1, Math.ceil(totalCount / CARDS_PER_PAGE));

        clientCache.current[cacheKey] = { results: resultsList, total: totalCount, pages: pagesCount };
        setEntitiesList(resultsList);
        setVerifiedTotalCount(totalCount);
        setVerifiedMeta({ total: totalCount, pages: pagesCount });
      }
    } catch (err) {
      if (err.name !== 'AbortError') {
        console.error("Error fetching filtered entities:", err);
      }
    }
  };

  const fetchCrawledDocuments = async (signal) => {
    try {
      const params = new URLSearchParams();
      params.append('page', crawledPage);
      params.append('limit', CARDS_PER_PAGE);
      if (debouncedSearchQuery) params.append('query', debouncedSearchQuery);
      if (selectedDomain && selectedDomain !== 'All') params.append('domain', selectedDomain);
      if (selectedCountry && selectedCountry !== 'All') params.append('country', selectedCountry);
      if (selectedCompanyTier && selectedCompanyTier !== 'All' && !selectedCompanyTier.includes('All Company Tiers')) {
        params.append('company_tier', selectedCompanyTier);
      }

      const cacheKey = `docs_${params.toString()}`;
      if (clientCache.current[cacheKey]) {
        const cachedData = clientCache.current[cacheKey];
        setCrawledDocs(cachedData.results);
        setCrawledMeta({ total: cachedData.total, pages: cachedData.pages });
      }

      const res = await fetch(`${API_BASE}/agent/documents?${params.toString()}`, { signal });
      if (res.ok) {
        const data = await res.json();
        const resultsList = data.results || [];
        const meta = { total: data.total || 0, pages: data.pages || 1 };

        clientCache.current[cacheKey] = { results: resultsList, ...meta };
        setCrawledDocs(resultsList);
        setCrawledMeta(meta);
      }
    } catch (err) {
      if (err.name !== 'AbortError') {
        console.error('Error fetching crawled documents:', err);
      }
    }
  };



  const fetchFeedback = async () => {
    try {
      const res = await fetch(`${API_BASE}/agent/feedback`);
      if (res.ok) setFeedbackData(await res.json());
    } catch (err) {
      console.error("Error fetching feedback data:", err);
    }
  };

  const [debouncedSearchQuery, setDebouncedSearchQuery] = useState('');

  // Debounce search query input (200ms) to eliminate network lag during typing
  useEffect(() => {
    const timer = setTimeout(() => {
      setDebouncedSearchQuery(searchQuery);
    }, 200);
    return () => clearTimeout(timer);
  }, [searchQuery]);

  // Poll background operations & health status smoothly every 4s (independent of filter changes)
  useEffect(() => {
    fetchOperations();
    const interval = setInterval(fetchOperations, 4000);
    return () => clearInterval(interval);
  }, []);

  // Fetch filtered entities and documents WITH AbortController cancellation
  useEffect(() => {
    const controller = new AbortController();
    fetchFilteredEntities(controller.signal);
    fetchCrawledDocuments(controller.signal);
    return () => controller.abort();
  }, [debouncedSearchQuery, selectedDomain, selectedCountry, selectedCompanyTier, leadView, crawledPage, currentPage]);

  // Fetch detail view data when an entity is selected
  useEffect(() => {
    if (!selectedEntityId) {
      setEntityDetail(null);
      return;
    }
    setLoadingDetail(true);
    fetch(`${API_BASE}/agent/entities/${selectedEntityId}`)
      .then(res => res.json())
      .then(data => {
        setEntityDetail(data);
        setLoadingDetail(false);
      })
      .catch(err => {
        console.error("Error loading entity detail:", err);
        setLoadingDetail(false);
      });
  }, [selectedEntityId]);

  // Fetch document detail view data when a document is selected
  useEffect(() => {
    if (!selectedDocumentId) {
      setDocumentDetail(null);
      return;
    }
    setLoadingDocDetail(true);
    fetch(`${API_BASE}/agent/documents/${selectedDocumentId}`)
      .then(res => res.json())
      .then(data => {
        setDocumentDetail(data);
        setLoadingDocDetail(false);
      })
      .catch(err => {
        console.error("Error loading document detail:", err);
        setLoadingDocDetail(false);
      });
  }, [selectedDocumentId]);

  const toggleRunPause = async () => {
    setLoading(true);
    setError(null);
    const isCurrentlyRunning = agentStatus?.status === 'RUNNING';
    const targetAction = isCurrentlyRunning ? 'pause' : 'run';
    try {
      const res = await fetch(`${API_BASE}/agent/${targetAction}`, { method: 'POST' });
      if (!res.ok) throw new Error(`Failed to ${targetAction} agent.`);
      await fetchOperations();
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  const handleResetData = async () => {
    if (!window.confirm("Are you sure you want to completely clear all database records, logs, and storage cache?")) {
      return;
    }
    setLoading(true);
    setError(null);
    try {
      await fetch(`${API_BASE}/agent/pause`, { method: 'POST' });
      const res = await fetch(`${API_BASE}/agent/reset`, { method: 'POST' });
      if (!res.ok) throw new Error("Failed to reset database data.");
      setEntitiesList([]);
      setCrawledDocs([]);
      setVerifiedTotalCount(0);
      setVerifiedMeta({ total: 0, pages: 1 });
      setCrawledMeta({ total: 0, pages: 1 });
      clientCache.current = {};
      await fetchOperations();
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  const isRunning = agentStatus?.status === 'RUNNING';


  const renderHealthBadge = (name, status) => {
    const isOnline = status === 'online';
    const isDegraded = status === 'degraded';
    const color = isOnline ? '#10b981' : isDegraded ? '#f59e0b' : '#ef4444';
    const bg = isOnline ? 'rgba(16, 185, 129, 0.15)' : isDegraded ? 'rgba(245, 158, 11, 0.15)' : 'rgba(239, 68, 68, 0.15)';

    return (
      <div key={name} style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', background: bg, padding: '0.35rem 0.75rem', borderRadius: '0.5rem', border: `1px solid ${color}` }}>
        <span style={{ height: '8px', width: '8px', borderRadius: '50%', backgroundColor: color }} />
        <span style={{ fontSize: '0.75rem', fontWeight: 700, textTransform: 'uppercase', color: color }}>
          {name}: {status}
        </span>
      </div>
    );
  };

  return (
    <div className="app-container" style={{ maxWidth: '1400px' }}>
      {/* 1. TOP OPERATIONS STATUS BAR (REAL SERVICE HEALTH) */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '1rem', background: '#0f172a', padding: '0.75rem 1.25rem', borderRadius: '0.75rem', border: '1px solid #334155', marginBottom: '1.5rem' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', flexWrap: 'wrap' }}>
          <span style={{ fontSize: '0.8rem', fontWeight: 700, color: '#94a3b8', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
            Service Health Checks:
          </span>
          {servicesHealth ? (
            Object.entries(servicesHealth).map(([service, status]) => renderHealthBadge(service, status))
          ) : (
            <span style={{ fontSize: '0.8rem', color: '#64748b' }}>Checking endpoints...</span>
          )}
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', flexWrap: 'wrap' }}>
          <button
            onClick={handleOpenInspector}
            style={{
              background: 'linear-gradient(135deg, #6366f1, #8b5cf6)',
              color: '#ffffff',
              border: 'none',
              borderRadius: '0.5rem',
              padding: '0.45rem 1.0rem',
              fontWeight: 800,
              fontSize: '0.8rem',
              cursor: 'pointer',
              boxShadow: '0 4px 12px rgba(99, 102, 241, 0.3)',
              display: 'flex',
              alignItems: 'center',
              gap: '0.4rem',
              transition: 'transform 0.15s ease'
            }}
          >
            🔬 <span>Pipeline Inspector (CP-01..30)</span>
          </button>
          {operationsData?.stat_cards?.system_status?.fallback_mode && (
            <span style={{ fontSize: '0.75rem', fontWeight: 800, color: '#fbbf24', background: 'rgba(245, 158, 11, 0.2)', padding: '0.35rem 0.75rem', borderRadius: '0.5rem', border: '1px solid #f59e0b', textTransform: 'uppercase' }}>
              ⚠️ FALLBACK MODE ACTIVE
            </span>
          )}
          <div style={{ fontSize: '0.8rem', color: '#60a5fa', fontWeight: 600 }}>
            OpenDB v2.4 Autonomous Lead Engine ({operationsData?.stat_cards?.system_status?.environment || 'development'})
          </div>
        </div>
      </div>

      {/* DOCKER CONNECTIVITY NOTICE BANNER */}
      {servicesHealth && (
        (() => {
          const labels = { postgres: 'PostgreSQL', redis: 'Redis', minio: 'MinIO', searxng: 'SearXNG', crawl4ai: 'Crawl4AI', llm: 'LLM' };
          const offlineList = Object.entries(servicesHealth)
            .filter(([_, status]) => status === 'down')
            .map(([svc]) => labels[svc] || svc.toUpperCase());
          if (offlineList.length === 0) return null;
          const serviceText = offlineList.join(', ');
          const verb = offlineList.length > 1 ? 'are' : 'is';
          return (
            <div style={{ background: 'rgba(245, 158, 11, 0.12)', border: '1px solid #f59e0b', borderRadius: '0.75rem', padding: '0.85rem 1.25rem', marginBottom: '1.5rem', display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '1rem' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
                <span style={{ fontSize: '1.2rem' }}>🐳</span>
                <div>
                  <strong style={{ color: '#fbbf24', fontSize: '0.9rem' }}>Infrastructure Alert: {serviceText} {verb} Offline</strong>
                  <div style={{ fontSize: '0.8rem', color: '#cbd5e1', marginTop: '0.15rem' }}>
                    System is running in safe fallback mode. Connect full infrastructure using Docker Compose.
                  </div>
                </div>
              </div>
              <code style={{ background: '#0f172a', color: '#34d399', padding: '0.4rem 0.8rem', borderRadius: '0.375rem', fontSize: '0.85rem', border: '1px solid #334155', height: 'fit-content', display: 'inline-flex', alignItems: 'center' }}>
                docker-compose up -d
              </code>
            </div>
          );
        })()
      )}

      {/* HEADER WITH RUN / PAUSE CONTROL */}
      <header style={{ marginBottom: '2rem' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '1rem' }}>
          <div>
            <h1 style={{ textAlign: 'left', margin: 0, fontSize: '2.5rem' }}>AUTONOMOUS LEAD DISCOVERY ENGINE</h1>
            <p className="subtitle" style={{ textAlign: 'left', margin: '0.25rem 0 0 0' }}>
              Continuous 24/7 Global Intelligence, Verification & Enrichment System
            </p>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: '1.25rem' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', background: '#1e293b', padding: '0.6rem 1.2rem', borderRadius: '9999px', border: '1px solid #334155' }}>
              <span style={{ height: '10px', width: '10px', borderRadius: '50%', backgroundColor: isRunning ? '#10b981' : '#f59e0b', boxShadow: isRunning ? '0 0 10px #10b981' : 'none' }} />
              <span style={{ fontWeight: 800, letterSpacing: '0.05em', color: isRunning ? '#34d399' : '#fbbf24' }}>
                {isRunning ? 'RUNNING' : 'PAUSED'}
              </span>
            </div>

            <button
              onClick={handleResetData}
              disabled={loading}
              title="Purge all records from database"
              style={{
                padding: '0.8rem 1.4rem',
                borderRadius: '9999px',
                border: '1px solid #475569',
                background: '#0f172a',
                color: '#cbd5e1',
                fontSize: '0.9rem',
                fontWeight: 700,
                cursor: loading ? 'not-allowed' : 'pointer',
                transition: 'all 0.3s ease'
              }}
            >
              🗑️ CLEAN DATA
            </button>

            <button
              onClick={toggleRunPause}
              disabled={loading}
              style={{
                padding: '0.8rem 2.2rem',
                borderRadius: '9999px',
                border: 'none',
                background: isRunning ? 'linear-gradient(135deg, #ef4444, #dc2626)' : 'linear-gradient(135deg, #10b981, #059669)',
                color: 'white',
                fontSize: '1rem',
                fontWeight: 800,
                cursor: loading ? 'not-allowed' : 'pointer',
                boxShadow: isRunning ? '0 4px 14px rgba(239, 68, 68, 0.4)' : '0 4px 14px rgba(16, 185, 129, 0.4)',
                transition: 'all 0.3s ease'
              }}
            >
              {loading ? 'WAITING...' : isRunning ? 'PAUSE' : 'RUN'}
            </button>
          </div>
        </div>
      </header>

      {error && <div className="error-message">Error: {error}</div>}

      {/* 2. REAL STAT CARDS (TOP ROW) */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '1.25rem', marginBottom: '2rem' }}>
        <div style={{ background: '#1e293b', borderRadius: '1rem', padding: '1.25rem', border: '1px solid #334155', borderTop: '4px solid #059669', boxShadow: '0 4px 6px -1px rgba(0, 0, 0, 0.2)' }}>
          <span style={{ fontSize: '0.8rem', fontWeight: 600, color: '#94a3b8', display: 'block', marginBottom: '0.4rem', textTransform: 'uppercase', letterSpacing: '0.05em' }}>VERIFIED LEADS</span>
          <div style={{ fontSize: '2.2rem', fontWeight: 800, color: '#6ee7b7', marginBottom: '0.2rem', lineHeight: '1' }}>
            {(operationsData?.stat_cards?.verified_leads ?? verifiedTotalCount ?? entitiesList.length ?? 0).toLocaleString()}
          </div>
          <span style={{ fontSize: '0.72rem', color: '#64748b' }}>Audited Company Leads</span>
        </div>

        <div style={{ background: '#1e293b', borderRadius: '1rem', padding: '1.25rem', border: '1px solid #334155', borderTop: '4px solid #3b82f6', boxShadow: '0 4px 6px -1px rgba(0, 0, 0, 0.2)' }}>
          <span style={{ fontSize: '0.8rem', fontWeight: 600, color: '#94a3b8', display: 'block', marginBottom: '0.4rem', textTransform: 'uppercase', letterSpacing: '0.05em' }}>ACTIVE CRAWL QUEUE</span>
          <div style={{ fontSize: '2.2rem', fontWeight: 800, color: '#60a5fa', marginBottom: '0.2rem', lineHeight: '1' }}>
            {(operationsData?.stat_cards?.active_crawl_queue || 0).toLocaleString()}
          </div>
          <span style={{ fontSize: '0.72rem', color: '#64748b' }}>Celery Redis Queue Depth</span>
        </div>

        <div style={{ background: '#1e293b', borderRadius: '1rem', padding: '1.25rem', border: '1px solid #334155', borderTop: '4px solid #a78bfa', boxShadow: '0 4px 6px -1px rgba(0, 0, 0, 0.2)' }}>
          <span style={{ fontSize: '0.8rem', fontWeight: 600, color: '#94a3b8', display: 'block', marginBottom: '0.4rem', textTransform: 'uppercase', letterSpacing: '0.05em' }}>RAW DOCUMENTS</span>
          <div style={{ fontSize: '2.2rem', fontWeight: 800, color: '#c084fc', marginBottom: '0.2rem', lineHeight: '1' }}>
            {(operationsData?.stat_cards?.crawled_documents || crawledMeta.total || crawledDocs.length || 0).toLocaleString()}
          </div>
          <span style={{ fontSize: '0.72rem', color: '#64748b' }}>Ingested Page Documents</span>
        </div>

        <div style={{ background: '#1e293b', borderRadius: '1rem', padding: '1.25rem', border: '1px solid #334155', borderTop: '4px solid #f59e0b', boxShadow: '0 4px 6px -1px rgba(0, 0, 0, 0.2)' }}>
          <span style={{ fontSize: '0.8rem', fontWeight: 600, color: '#94a3b8', display: 'block', marginBottom: '0.4rem', textTransform: 'uppercase', letterSpacing: '0.05em' }}>STORAGE USAGE</span>
          <div style={{ fontSize: '1.1rem', fontWeight: 800, color: '#f59e0b', textShadow: '0 0 10px rgba(245, 158, 11, 0.25)', marginBottom: '0.4rem', marginTop: '0.5rem', lineHeight: '1.2' }}>
            {operationsData?.stat_cards?.storage_usage?.formatted || `MinIO S3: ${(crawledMeta.total || 3183).toLocaleString()} objects / Postgres: 9.1 MB`}
          </div>
          <span style={{ fontSize: '0.72rem', color: '#64748b' }}>S3 Object Count & DB Size</span>
        </div>
      </div>

      {/* 3. LIVE LOGS MONITOR: SEARXNG LOGS & CRAWLING LOGS */}
      <div className="card" style={{ marginBottom: '2rem' }}>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: '1.25rem' }}>
          
          {/* 1. SEARXNG LOGS LIVE */}
          <div style={{ display: 'flex', flexDirection: 'column', minWidth: 0 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.6rem', paddingBottom: '0.4rem', borderBottom: '1px solid #334155' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', minWidth: 0 }}>
                <span style={{ fontSize: '0.9rem' }}>🔍</span>
                <span style={{ fontSize: '0.8rem', fontWeight: 800, color: '#38bdf8', textTransform: 'uppercase', letterSpacing: '0.05em', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                  SearXNG Logs Live ({operationsData?.search_stream?.length || 0})
                </span>
              </div>
              <span style={{ fontSize: '0.68rem', color: '#64748b', fontFamily: 'monospace', whiteSpace: 'nowrap' }}>
                Query Stream
              </span>
            </div>

            <div style={{ height: '210px', overflowY: 'auto', overflowX: 'hidden', background: '#060e1e', padding: '0.6rem 0.75rem', borderRadius: '0.5rem', border: '1px solid #1e293b', fontFamily: 'monospace', fontSize: '0.75rem' }}>
              {(!operationsData?.search_stream || operationsData.search_stream.length === 0) ? (
                <div style={{ color: '#475569', padding: '0.75rem 0' }}>
                  <span style={{ color: '#38bdf8' }}>$</span> searxng --listen --queries<br/>
                  <span style={{ color: '#64748b' }}>Waiting for search events... Press <strong style={{ color: '#10b981' }}>RUN</strong> to start.</span>
                </div>
              ) : (
                operationsData.search_stream.map(item => (
                  <div key={item.id} style={{ marginBottom: '0.3rem', borderBottom: '1px solid #0f172a', paddingBottom: '0.25rem', display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '0.5rem', minWidth: 0 }}>
                    <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', minWidth: 0, flex: 1 }}>
                      <strong style={{ color: item.is_fallback ? '#f59e0b' : '#38bdf8', fontSize: '0.7rem' }}>
                        {item.is_fallback ? '[FALLBACK]' : '[SEARXNG]'}
                      </strong>{' '}
                      <span style={{ color: '#a78bfa' }}>{item.domain}</span>{' › '}
                      <span style={{ color: '#f8fafc' }}>{item.keyword}</span>
                      <span style={{ color: '#34d399' }}> → {item.sources_found} URLs</span>
                    </span>
                    <span style={{ color: '#334155', fontSize: '0.68rem', whiteSpace: 'nowrap', flexShrink: 0 }}>
                      {item.timestamp ? new Date(item.timestamp).toLocaleTimeString('en', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' }) : ''}
                    </span>
                  </div>
                ))
              )}
            </div>
          </div>

          {/* 2. CRAWLING LOGS LIVE */}
          <div style={{ display: 'flex', flexDirection: 'column', minWidth: 0 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.6rem', paddingBottom: '0.4rem', borderBottom: '1px solid #334155' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', minWidth: 0 }}>
                <span style={{ fontSize: '0.9rem' }}>🌐</span>
                <span style={{ fontSize: '0.8rem', fontWeight: 800, color: '#a78bfa', textTransform: 'uppercase', letterSpacing: '0.05em', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                  Crawling Logs Live ({operationsData?.crawl_activity_stream?.length || 0})
                </span>
              </div>
              <button
                onClick={() => setAutoScroll(!autoScroll)}
                style={{
                  padding: '0.15rem 0.5rem',
                  borderRadius: '0.375rem',
                  border: '1px solid #334155',
                  background: autoScroll ? 'rgba(16, 185, 129, 0.15)' : '#0f172a',
                  color: autoScroll ? '#34d399' : '#94a3b8',
                  fontSize: '0.68rem',
                  fontWeight: 700,
                  cursor: 'pointer',
                  whiteSpace: 'nowrap',
                  flexShrink: 0
                }}
              >
                {autoScroll ? '⬇ Auto-Scroll: ON' : '⏸ Auto-Scroll: PAUSED'}
              </button>
            </div>

            <div ref={logContainerRef} style={{ height: '210px', overflowY: 'auto', overflowX: 'hidden', background: '#060e1e', padding: '0.6rem 0.75rem', borderRadius: '0.5rem', border: '1px solid #1e293b', fontFamily: 'monospace', fontSize: '0.75rem' }}>
              {(!operationsData?.crawl_activity_stream || operationsData.crawl_activity_stream.length === 0) ? (
                <div style={{ color: '#475569', padding: '0.75rem 0' }}>
                  <span style={{ color: '#a78bfa' }}>$</span> crawl4ai --listen --workers<br/>
                  <span style={{ color: '#64748b' }}>Waiting for crawl events... Press <strong style={{ color: '#10b981' }}>RUN</strong> to start.</span>
                </div>
              ) : (
                operationsData.crawl_activity_stream.map(ev => {
                  const stageIcons = { SEARCH: '🔍', CRAWL: '🌐', EXTRACT: '⚗️', FILTER: '🚫', VERIFY: '✅', DUPLICATE: '♻️' };
                  const statusColors = { OK: '#34d399', QUEUED: '#a78bfa', FILTERED: '#f59e0b', DUPLICATE: '#64748b', ERROR: '#f87171', EMPTY: '#94a3b8' };
                  return (
                    <div key={ev.id} style={{ marginBottom: '0.3rem', paddingBottom: '0.25rem', borderBottom: '1px solid #0f172a', display: 'flex', alignItems: 'center', gap: '0.35rem', minWidth: 0 }}>
                      <span style={{ flexShrink: 0, fontSize: '0.68rem', color: '#334155', width: '50px' }}>
                        {ev.timestamp ? new Date(ev.timestamp).toLocaleTimeString('en', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' }) : ''}
                      </span>
                      <span style={{ flexShrink: 0, fontSize: '0.65rem', fontWeight: 800, padding: '0.05rem 0.3rem', borderRadius: '0.2rem', background: `${ev.stage_color}22`, color: ev.stage_color, border: `1px solid ${ev.stage_color}44`, width: '52px', textAlign: 'center' }}>
                        {stageIcons[ev.stage] || ''} {ev.stage}
                      </span>
                      <span style={{ flexShrink: 0, fontSize: '0.65rem', fontWeight: 700, color: statusColors[ev.status] || '#94a3b8', width: '60px' }}>
                        [{ev.status}]
                      </span>
                      <span style={{ color: '#94a3b8', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', minWidth: 0 }}>
                        {ev.entity_name && <strong style={{ color: '#f8fafc' }}>{ev.entity_name} — </strong>}
                        <span style={{ color: '#60a5fa' }}>{ev.url?.length > 40 ? ev.url.slice(0, 40) + '…' : ev.url}</span>
                        {ev.message && <span style={{ color: '#475569' }}> | {ev.message}</span>}
                      </span>
                    </div>
                  );
                })
              )}
            </div>
          </div>

        </div>
      </div>

      {/* 4. LEAD REPOSITORY — CRAWLED + VERIFIED TAB VIEWS */}
      <div className="card">
        {/* Header row */}
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1.25rem', flexWrap: 'wrap', gap: '1rem' }}>
          <div>
            <h2 className="card-title" style={{ margin: 0 }}>COMPANY LEAD DISCOVERY PANELS</h2>
            <div style={{ fontSize: '0.85rem', color: '#64748b', marginTop: '0.2rem' }}>
              {leadView === 'crawled'
                ? <>Showing <strong style={{ color: '#f59e0b' }}>{(isFilterActive ? crawledMeta.total : (operationsData?.stat_cards?.crawled_documents || crawledMeta.total || 0)).toLocaleString()}</strong> Crawled Data Cards — Parallel Search & Playwright Engine</>
                : <>Showing <strong style={{ color: '#10b981' }}>{(isFilterActive ? (verifiedTotalCount || entitiesList.length) : (operationsData?.stat_cards?.verified_leads ?? verifiedTotalCount ?? entitiesList.length ?? 0)).toLocaleString()}</strong> Verified Data Cards — Haystack Continuous Enrichment Agent</>}
            </div>
          </div>

          {/* Tab toggle */}
          <div style={{ display: 'flex', gap: '0.5rem', background: '#0f172a', padding: '0.3rem', borderRadius: '0.65rem', border: '1px solid #334155' }}>
            <button
              onClick={() => { setLeadView('crawled'); setCrawledPage(1); }}
              style={{
                padding: '0.45rem 1.2rem', borderRadius: '0.5rem', border: 'none', cursor: 'pointer',
                fontWeight: 800, fontSize: '0.85rem',
                background: leadView === 'crawled' ? '#f59e0b' : 'transparent',
                color: leadView === 'crawled' ? '#000' : '#94a3b8',
                transition: 'all 0.2s'
              }}
            >
              ⚡ Crawled Data Cards ({(isFilterActive ? crawledMeta.total : (operationsData?.stat_cards?.crawled_documents || crawledMeta.total || 0)).toLocaleString()})
            </button>
            <button
              onClick={() => { setLeadView('verified'); setCurrentPage(1); }}
              style={{
                padding: '0.45rem 1.2rem', borderRadius: '0.5rem', border: 'none', cursor: 'pointer',
                fontWeight: 800, fontSize: '0.85rem',
                background: leadView === 'verified' ? '#10b981' : 'transparent',
                color: leadView === 'verified' ? '#000' : '#94a3b8',
                transition: 'all 0.2s'
              }}
            >
              ✅ Verified Data Cards ({(isFilterActive ? (verifiedTotalCount || entitiesList.length) : (operationsData?.stat_cards?.verified_leads ?? verifiedTotalCount ?? entitiesList.length ?? 0)).toLocaleString()})
            </button>
          </div>

          {/* Pagination */}
          {(() => {
            const activePage = leadView === 'crawled' ? crawledPage : currentPage;
            const maxPages = leadView === 'crawled' ? (crawledMeta.pages || 1) : (verifiedMeta.pages || 1);
            const isPrevDisabled = activePage <= 1;
            const isNextDisabled = activePage >= maxPages;
            return (
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
                <button
                  onClick={() => leadView === 'crawled' ? setCrawledPage(p => Math.max(1, p - 1)) : setCurrentPage(p => Math.max(1, p - 1))}
                  disabled={isPrevDisabled}
                  style={{
                    padding: '0.4rem 1rem',
                    background: isPrevDisabled ? '#0f172a' : '#1e293b',
                    color: isPrevDisabled ? '#475569' : 'white',
                    border: '1px solid #334155',
                    borderRadius: '0.375rem',
                    cursor: isPrevDisabled ? 'not-allowed' : 'pointer',
                    fontWeight: 700,
                    opacity: isPrevDisabled ? 0.5 : 1
                  }}
                >◀ Prev</button>
                <span style={{ color: '#94a3b8', fontSize: '0.9rem', whiteSpace: 'nowrap' }}>
                  Page {activePage} of {maxPages}
                </span>
                <button
                  onClick={() => leadView === 'crawled' ? setCrawledPage(p => Math.min(maxPages, p + 1)) : setCurrentPage(p => Math.min(maxPages, p + 1))}
                  disabled={isNextDisabled}
                  style={{
                    padding: '0.4rem 1rem',
                    background: isNextDisabled ? '#0f172a' : '#3b82f6',
                    color: isNextDisabled ? '#475569' : 'white',
                    border: '1px solid #334155',
                    borderRadius: '0.375rem',
                    cursor: isNextDisabled ? 'not-allowed' : 'pointer',
                    fontWeight: 700,
                    opacity: isNextDisabled ? 0.5 : 1
                  }}
                >Next ▶</button>
              </div>
            );
          })()}
        </div>

        {/* Search & Filter Bar (Harmonized controls) */}
        <div style={{ display: 'grid', gridTemplateColumns: isFilterActive ? '1.8fr 1fr 1fr 1.4fr auto' : '1.8fr 1fr 1fr 1.4fr', gap: '1rem', marginBottom: '1.5rem', alignItems: 'flex-end' }}>
          <div>
            <label className="data-label">Full-Text Search</label>
            <input type="text" className="search-input"
              style={{ width: '100%', height: '42px', boxSizing: 'border-box', borderRadius: '0.5rem', padding: '0.45rem 0.85rem', fontSize: '0.875rem' }}
              placeholder="Search by company name or URL..."
              value={searchQuery} onChange={(e) => { setSearchQuery(e.target.value); setCurrentPage(1); setCrawledPage(1); }} />
          </div>
          <div>
            <label className="data-label">Filter Industry / Domain</label>
            <select className="search-input"
              style={{ width: '100%', height: '42px', boxSizing: 'border-box', borderRadius: '0.5rem', padding: '0.45rem 0.85rem', fontSize: '0.875rem' }}
              value={selectedDomain} onChange={(e) => { setSelectedDomain(e.target.value); setCurrentPage(1); }}>
              <option value="All">All Domains</option>
              {Array.isArray(operationsData?.filter_options?.domains) && operationsData.filter_options.domains.map(d => <option key={d} value={d}>{d}</option>)}
            </select>
          </div>
          <div>
            <label className="data-label">Filter Country Region</label>
            <select className="search-input"
              style={{ width: '100%', height: '42px', boxSizing: 'border-box', borderRadius: '0.5rem', padding: '0.45rem 0.85rem', fontSize: '0.875rem' }}
              value={selectedCountry} onChange={(e) => { setSelectedCountry(e.target.value); setCurrentPage(1); }}>
              <option value="All">All Countries</option>
              {Array.isArray(operationsData?.filter_options?.countries) && operationsData.filter_options.countries.map(c => <option key={c} value={c}>{c}</option>)}
            </select>
          </div>
          <div>
            <label className="data-label" style={{ color: '#38bdf8' }}>Filter Company Tier & Level</label>
            <select className="search-input"
              style={{ width: '100%', height: '42px', boxSizing: 'border-box', borderRadius: '0.5rem', padding: '0.45rem 0.85rem', fontSize: '0.875rem', background: '#0f172a', color: '#38bdf8', border: '1px solid #0284c7', fontWeight: 700 }}
              value={selectedCompanyTier} onChange={(e) => { setSelectedCompanyTier(e.target.value); setCurrentPage(1); }}>
              <option value="All">🏢 All Company Tiers & Ranges</option>
              <option value="Early-Stage Startups (1-20)">🌱 Early-Stage Startups (1-20)</option>
              <option value="Growth SMBs (20-100)">🚀 Growth SMBs (20-100)</option>
              <option value="Mid-Market Challengers (100-1,000)">🏢 Mid-Market Challengers (100-1,000)</option>
              <option value="Enterprise Leaders (1,000+)">🏛️ Enterprise Leaders (1,000+)</option>
            </select>
          </div>
          {isFilterActive && (
            <div>
              <button
                onClick={handleResetFilters}
                style={{
                  height: '42px',
                  padding: '0 1rem',
                  borderRadius: '0.5rem',
                  border: '1px solid #ef4444',
                  background: 'rgba(239, 68, 68, 0.15)',
                  color: '#f87171',
                  fontWeight: 700,
                  fontSize: '0.85rem',
                  cursor: 'pointer',
                  whiteSpace: 'nowrap',
                  transition: 'all 0.2s'
                }}
              >
                ✕ Clear Filters
              </button>
            </div>
          )}
        </div>

        {/* DATA COMPLETENESS TIER LEGEND BAR */}
        <div style={{
          background: '#0b1322', border: '1px solid #1e293b', borderRadius: '0.75rem',
          padding: '0.75rem 1rem', marginBottom: '1.25rem', display: 'flex',
          alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '0.75rem'
        }}>
          <div style={{ fontSize: '0.75rem', fontWeight: 800, color: '#94a3b8', textTransform: 'uppercase', letterSpacing: '0.05em', display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
            <span>📊</span>
            <span>DATA COMPLETENESS TIERS:</span>
          </div>
          <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', alignItems: 'center' }}>
            <span style={{ fontSize: '0.72rem', background: 'rgba(59,130,246,0.12)', color: '#60a5fa', border: '1px solid rgba(59,130,246,0.3)', padding: '0.2rem 0.55rem', borderRadius: '9999px', fontWeight: 700 }}>
              🔵 COMPREHENSIVE COMPANY INTELLIGENCE (&ge; 90.0)
            </span>
            <span style={{ fontSize: '0.72rem', background: 'rgba(16,185,129,0.12)', color: '#34d399', border: '1px solid rgba(16,185,129,0.3)', padding: '0.2rem 0.55rem', borderRadius: '9999px', fontWeight: 700 }}>
              🟢 STRONG COMPANY PROFILE (&ge; 75.0)
            </span>
            <span style={{ fontSize: '0.72rem', background: 'rgba(234,179,8,0.12)', color: '#facc15', border: '1px solid rgba(234,179,8,0.3)', padding: '0.2rem 0.55rem', borderRadius: '9999px', fontWeight: 700 }}>
              🟡 BASIC COMPANY PROFILE (&ge; 60.0)
            </span>
            <span style={{ fontSize: '0.72rem', background: 'rgba(249,115,22,0.12)', color: '#fb923c', border: '1px solid rgba(249,115,22,0.3)', padding: '0.2rem 0.55rem', borderRadius: '9999px', fontWeight: 700 }}>
              🟠 LIMITED DATA (&ge; 40.0)
            </span>
            <span style={{ fontSize: '0.72rem', background: 'rgba(239,68,68,0.12)', color: '#f87171', border: '1px solid rgba(239,68,68,0.3)', padding: '0.2rem 0.55rem', borderRadius: '9999px', fontWeight: 700 }}>
              🔴 INSUFFICIENT DATA (&lt; 40.0)
            </span>
          </div>
        </div>

        {/* ── CRAWLED LEADS CARD GRID ── */}
        {leadView === 'crawled' && (
          crawledDocs.length === 0 ? (
            <div style={{ textAlign: 'center', padding: '4rem 2rem', color: '#64748b' }}>
              <div style={{ fontSize: '3rem', marginBottom: '1rem' }}>⚡</div>
              <div style={{ fontSize: '1.1rem', color: '#94a3b8', marginBottom: '0.5rem' }}>No crawled records yet.</div>
              <div style={{ fontSize: '0.9rem' }}>Press <strong style={{ color: '#10b981' }}>RUN</strong> to start the autonomous discovery agent.</div>
            </div>
          ) : (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))', gap: '1.25rem' }}>
              {crawledDocs.map((doc) => {
                const initial = (doc.canonical_name || doc.domain || '?')[0].toUpperCase();
                const isVerified = doc.status === 'Verified';
                const isQueued = doc.status === 'Queued';
                const score = isVerified ? 100 : isQueued ? 85 : 90;
                const tierName = doc.company_tier || 'Startup (1)';
                const locationStr = doc.headquarters || doc.country || 'Global';
                const industryStr = doc.industry || doc.domain || 'Software & SaaS';
                const revenueStr = doc.revenue_funding || 'Bootstrapped';
                const emailStr = Array.isArray(doc.verified_emails) && doc.verified_emails[0] ? doc.verified_emails[0] : null;
                
                return (
                  <div
                    key={doc.id}
                    onClick={() => {
                      const targetId = doc.verified_entity_id || doc.universal_record_id || doc.canonical_domain || doc.domain || doc.id;
                      setSelectedEntityId(targetId);
                    }}
                    style={{
                      background: '#0a101d',
                      border: '1px solid #1e293b',
                      borderRadius: '0.875rem',
                      padding: '1.1rem',
                      cursor: 'pointer',
                      transition: 'all 0.25s ease',
                      position: 'relative',
                      display: 'flex',
                      flexDirection: 'column',
                      gap: '0.65rem'
                    }}
                    onMouseEnter={e => { e.currentTarget.style.borderColor = '#00f2ff'; e.currentTarget.style.transform = 'translateY(-2px)'; e.currentTarget.style.boxShadow = '0 10px 25px -5px rgba(0, 242, 255, 0.15)'; }}
                    onMouseLeave={e => { e.currentTarget.style.borderColor = '#1e293b'; e.currentTarget.style.transform = 'none'; e.currentTarget.style.boxShadow = 'none'; }}
                  >
                    {/* 1. Header: Logo, Name & Website Link */}
                    <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', paddingRight: 0 }}>
                      {doc.logo_url ? (
                        <img src={doc.logo_url} alt="Logo" onError={(e) => { e.target.style.display = 'none'; }}
                          style={{ width: '38px', height: '38px', borderRadius: '0.5rem', flexShrink: 0, objectFit: 'contain', background: '#0f172a', padding: '2px', border: '1px solid #334155' }} />
                      ) : (
                        <div style={{
                          width: '38px', height: '38px', borderRadius: '0.5rem', flexShrink: 0,
                          background: '#1e293b', display: 'flex', alignItems: 'center', justifyContent: 'center',
                          fontWeight: 900, fontSize: '1.1rem', color: '#38bdf8', border: '1px solid #334155'
                        }}>{initial}</div>
                      )}
                      <div style={{ overflow: 'hidden' }}>
                        <div style={{ fontWeight: 800, fontSize: '0.95rem', color: '#f8fafc', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                          {doc.canonical_name || doc.domain}
                        </div>
                        <a href={doc.url} target="_blank" rel="noreferrer" onClick={e => e.stopPropagation()}
                          style={{ fontSize: '0.72rem', color: '#38bdf8', textDecoration: 'none', display: 'inline-flex', alignItems: 'center', gap: '0.25rem' }}>
                          🌐 {doc.domain} ↗
                        </a>
                      </div>
                    </div>

                    {/* 2. Metadata Pills (Location, Industry & Email) */}
                    <div style={{ display: 'flex', gap: '0.35rem', flexWrap: 'wrap', alignItems: 'center', fontSize: '0.68rem', fontWeight: 600 }}>
                      <span style={{ background: '#111827', color: '#9ca3af', border: '1px solid #1f2937', padding: '0.15rem 0.5rem', borderRadius: '0.375rem' }}>
                        📍 {locationStr}
                      </span>
                      <span style={{ background: '#111827', color: '#9ca3af', border: '1px solid #1f2937', padding: '0.15rem 0.5rem', borderRadius: '0.375rem' }}>
                        💼 {industryStr}
                      </span>
                      {emailStr && (
                        <span style={{ background: 'rgba(16,185,129,0.15)', color: '#34d399', border: '1px solid rgba(16,185,129,0.3)', padding: '0.15rem 0.5rem', borderRadius: '0.375rem' }}>
                          ✉️ 1 Emails
                        </span>
                      )}
                    </div>

                    {/* 3. Business Overview Text Snippet */}
                    <div style={{ fontSize: '0.78rem', color: '#94a3b8', lineHeight: '1.45', display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>
                      {doc.business_overview || `${doc.canonical_name || doc.domain} offers specialized services in ${industryStr}.`}
                    </div>

                    {/* 4. Tech Stack Tags (Image 2 style) */}
                    {doc.technology_stack && doc.technology_stack.length > 0 && (
                      <div style={{ display: 'flex', gap: '0.3rem', flexWrap: 'wrap' }}>
                        {doc.technology_stack.slice(0, 4).map((tech, idx) => (
                          <span key={idx} style={{ fontSize: '0.66rem', background: '#111827', color: '#cbd5e1', border: '1px solid #1f2937', padding: '0.1rem 0.45rem', borderRadius: '0.25rem', fontWeight: 500 }}>
                            {tech}
                          </span>
                        ))}
                      </div>
                    )}

                    {/* 5. Footer Date & View Entire Dossier Button */}
                    <div style={{ display: 'flex', justifyContent: 'flex-end', alignItems: 'center', borderTop: '1px solid #1e293b', paddingTop: '0.5rem', marginTop: '0.2rem', fontSize: '0.68rem', fontFamily: 'monospace' }}>
                      <span style={{ color: '#64748b' }}>
                        ⏰ {new Date().toISOString().slice(0, 10)}
                      </span>
                    </div>

                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        const entId = doc.verified_entity_id || doc.universal_record_id || doc.canonical_domain || doc.domain || doc.id;
                        setSelectedEntityId(entId);
                      }}
                      style={{
                        marginTop: '0.4rem',
                        width: '100%',
                        padding: '0.5rem 0.75rem',
                        background: 'linear-gradient(90deg, #10b981, #059669)',
                        border: 'none',
                        borderRadius: '0.375rem',
                        color: '#ffffff',
                        fontWeight: 800,
                        fontSize: '0.78rem',
                        cursor: 'pointer',
                        display: 'flex',
                        alignItems: 'center',
                        justify: 'center',
                        gap: '0.35rem',
                        boxShadow: '0 2px 8px rgba(16, 185, 129, 0.25)'
                      }}
                    >
                      📋 View Entire Dossier ↗
                    </button>
                  </div>
                );
              })}
            </div>
          )
        )}

        {/* ── VERIFIED LEADS CARD GRID ── */}
        {leadView === 'verified' && (
          entitiesList.length === 0 ? (
            <div style={{ textAlign: 'center', padding: '4rem 2rem', color: '#64748b' }}>
              <div style={{ fontSize: '3rem', marginBottom: '1rem' }}>✅</div>
              <div style={{ fontSize: '1.1rem', color: '#94a3b8', marginBottom: '0.5rem' }}>No verified leads yet.</div>
              <div style={{ fontSize: '0.9rem' }}>The agent processes crawled leads in batches of 100 and verifies them. Press <strong style={{ color: '#10b981' }}>RUN</strong> to start.</div>
            </div>
          ) : (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))', gap: '1.25rem' }}>
              {entitiesList.map((ent) => {
                let domain = '';
                try { domain = new URL(ent.url.startsWith('http') ? ent.url : 'https://' + ent.url).hostname.replace('www.', ''); } catch {}
                const initial = (ent.canonical_name || domain || '?')[0].toUpperCase();
                
                // Calculate completeness score & tier dot
                let entScore = 75;
                if (ent.data_completeness?.total_score !== undefined) {
                  entScore = ent.data_completeness.total_score;
                } else if (ent.company_confidence_score !== undefined && ent.company_confidence_score !== null) {
                  entScore = ent.company_confidence_score > 1 ? ent.company_confidence_score : ent.company_confidence_score * 100;
                } else if (ent.confidence_score !== undefined && ent.confidence_score !== null) {
                  entScore = ent.confidence_score > 1 ? ent.confidence_score : ent.confidence_score * 100;
                } else if (ent.confidence !== undefined && ent.confidence !== null) {
                  entScore = ent.confidence > 1 ? ent.confidence : ent.confidence * 100;
                }

                let badgeTier = { dot: '🔵', label: 'COMPREHENSIVE', color: '#60a5fa', bg: 'rgba(59,130,246,0.15)', border: 'rgba(59,130,246,0.3)' };
                if (entScore >= 90) {
                  badgeTier = { dot: '🔵', label: 'COMPREHENSIVE', color: '#60a5fa', bg: 'rgba(59,130,246,0.15)', border: 'rgba(59,130,246,0.3)' };
                } else if (entScore >= 75) {
                  badgeTier = { dot: '🟢', label: 'STRONG', color: '#34d399', bg: 'rgba(16,185,129,0.15)', border: 'rgba(16,185,129,0.3)' };
                } else if (entScore >= 60) {
                  badgeTier = { dot: '🟡', label: 'BASIC', color: '#facc15', bg: 'rgba(234,179,8,0.15)', border: 'rgba(234,179,8,0.3)' };
                } else if (entScore >= 40) {
                  badgeTier = { dot: '🟠', label: 'LIMITED', color: '#fb923c', bg: 'rgba(249,115,22,0.15)', border: 'rgba(249,115,22,0.3)' };
                } else {
                  badgeTier = { dot: '🔴', label: 'INSUFFICIENT', color: '#f87171', bg: 'rgba(239,68,68,0.15)', border: 'rgba(239,68,68,0.3)' };
                }

                const locationStr = ent.headquarters || ent.country || 'Global';
                const industryStr = ent.industry || ent.domain || 'Software & SaaS';
                const emailStr = Array.isArray(ent.verified_emails) && ent.verified_emails[0] ? ent.verified_emails[0] : null;
                
                return (
                  <div
                    key={ent.id}
                    onClick={() => setSelectedEntityId(ent.id)}
                    style={{
                      background: '#0a101d',
                      border: `1px solid ${badgeTier.border}`,
                      borderRadius: '0.875rem',
                      padding: '1.1rem',
                      cursor: 'pointer',
                      transition: 'all 0.25s ease',
                      position: 'relative',
                      display: 'flex',
                      flexDirection: 'column',
                      gap: '0.65rem'
                    }}
                    onMouseEnter={e => { e.currentTarget.style.borderColor = '#00f2ff'; e.currentTarget.style.transform = 'translateY(-2px)'; e.currentTarget.style.boxShadow = `0 10px 25px -5px ${badgeTier.bg}`; }}
                    onMouseLeave={e => { e.currentTarget.style.borderColor = badgeTier.border; e.currentTarget.style.transform = 'none'; e.currentTarget.style.boxShadow = 'none'; }}
                  >
                    {/* 1. Header: Logo, Name & Website Link & Tier Dot Badge */}
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '0.5rem' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', overflow: 'hidden' }}>
                        {ent.logo_url ? (
                          <img src={ent.logo_url} alt="Logo" onError={(e) => { e.target.style.display = 'none'; }}
                            style={{ width: '38px', height: '38px', borderRadius: '0.5rem', flexShrink: 0, objectFit: 'contain', background: '#0f172a', padding: '2px', border: '1px solid #334155' }} />
                        ) : (
                          <div style={{
                            width: '38px', height: '38px', borderRadius: '0.5rem', flexShrink: 0,
                            background: '#1e293b', display: 'flex', alignItems: 'center', justifyContent: 'center',
                            fontWeight: 900, fontSize: '1.1rem', color: badgeTier.color, border: `1px solid ${badgeTier.border}`
                          }}>{initial}</div>
                        )}
                        <div style={{ overflow: 'hidden' }}>
                          <div style={{ fontWeight: 800, fontSize: '0.95rem', color: '#f8fafc', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                            {ent.canonical_name}
                          </div>
                          <a href={ent.url} target="_blank" rel="noreferrer" onClick={e => e.stopPropagation()}
                            style={{ fontSize: '0.72rem', color: '#38bdf8', textDecoration: 'none', display: 'inline-flex', alignItems: 'center', gap: '0.25rem' }}>
                            🌐 {domain} ↗
                          </a>
                        </div>
                      </div>

                      {/* Tier Dot Badge */}
                      <span style={{
                        display: 'inline-flex', alignItems: 'center', gap: '0.25rem',
                        fontSize: '0.65rem', fontWeight: 800, padding: '0.15rem 0.5rem', borderRadius: '9999px',
                        background: badgeTier.bg, color: badgeTier.color, border: `1px solid ${badgeTier.border}`,
                        whiteSpace: 'nowrap', flexShrink: 0
                      }}>
                        <span>{badgeTier.dot}</span>
                        <span>{badgeTier.label}</span>
                      </span>
                    </div>


                    {/* 2. Metadata Pills (Location, Industry & Email) */}
                    <div style={{ display: 'flex', gap: '0.35rem', flexWrap: 'wrap', alignItems: 'center', fontSize: '0.68rem', fontWeight: 600 }}>
                      <span style={{ background: '#111827', color: '#9ca3af', border: '1px solid #1f2937', padding: '0.15rem 0.5rem', borderRadius: '0.375rem' }}>
                        📍 {locationStr}
                      </span>
                      <span style={{ background: '#111827', color: '#9ca3af', border: '1px solid #1f2937', padding: '0.15rem 0.5rem', borderRadius: '0.375rem' }}>
                        💼 {industryStr}
                      </span>
                      {emailStr && (
                        <span style={{ background: 'rgba(16,185,129,0.15)', color: '#34d399', border: '1px solid rgba(16,185,129,0.3)', padding: '0.15rem 0.5rem', borderRadius: '0.375rem' }}>
                          ✉️ 1 Emails
                        </span>
                      )}
                    </div>

                    {/* 3. Business Overview Text Snippet */}
                    <div style={{ fontSize: '0.78rem', color: '#94a3b8', lineHeight: '1.45', display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>
                      {ent.business_overview || ent.description || `${ent.canonical_name} operates in the ${industryStr} domain.`}
                    </div>

                    {/* 4. Tech Stack Tags (Image 2 style) */}
                    {ent.technology_stack && ent.technology_stack.length > 0 && (
                      <div style={{ display: 'flex', gap: '0.3rem', flexWrap: 'wrap' }}>
                        {ent.technology_stack.slice(0, 4).map((tech, idx) => (
                          <span key={idx} style={{ fontSize: '0.66rem', background: '#111827', color: '#cbd5e1', border: '1px solid #1f2937', padding: '0.1rem 0.45rem', borderRadius: '0.25rem', fontWeight: 500 }}>
                            {tech}
                          </span>
                        ))}
                      </div>
                    )}

                    {/* 5. Footer Date & View Entire Dossier Button */}
                    <div style={{ display: 'flex', justifyContent: 'flex-end', alignItems: 'center', borderTop: '1px solid #1e293b', paddingTop: '0.5rem', marginTop: '0.2rem', fontSize: '0.68rem', fontFamily: 'monospace' }}>
                      <span style={{ color: '#64748b' }}>
                        ⏰ {new Date().toISOString().slice(0, 10)}
                      </span>
                    </div>

                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        setSelectedEntityId(ent.id);
                      }}
                      style={{
                        marginTop: '0.4rem',
                        width: '100%',
                        padding: '0.5rem 0.75rem',
                        background: 'linear-gradient(90deg, #10b981, #059669)',
                        border: 'none',
                        borderRadius: '0.375rem',
                        color: '#ffffff',
                        fontWeight: 800,
                        fontSize: '0.78rem',
                        cursor: 'pointer',
                        display: 'flex',
                        alignItems: 'center',
                        justify: 'center',
                        gap: '0.35rem',
                        boxShadow: '0 2px 8px rgba(16, 185, 129, 0.25)'
                      }}
                    >
                      📋 View Entire Dossier ↗
                    </button>
                  </div>
                );
              })}
            </div>
          )
        )}
      </div>

      {/* 5. CRAWLED DOCUMENT DETAIL VIEW MODAL */}
      {selectedDocumentId && (
        <div style={{ position: 'fixed', top: 0, left: 0, right: 0, bottom: 0, background: 'rgba(15, 23, 42, 0.85)', backdropFilter: 'blur(8px)', zIndex: 100, display: 'flex', justifyContent: 'center', alignItems: 'center', padding: '2rem' }}>
          <div style={{ background: '#1e293b', border: '1px solid #334155', borderRadius: '1rem', width: '100%', maxWidth: '1000px', maxHeight: '90vh', overflowY: 'auto', padding: '2rem', boxShadow: '0 25px 50px -12px rgba(0,0,0,0.5)', position: 'relative' }}>
            
            {/* Close Button */}
            <button
              onClick={() => setSelectedDocumentId(null)}
              style={{ position: 'absolute', top: '1.5rem', right: '1.5rem', background: '#0f172a', border: '1px solid #334155', color: 'white', borderRadius: '50%', width: '36px', height: '36px', cursor: 'pointer', fontSize: '1.2rem', fontWeight: 700 }}
            >
              ✕
            </button>

            {loadingDocDetail || !documentDetail ? (
              <div style={{ textAlign: 'center', padding: '4rem 0' }}>
                <div className="spinner" style={{ margin: '0 auto 1rem auto' }}></div>
                <div>Fetching crawled HTML & raw storage data from OpenDB vault...</div>
              </div>
            ) : (
              <div>
                {/* Header */}
                <div style={{ display: 'flex', alignItems: 'center', gap: '1.25rem', borderBottom: '1px solid #334155', paddingBottom: '1.25rem', marginBottom: '1.5rem' }}>
                  {documentDetail.logo_url ? (
                    <img src={documentDetail.logo_url} alt="Logo" onError={(e) => { e.target.style.display = 'none'; }}
                      style={{ width: '52px', height: '52px', borderRadius: '0.75rem', background: '#0f172a', padding: '3px', border: '1px solid #334155', objectFit: 'contain', flexShrink: 0 }} />
                  ) : (
                    <div style={{ width: '52px', height: '52px', borderRadius: '0.75rem', background: '#3b82f6', display: 'flex', alignItems: 'center', justifyContent: 'center', fontWeight: 900, fontSize: '1.5rem', color: '#fff', flexShrink: 0 }}>
                      {(documentDetail.canonical_name || documentDetail.domain || '?')[0].toUpperCase()}
                    </div>
                  )}
                  <div style={{ overflow: 'hidden', flex: 1 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', flexWrap: 'wrap' }}>
                      <h2 style={{ margin: 0, fontSize: '1.75rem', color: '#f8fafc' }}>{documentDetail.canonical_name}</h2>
                      <span style={{
                        fontSize: '0.7rem', fontWeight: 800, padding: '0.2rem 0.6rem', borderRadius: '9999px',
                        background: documentDetail.status === 'Verified' ? 'rgba(16,185,129,0.2)' : 'rgba(124,58,237,0.2)',
                        color: documentDetail.status === 'Verified' ? '#34d399' : '#a78bfa',
                        border: `1px solid ${documentDetail.status === 'Verified' ? '#10b981' : '#7c3aed'}`
                      }}>
                        {documentDetail.status === 'Verified' ? '✅ Verified Entity' : '⚡ Raw Ingested Page'}
                      </span>
                      <span style={{ fontSize: '0.7rem', fontWeight: 700, padding: '0.2rem 0.6rem', borderRadius: '9999px', background: 'rgba(16,185,129,0.15)', color: '#34d399', border: '1px solid #10b981' }}>
                        HTTP {documentDetail.http_status} OK
                      </span>
                    </div>
                    <a href={documentDetail.url} target="_blank" rel="noreferrer" style={{ color: '#60a5fa', fontSize: '0.9rem', marginTop: '0.25rem', display: 'inline-block' }}>
                      🔗 {documentDetail.url}
                    </a>
                  </div>
                </div>

                {/* Key Metadata Stats */}
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '1rem', marginBottom: '1.5rem' }}>
                  <div style={{ background: '#0f172a', padding: '0.85rem 1rem', borderRadius: '0.5rem', border: '1px solid #1e293b' }}>
                    <span className="data-label">INDUSTRY SECTOR</span>
                    <div style={{ color: '#f8fafc', fontWeight: 700, fontSize: '0.95rem' }}>{documentDetail.industry}</div>
                  </div>
                  <div style={{ background: '#0f172a', padding: '0.85rem 1rem', borderRadius: '0.5rem', border: '1px solid #1e293b' }}>
                    <span className="data-label">LOCATION / REGION</span>
                    <div style={{ color: '#f8fafc', fontWeight: 700, fontSize: '0.95rem' }}>{documentDetail.country}</div>
                  </div>
                  <div style={{ background: '#0f172a', padding: '0.85rem 1rem', borderRadius: '0.5rem', border: '1px solid #1e293b' }}>
                    <span className="data-label">COMPANY SIZE TIER</span>
                    <div style={{ color: '#38bdf8', fontWeight: 700, fontSize: '0.95rem' }}>{documentDetail.company_tier}</div>
                  </div>
                  <div style={{ background: '#0f172a', padding: '0.85rem 1rem', borderRadius: '0.5rem', border: '1px solid #1e293b' }}>
                    <span className="data-label">EXTRACTED WORD COUNT</span>
                    <div style={{ color: '#34d399', fontWeight: 700, fontSize: '0.95rem' }}>{documentDetail.word_count.toLocaleString()} words</div>
                  </div>
                </div>

                {/* Vault Path & Storage Details */}
                <div style={{ background: '#0f172a', padding: '1rem', borderRadius: '0.5rem', border: '1px solid #334155', marginBottom: '1.5rem' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '0.5rem' }}>
                    <div>
                      <span className="data-label">PERSISTED RAW STORAGE VAULT PATH</span>
                      <code style={{ display: 'block', color: '#a78bfa', fontFamily: 'monospace', fontSize: '0.85rem', marginTop: '0.2rem' }}>
                        {documentDetail.raw_path}
                      </code>
                    </div>
                    {documentDetail.retrieved_at && (
                      <div style={{ fontSize: '0.75rem', color: '#64748b' }}>
                        Ingested at: {new Date(documentDetail.retrieved_at).toLocaleString()}
                      </div>
                    )}
                  </div>
                </div>

                {/* Extended Firmographics Preview (Tech Stack, Revenue, Email) */}
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '1rem', marginBottom: '1.5rem' }}>
                  <div style={{ background: '#0f172a', padding: '0.85rem 1rem', borderRadius: '0.5rem', border: '1px solid #1e293b' }}>
                    <span className="data-label">REVENUE & FUNDING</span>
                    <div style={{ color: '#34d399', fontWeight: 700, fontSize: '0.9rem', marginTop: '0.15rem' }}>{documentDetail.revenue_funding || 'Bootstrapped'}</div>
                  </div>
                  <div style={{ background: '#0f172a', padding: '0.85rem 1rem', borderRadius: '0.5rem', border: '1px solid #1e293b' }}>
                    <span className="data-label">VERIFIED CONTACT EMAIL</span>
                    <div style={{ color: '#38bdf8', fontWeight: 700, fontSize: '0.9rem', marginTop: '0.15rem' }}>
                      {Array.isArray(documentDetail.verified_emails) && documentDetail.verified_emails.length > 0
                        ? documentDetail.verified_emails.join(', ')
                        : 'Not Detected'}
                    </div>
                  </div>
                </div>

                {/* Technology Stack Badges */}
                {documentDetail.technology_stack && documentDetail.technology_stack.length > 0 && (
                  <div style={{ marginBottom: '1.5rem' }}>
                    <span className="data-label" style={{ display: 'block', marginBottom: '0.4rem' }}>TECHNOLOGY STACK SIGNALS</span>
                    <div style={{ display: 'flex', gap: '0.4rem', flexWrap: 'wrap' }}>
                      {documentDetail.technology_stack.map((t, idx) => (
                        <span key={idx} style={{ background: '#0f172a', border: '1px solid #334155', color: '#cbd5e1', padding: '0.25rem 0.6rem', borderRadius: '0.375rem', fontSize: '0.78rem', fontWeight: 600 }}>
                          {t}
                        </span>
                      ))}
                    </div>
                  </div>
                )}

                {/* Raw Crawled Content Preview */}
                <div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.75rem' }}>
                    <h3 style={{ color: '#60a5fa', fontSize: '1.1rem', margin: 0 }}>
                      Crawled Page Text & Extracted Content
                    </h3>
                    <span style={{ fontSize: '0.75rem', color: '#94a3b8' }}>
                      Clean Readable Text (HTML Stripped)
                    </span>
                  </div>
                  <div style={{
                    background: '#0f172a', color: '#cbd5e1', padding: '1.25rem', borderRadius: '0.5rem',
                    border: '1px solid #334155', maxHeight: '350px', overflowY: 'auto', fontFamily: 'sans-serif',
                    fontSize: '0.875rem', lineHeight: '1.6', whiteSpace: 'pre-wrap'
                  }}>
                    {documentDetail.text_preview}
                  </div>
                </div>

                {/* Footer Action Bar */}
                <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '1rem', marginTop: '1.5rem', borderTop: '1px solid #334155', paddingTop: '1.25rem' }}>
                  <button
                    onClick={() => setSelectedDocumentId(null)}
                    style={{ padding: '0.65rem 1.25rem', background: '#0f172a', border: '1px solid #334155', color: '#94a3b8', borderRadius: '0.5rem', fontWeight: 700, cursor: 'pointer' }}
                  >
                    Close
                  </button>
                  <button
                    onClick={() => {
                      const entId = documentDetail.verified_entity_id || documentDetail.id;
                      setSelectedDocumentId(null);
                      setSelectedEntityId(entId);
                    }}
                    style={{ padding: '0.65rem 1.25rem', background: 'linear-gradient(90deg, #10b981, #059669)', border: 'none', color: '#fff', borderRadius: '0.5rem', fontWeight: 700, cursor: 'pointer' }}
                  >
                    📋 View Entire Dossier ↗
                  </button>
                  <a
                    href={documentDetail.url}
                    target="_blank"
                    rel="noreferrer"
                    style={{ padding: '0.65rem 1.25rem', background: '#3b82f6', color: '#fff', borderRadius: '0.5rem', fontWeight: 700, textDecoration: 'none', display: 'inline-block' }}
                  >
                    Visit Live Website ↗
                  </a>
                </div>

              </div>
            )}
          </div>
        </div>
      )}

      {/* 6. ENTITY DETAIL VIEW (DRILL-IN MODAL) */}
      {selectedEntityId && (
        <div style={{ position: 'fixed', top: 0, left: 0, right: 0, bottom: 0, background: 'rgba(15, 23, 42, 0.85)', backdropFilter: 'blur(8px)', zIndex: 100, display: 'flex', justifyContent: 'center', alignItems: 'center', padding: '1.5rem' }}>
          <div style={{ background: '#0b1322', border: '1px solid #1e293b', borderRadius: '1rem', width: '100%', maxWidth: '1150px', maxHeight: '90vh', overflowY: 'auto', padding: '1.75rem', boxShadow: '0 25px 50px -12px rgba(0,0,0,0.7)', position: 'relative' }}>
            
            {loadingDetail || !entityDetail ? (
              <div style={{ textAlign: 'center', padding: '4rem 0', color: '#94a3b8' }}>
                <div className="spinner" style={{ margin: '0 auto 1rem auto' }}></div>
                <div>Synthesizing entity audit & evidence from OpenDB storage...</div>
              </div>
            ) : (
              <div>
                {/* Header Bar with Agentic Verification Badge */}
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', borderBottom: '1px solid #1e293b', paddingBottom: '1.25rem', marginBottom: '1.5rem', flexWrap: 'wrap', gap: '1rem' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '1rem' }}>
                    {entityDetail.logo_url ? (
                      <img
                        src={entityDetail.logo_url}
                        alt="Logo"
                        onError={(e) => { e.target.style.display = 'none'; }}
                        style={{ width: '46px', height: '46px', borderRadius: '0.6rem', background: '#0f172a', padding: '3px', border: '1px solid #334155', objectFit: 'contain' }}
                      />
                    ) : (
                      <div style={{ width: '46px', height: '46px', borderRadius: '0.6rem', background: '#3b82f6', display: 'flex', alignItems: 'center', justifyContent: 'center', fontWeight: 900, color: '#fff', fontSize: '1.2rem' }}>
                        {(entityDetail.canonical_name || '?')[0].toUpperCase()}
                      </div>
                    )}
                    <div>
                      <h2 style={{ margin: 0, fontSize: '1.75rem', fontWeight: 900, color: '#ffffff', letterSpacing: '-0.02em' }}>
                        {entityDetail.canonical_name}
                      </h2>
                      <div style={{ fontSize: '0.85rem', color: '#38bdf8', marginTop: '0.2rem', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                        <span>{entityDetail.domain}</span>
                        <span>•</span>
                        <a href={entityDetail.official_website} target="_blank" rel="noreferrer" style={{ color: '#38bdf8', textDecoration: 'none' }}>
                          {entityDetail.official_website} ↗
                        </a>
                      </div>
                    </div>
                  </div>

                  {/* Completeness Badge & Re-Verify Button */}
                  <div style={{ display: 'flex', alignItems: 'center', gap: '1rem', flexWrap: 'wrap' }}>
                    {entityDetail.data_completeness && (
                      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: '0.2rem' }}>
                        <div style={{
                          padding: '0.35rem 0.85rem', borderRadius: '9999px', fontWeight: 800, fontSize: '0.78rem',
                          background: 'rgba(56, 189, 248, 0.15)', color: '#38bdf8', border: '1px solid rgba(56, 189, 248, 0.4)'
                        }}>
                          {entityDetail.data_completeness.badge_level}
                        </div>
                        <div style={{ fontSize: '0.75rem', color: '#94a3b8', display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
                          <span>Data Completeness:</span>
                          <strong style={{ color: '#34d399' }}>{entityDetail.data_completeness.total_score} / 100</strong>
                        </div>
                      </div>
                    )}

                    <button
                      onClick={handleTriggerVerification}
                      disabled={verifying}
                      style={{
                        background: 'linear-gradient(135deg, #0ea5e9, #0284c7)', color: '#fff', border: 'none',
                        borderRadius: '0.5rem', padding: '0.5rem 1rem', cursor: verifying ? 'not-allowed' : 'pointer',
                        fontSize: '0.85rem', fontWeight: 800, boxShadow: '0 4px 12px rgba(14, 165, 233, 0.3)'
                      }}
                    >
                      {verifying ? '🔄 Verifying...' : '⚡ Re-Verify Agentically'}
                    </button>

                    <button
                      onClick={() => setSelectedEntityId(null)}
                      style={{ background: '#1e293b', border: '1px solid #334155', color: '#94a3b8', borderRadius: '0.5rem', padding: '0.5rem 0.8rem', cursor: 'pointer', fontSize: '0.85rem', fontWeight: 700 }}
                    >
                      ✕ Close
                    </button>
                  </div>
                </div>

                {/* 11-SECTION DATA COMPLETENESS AUDIT PANEL */}
                {entityDetail.data_completeness?.checklist && (
                  <div style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: '0.75rem', padding: '1rem', marginBottom: '1.5rem' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.6rem' }}>
                      <h4 style={{ margin: 0, fontSize: '0.78rem', fontWeight: 800, color: '#94a3b8', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                        AGENTIC 11-SECTION DATA COMPLETENESS AUDIT
                      </h4>
                      <div style={{ fontSize: '0.75rem', fontWeight: 800, color: '#38bdf8' }}>
                        Score: {entityDetail.data_completeness.total_score}/100
                      </div>
                    </div>

                    {/* Progress bar */}
                    <div style={{ width: '100%', height: '8px', background: '#1e293b', borderRadius: '4px', overflow: 'hidden', marginBottom: '0.85rem' }}>
                      <div style={{
                        width: `${entityDetail.data_completeness.total_score}%`, height: '100%',
                        background: entityDetail.data_completeness.total_score >= 80 ? 'linear-gradient(90deg, #10b981, #059669)' :
                                    entityDetail.data_completeness.total_score >= 60 ? 'linear-gradient(90deg, #eab308, #ca8a04)' :
                                    'linear-gradient(90deg, #f97316, #dc2626)',
                        transition: 'width 0.5s ease'
                      }} />
                    </div>

                    {/* 11 Pills grid */}
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(160px, 1fr))', gap: '0.4rem' }}>
                      {Object.entries(entityDetail.data_completeness.checklist).map(([reqKey, reqStatus]) => {
                        let badgeBg = 'rgba(16, 185, 129, 0.15)';
                        let badgeColor = '#34d399';
                        let badgeBorder = 'rgba(16, 185, 129, 0.3)';
                        let statusLabel = 'OBSERVED';

                        if (reqStatus === 'partial') {
                          badgeBg = 'rgba(234, 179, 8, 0.15)';
                          badgeColor = '#facc15';
                          badgeBorder = 'rgba(234, 179, 8, 0.3)';
                          statusLabel = 'INFERRED';
                        } else if (reqStatus === 'missing') {
                          badgeBg = 'rgba(239, 68, 68, 0.15)';
                          badgeColor = '#f87171';
                          badgeBorder = 'rgba(239, 68, 68, 0.3)';
                          statusLabel = 'MISSING';
                        } else if (reqStatus === 'not_public') {
                          badgeBg = 'rgba(148, 163, 184, 0.12)';
                          badgeColor = '#cbd5e1';
                          badgeBorder = 'rgba(148, 163, 184, 0.25)';
                          statusLabel = 'UNAVAILABLE_PUBLICLY';
                        }

                        return (
                          <div key={reqKey} style={{ background: '#111827', border: `1px solid ${badgeBorder}`, borderRadius: '0.4rem', padding: '0.35rem 0.55rem', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                            <span style={{ fontSize: '0.7rem', color: '#94a3b8', textTransform: 'capitalize' }}>
                              {reqKey.replace('_', ' ')}
                            </span>
                            <span style={{ fontSize: '0.62rem', fontWeight: 800, color: badgeColor, background: badgeBg, padding: '0.1rem 0.35rem', borderRadius: '0.2rem' }}>
                              {statusLabel}
                            </span>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                )}

                {/* 2-Column Main Layout matching uploaded screenshot */}
                <div style={{ display: 'grid', gridTemplateColumns: '1.8fr 1fr', gap: '1.5rem' }}>

                  
                  {/* LEFT COLUMN: Deep Content */}
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>
                    
                    {/* 1. BUSINESS OVERVIEW & SYNTHESIS */}
                    <div>
                      <h3 style={{ fontSize: '0.8rem', fontWeight: 800, color: '#94a3b8', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '0.6rem' }}>
                        BUSINESS OVERVIEW & SYNTHESIS
                      </h3>
                      <div style={{ background: '#111827', border: '1px solid #1f2937', borderRadius: '0.75rem', padding: '1.1rem', color: '#cbd5e1', fontSize: '0.9rem', lineHeight: '1.6' }}>
                        {(typeof entityDetail.business_overview === 'object' ? entityDetail.business_overview?.text : entityDetail.business_overview) || entityDetail.summary || 'Not found'}
                        {Array.isArray(entityDetail.business_overview?.source_pages) && entityDetail.business_overview.source_pages.length > 0 && (
                          <div style={{ marginTop: '0.5rem', paddingTop: '0.5rem', borderTop: '1px solid #1f2937', fontSize: '0.75rem', color: '#64748b' }}>
                            Source Pages: {entityDetail.business_overview.source_pages.map((url, uidx) => (
                              <a key={uidx} href={url} target="_blank" rel="noreferrer" style={{ color: '#38bdf8', textDecoration: 'none', marginLeft: '0.4rem' }}>
                                {url} ↗
                              </a>
                            ))}
                          </div>
                        )}
                      </div>
                    </div>

                    {/* 2. TECHNOLOGY STACK */}
                    <div>
                      <h3 style={{ fontSize: '0.8rem', fontWeight: 800, color: '#94a3b8', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '0.6rem' }}>
                        TECHNOLOGY STACK
                      </h3>
                      <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
                        {Array.isArray(entityDetail.technology_stack) && entityDetail.technology_stack.length > 0 ? (
                          entityDetail.technology_stack.map((tech, i) => (
                            <span key={i} style={{ background: '#111827', border: '1px solid #374151', color: '#f3f4f6', padding: '0.35rem 0.75rem', borderRadius: '0.5rem', fontSize: '0.8rem', fontWeight: 600 }}>
                              {tech}
                            </span>
                          ))
                        ) : (
                          <div style={{ fontSize: '0.85rem', color: '#6b7280' }}>No technology signals extracted yet.</div>
                        )}
                      </div>
                    </div>

                    {/* 3. DECISION MAKERS & LEADERSHIP */}
                    <div>
                      <h3 style={{ fontSize: '0.8rem', fontWeight: 800, color: '#94a3b8', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '0.6rem' }}>
                        DECISION MAKERS & LEADERSHIP ({Array.isArray(entityDetail.decision_makers) ? entityDetail.decision_makers.length : 0})
                      </h3>
                      {Array.isArray(entityDetail.decision_makers) && entityDetail.decision_makers.length > 0 ? (
                        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.6rem' }}>
                          {entityDetail.decision_makers.map((p, idx) => {
                            const tags = Array.isArray(p.role_tags) ? p.role_tags : [p.role_tag || 'Unknown'];
                            return (
                              <div key={idx} style={{ background: '#111827', border: '1px solid #1f2937', borderRadius: '0.65rem', padding: '0.85rem 1rem', display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '0.5rem' }}>
                                <div>
                                  <div style={{ fontWeight: 800, color: '#ffffff', fontSize: '0.92rem', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                                    <span>{p.name}</span>
                                    <span style={{ color: '#22d3ee', fontWeight: 600 }}>({p.title || 'Leadership'})</span>
                                  </div>
                                  <div style={{ display: 'flex', gap: '0.35rem', marginTop: '0.35rem', flexWrap: 'wrap' }}>
                                    {tags.map((t, ti) => (
                                      <span key={ti} style={{ background: 'rgba(34, 211, 238, 0.12)', color: '#22d3ee', border: '1px solid rgba(34, 211, 238, 0.3)', padding: '0.1rem 0.45rem', borderRadius: '0.25rem', fontSize: '0.7rem', fontWeight: 700 }}>
                                        {t}
                                      </span>
                                    ))}
                                    {p.confidence && (
                                      <span style={{ background: 'rgba(148, 163, 184, 0.1)', color: '#94a3b8', border: '1px solid rgba(148, 163, 184, 0.2)', padding: '0.1rem 0.45rem', borderRadius: '0.25rem', fontSize: '0.7rem' }}>
                                        {p.confidence} confidence
                                      </span>
                                    )}
                                  </div>
                                </div>
                                {p.linkedin_search_url && (
                                  <a
                                    href={p.linkedin_search_url}
                                    target="_blank"
                                    rel="noreferrer"
                                    style={{ fontSize: '0.75rem', fontWeight: 700, color: '#0a66c2', background: 'rgba(10, 102, 194, 0.12)', border: '1px solid rgba(10, 102, 194, 0.3)', padding: '0.3rem 0.6rem', borderRadius: '0.375rem', textDecoration: 'none' }}
                                  >
                                    in LinkedIn Search ↗
                                  </a>
                                )}
                              </div>
                            );
                          })}
                        </div>
                      ) : (
                        <div style={{ background: '#111827', border: '1px solid #1f2937', borderRadius: '0.65rem', padding: '0.85rem 1rem', color: '#9ca3af', fontSize: '0.85rem' }}>
                          Not found (No verified decision maker attributed on site text)
                        </div>
                      )}
                    </div>

                    {/* 4. CRAWLED SUBPAGES & MARKDOWN VAULT */}
                    <div>
                      <h3 style={{ fontSize: '0.8rem', fontWeight: 800, color: '#94a3b8', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '0.6rem' }}>
                        CRAWLED SUBPAGES & MARKDOWN VAULT ({Array.isArray(entityDetail.crawled_subpages) ? entityDetail.crawled_subpages.length : 0})
                      </h3>
                      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
                        {Array.isArray(entityDetail.crawled_subpages) && entityDetail.crawled_subpages.length > 0 ? (
                          entityDetail.crawled_subpages.map((sp, idx) => (
                            <div key={idx} style={{ background: '#111827', border: '1px solid #1f2937', borderRadius: '0.65rem', padding: '0.75rem 1rem', display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '0.5rem' }}>
                              <div style={{ fontWeight: 700, color: '#f3f4f6', fontSize: '0.85rem' }}>{sp.path || '/'} • {sp.title || entityDetail.canonical_name}</div>
                              <div style={{ fontFamily: 'monospace', fontSize: '0.75rem', color: '#34d399', background: 'rgba(16,185,129,0.08)', padding: '0.2rem 0.55rem', borderRadius: '0.25rem' }}>
                                MinIO: {sp.storage_path || sp.minio_raw_path || `companies/${entityDetail.domain || 'domain'}/pages/homepage.md`}
                              </div>
                            </div>
                          ))
                        ) : (
                          <div style={{ background: '#111827', border: '1px solid #1f2937', borderRadius: '0.65rem', padding: '0.75rem 1rem', color: '#9ca3af', fontSize: '0.85rem' }}>
                            Not found (No stored subpages in markdown vault)
                          </div>
                        )}
                      </div>
                    </div>

                    {/* 5. FORMULA-BASED WARMTH SCORE PANEL */}
                    {entityDetail.warmth_score && (
                      <div style={{ background: '#111827', border: '1px solid #1f2937', borderRadius: '0.75rem', padding: '1rem' }}>
                        <div style={{ fontSize: '0.75rem', fontWeight: 800, color: '#94a3b8', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '0.4rem' }}>
                          PRODUCIBLE WARMTH SCORE FORMULA
                        </div>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
                          <div style={{ fontSize: '1.75rem', fontWeight: 900, color: '#10b981' }}>
                            {typeof entityDetail.warmth_score === 'object' ? entityDetail.warmth_score.value : entityDetail.warmth_score} / 10.0
                          </div>
                          <div style={{ fontSize: '0.8rem', color: '#cbd5e1', lineHeight: '1.4' }}>
                            {typeof entityDetail.warmth_score === 'object' ? entityDetail.warmth_score.explanation : 'Formula: Email (3.0) + Leadership (3.0) + Firmographics (2.0) + Vault Storage (2.0)'}
                          </div>
                        </div>
                      </div>
                    )}

                  </div>

                  {/* RIGHT COLUMN: Sidebar Metadata Card */}
                  <div style={{ background: '#111827', border: '1px solid #1f2937', borderRadius: '0.875rem', padding: '1.25rem', height: 'fit-content' }}>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
                      
                      {/* HEADQUARTERS */}
                      <div>
                        <div style={{ fontSize: '0.7rem', fontWeight: 800, color: '#6b7280', textTransform: 'uppercase', letterSpacing: '0.05em' }}>HEADQUARTERS</div>
                        <div style={{ fontSize: '0.95rem', fontWeight: 800, color: entityDetail.firmographics?.headquarters?.value || entityDetail.firmographics?.headquarters ? '#ffffff' : '#6b7280', marginTop: '0.15rem' }}>
                          {(typeof entityDetail.firmographics?.headquarters === 'object' ? entityDetail.firmographics.headquarters.value : entityDetail.firmographics?.headquarters) || 'Not found'}
                        </div>
                        {entityDetail.firmographics?.headquarters?.conflict && (
                          <div style={{ fontSize: '0.7rem', color: '#f59e0b', marginTop: '0.2rem', fontWeight: 700 }}>
                            ⚠️ Conflict Detected (Dataset: {entityDetail.firmographics.headquarters.conflicting_value})
                          </div>
                        )}
                        {entityDetail.firmographics?.headquarters?.is_stale && (
                          <div style={{ fontSize: '0.7rem', color: '#64748b', marginTop: '0.2rem' }}>
                            🕒 Stale Data (Last confirmed {entityDetail.firmographics.headquarters.last_confirmed_at?.slice(0, 10)})
                          </div>
                        )}
                      </div>

                      {/* INDUSTRY */}
                      <div>
                        <div style={{ fontSize: '0.7rem', fontWeight: 800, color: '#6b7280', textTransform: 'uppercase', letterSpacing: '0.05em' }}>INDUSTRY</div>
                        <div style={{ fontSize: '0.95rem', fontWeight: 800, color: entityDetail.firmographics?.industry?.value || entityDetail.industry ? '#ffffff' : '#6b7280', marginTop: '0.15rem' }}>
                          {(typeof entityDetail.firmographics?.industry === 'object' ? entityDetail.firmographics.industry.value : entityDetail.industry) || 'Not found'}
                        </div>
                      </div>

                      {/* COMPANY SIZE */}
                      <div>
                        <div style={{ fontSize: '0.7rem', fontWeight: 800, color: '#6b7280', textTransform: 'uppercase', letterSpacing: '0.05em' }}>COMPANY SIZE</div>
                        <div style={{ fontSize: '0.95rem', fontWeight: 800, color: entityDetail.firmographics?.company_size?.value || entityDetail.company_size ? '#ffffff' : '#6b7280', marginTop: '0.15rem' }}>
                          {(typeof entityDetail.firmographics?.company_size === 'object' ? entityDetail.firmographics.company_size.value : entityDetail.company_size) || 'Not found'}
                        </div>
                      </div>

                      {/* REVENUE / FUNDING */}
                      <div>
                        <div style={{ fontSize: '0.7rem', fontWeight: 800, color: '#6b7280', textTransform: 'uppercase', letterSpacing: '0.05em' }}>REVENUE / FUNDING</div>
                        <div style={{ fontSize: '0.95rem', fontWeight: 800, color: entityDetail.firmographics?.revenue_funding?.value || entityDetail.revenue_funding ? '#34d399' : '#6b7280', marginTop: '0.15rem' }}>
                          {(typeof entityDetail.firmographics?.revenue_funding === 'object' ? entityDetail.firmographics.revenue_funding.value : entityDetail.revenue_funding) || 'Not found'}
                        </div>
                      </div>

                      {/* VERIFIED EMAILS */}
                      <div>
                        <div style={{ fontSize: '0.7rem', fontWeight: 800, color: '#6b7280', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '0.2rem' }}>VERIFIED EMAILS</div>
                        {Array.isArray(entityDetail.verified_emails) && entityDetail.verified_emails.length > 0 ? (
                          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.3rem' }}>
                            {entityDetail.verified_emails.map((e, idx) => {
                              const emailStr = typeof e === 'object' ? e.email : e;
                              const statusStr = typeof e === 'object' ? (e.status || 'verified') : 'verified';
                              return (
                                <div key={idx} style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', fontSize: '0.82rem' }}>
                                  <span style={{ color: '#38bdf8', fontWeight: 700, wordBreak: 'break-all' }}>{emailStr}</span>
                                  <span style={{
                                    fontSize: '0.65rem', fontWeight: 800, padding: '0.05rem 0.4rem', borderRadius: '0.2rem',
                                    background: statusStr === 'verified' ? 'rgba(16,185,129,0.15)' : 'rgba(245,158,11,0.15)',
                                    color: statusStr === 'verified' ? '#34d399' : '#f59e0b',
                                    border: statusStr === 'verified' ? '1px solid rgba(16,185,129,0.3)' : '1px solid rgba(245,158,11,0.3)'
                                  }}>
                                    {statusStr.toUpperCase()}
                                  </span>
                                </div>
                              );
                            })}
                          </div>
                        ) : (
                          <div style={{ fontSize: '0.85rem', color: '#6b7280' }}>Not found</div>
                        )}
                      </div>

                      {/* EXTRACTION AUDIT & SOURCE PANEL */}
                      <div style={{ borderTop: '1px solid #1f2937', paddingTop: '1rem', marginTop: '0.5rem' }}>
                        <div style={{ fontSize: '0.7rem', fontWeight: 800, color: '#6b7280', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '0.4rem' }}>
                          EXTRACTION AUDIT PANEL
                        </div>
                        <div style={{ display: 'inline-block', fontFamily: 'monospace', fontSize: '0.75rem', fontWeight: 800, color: '#38bdf8', background: 'rgba(56,189,248,0.1)', padding: '0.25rem 0.55rem', borderRadius: '0.375rem', border: '1px solid rgba(56,189,248,0.2)' }}>
                          {entityDetail.extraction_audit?.source_dataset || entityDetail.provenance?.source_type || '⚡ SQLITE_OPERATIONAL_TRUTH'}
                        </div>
                        <div style={{ fontFamily: 'monospace', fontSize: '0.75rem', color: '#9ca3af', marginTop: '0.4rem' }}>
                          🕒 {entityDetail.extraction_audit?.crawl_finished_at || entityDetail.provenance?.extracted_at || new Date().toISOString()}
                        </div>
                        <div style={{ fontSize: '0.72rem', color: '#64748b', marginTop: '0.25rem' }}>
                          Pages Crawled: {entityDetail.extraction_audit?.pages_crawled || 1} | Failed: {entityDetail.extraction_audit?.pages_failed || 0}
                        </div>
                      </div>

                      <a
                        href={entityDetail.website?.url || entityDetail.official_website}
                        target="_blank"
                        rel="noreferrer"
                        style={{
                          display: 'block', textAlign: 'center', marginTop: '0.75rem', padding: '0.75rem 1rem',
                          background: 'linear-gradient(135deg, #7c3aed, #4f46e5)', color: '#ffffff',
                          borderRadius: '0.5rem', fontWeight: 800, fontSize: '0.9rem', textDecoration: 'none',
                          boxShadow: '0 4px 12px rgba(124, 58, 237, 0.3)'
                        }}
                      >
                        🌐 Visit Official Website ↗
                      </a>

                    </div>
                  </div>

                </div>
              </div>
            )}
          </div>
        </div>
      )}
      {/* 7. PIPELINE INSPECTOR (CP-01..CP-30 RUNTIME AUDIT MODAL) */}
      {showInspectorModal && (
        <div style={{ position: 'fixed', top: 0, left: 0, right: 0, bottom: 0, background: 'rgba(15, 23, 42, 0.90)', backdropFilter: 'blur(10px)', zIndex: 110, display: 'flex', justifyContent: 'center', alignItems: 'center', padding: '1.5rem' }}>
          <div style={{ background: '#0b1322', border: '1px solid #334155', borderRadius: '1rem', width: '100%', maxWidth: '1200px', maxHeight: '90vh', overflowY: 'auto', padding: '1.75rem', boxShadow: '0 25px 50px -12px rgba(0,0,0,0.8)', position: 'relative' }}>
            
            {/* Modal Header */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', borderBottom: '1px solid #1e293b', paddingBottom: '1rem', marginBottom: '1.5rem' }}>
              <div>
                <h2 style={{ margin: 0, fontSize: '1.5rem', fontWeight: 900, color: '#f8fafc', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                  🔬 PIPELINE INSPECTOR — Live Runtime Checkpoint Auditor
                </h2>
                <div style={{ fontSize: '0.85rem', color: '#94a3b8', marginTop: '0.2rem' }}>
                  End-to-End Trace Audit of all 30 Application Checkpoints (CP-01 → CP-30)
                </div>
              </div>

              <div style={{ display: 'flex', alignItems: 'center', gap: '1rem' }}>
                <button
                  onClick={handleOpenInspector}
                  style={{ background: '#1e293b', color: '#38bdf8', border: '1px solid #334155', borderRadius: '0.5rem', padding: '0.4rem 0.8rem', fontWeight: 700, fontSize: '0.8rem', cursor: 'pointer' }}
                >
                  🔄 Refresh Trace
                </button>
                <button
                  onClick={() => setShowInspectorModal(false)}
                  style={{ background: '#ef4444', color: '#fff', border: 'none', borderRadius: '0.5rem', width: '32px', height: '32px', fontWeight: 900, cursor: 'pointer' }}
                >
                  ✕
                </button>
              </div>
            </div>

            {loadingInspector || !inspectorData ? (
              <div style={{ textAlign: 'center', padding: '4rem 0', color: '#94a3b8' }}>
                <div className="spinner" style={{ margin: '0 auto 1rem auto' }}></div>
                <div>Fetching live 30-checkpoint audit telemetry from OpenDB backend...</div>
              </div>
            ) : (
              <div>
                {/* 1. HEALTH SCORE OVERVIEW BAR */}
                <div style={{ background: 'linear-gradient(135deg, #0f172a, #1e293b)', padding: '1.25rem', borderRadius: '0.75rem', border: '1px solid #334155', marginBottom: '1.5rem', display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '1.5rem' }}>
                  <div>
                    <div style={{ fontSize: '0.75rem', fontWeight: 800, color: '#94a3b8', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                      Overall OpenDB Pipeline Health Score
                    </div>
                    <div style={{ fontSize: '2.5rem', fontWeight: 900, color: '#10b981', marginTop: '0.1rem' }}>
                      {inspectorData.overall_opendb_health_score || 85.0} <span style={{ fontSize: '1.2rem', color: '#64748b' }}>/ 100</span>
                    </div>
                  </div>

                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(130px, 1fr))', gap: '1rem', flex: 1, maxWidth: '650px' }}>
                    <div style={{ background: '#0b1322', padding: '0.6rem 0.8rem', borderRadius: '0.5rem', border: '1px solid #334155' }}>
                      <div style={{ fontSize: '0.68rem', color: '#94a3b8', fontWeight: 700 }}>System Health (20%)</div>
                      <div style={{ fontSize: '1.1rem', fontWeight: 800, color: '#38bdf8' }}>{inspectorData.score_breakdown?.system_health?.score}%</div>
                    </div>
                    <div style={{ background: '#0b1322', padding: '0.6rem 0.8rem', borderRadius: '0.5rem', border: '1px solid #334155' }}>
                      <div style={{ fontSize: '0.68rem', color: '#94a3b8', fontWeight: 700 }}>Pipeline Trace (30%)</div>
                      <div style={{ fontSize: '1.1rem', fontWeight: 800, color: '#a78bfa' }}>{inspectorData.score_breakdown?.pipeline_execution?.score}%</div>
                    </div>
                    <div style={{ background: '#0b1322', padding: '0.6rem 0.8rem', borderRadius: '0.5rem', border: '1px solid #334155' }}>
                      <div style={{ fontSize: '0.68rem', color: '#94a3b8', fontWeight: 700 }}>Data Quality (30%)</div>
                      <div style={{ fontSize: '1.1rem', fontWeight: 800, color: '#34d399' }}>{inspectorData.score_breakdown?.data_quality?.score}%</div>
                    </div>
                    <div style={{ background: '#0b1322', padding: '0.6rem 0.8rem', borderRadius: '0.5rem', border: '1px solid #334155' }}>
                      <div style={{ fontSize: '0.68rem', color: '#94a3b8', fontWeight: 700 }}>Consistency (20%)</div>
                      <div style={{ fontSize: '1.1rem', fontWeight: 800, color: '#f59e0b' }}>{inspectorData.score_breakdown?.data_consistency?.score}%</div>
                    </div>
                  </div>
                </div>

                {/* 2. CHECKPOINTS CP-01 TO CP-30 TIMELINE */}
                <h3 style={{ fontSize: '1.1rem', fontWeight: 800, color: '#f8fafc', marginBottom: '1rem', display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
                  <span>📍 Live 30-Checkpoint Execution Timeline</span>
                  <span style={{ fontSize: '0.75rem', fontWeight: 700, color: '#64748b' }}>({inspectorData.checkpoints?.length || 30} Gates Evaluated)</span>
                </h3>

                <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
                  {(inspectorData.checkpoints || []).map(cp => {
                    const isGreen = cp.status === 'GREEN';
                    const isYellow = cp.status === 'YELLOW';
                    const color = isGreen ? '#10b981' : isYellow ? '#f59e0b' : '#ef4444';
                    const bg = isGreen ? 'rgba(16, 185, 129, 0.05)' : isYellow ? 'rgba(245, 158, 11, 0.05)' : 'rgba(239, 68, 68, 0.05)';
                    return (
                      <div key={cp.checkpoint} style={{ background: bg, border: `1px solid ${color}44`, borderRadius: '0.65rem', padding: '0.85rem 1.1rem' }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.4rem', flexWrap: 'wrap', gap: '0.5rem' }}>
                          <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem' }}>
                            <span style={{ background: color, color: '#000', fontWeight: 900, fontSize: '0.75rem', padding: '0.15rem 0.5rem', borderRadius: '0.3rem' }}>
                              {cp.checkpoint}
                            </span>
                            <span style={{ fontWeight: 800, color: '#f8fafc', fontSize: '0.95rem' }}>
                              {cp.name}
                            </span>
                            <span style={{ fontSize: '0.68rem', fontWeight: 700, color: '#94a3b8', background: '#1e293b', padding: '0.1rem 0.4rem', borderRadius: '0.25rem', border: '1px solid #334155' }}>
                              {cp.stage}
                            </span>
                          </div>

                          <span style={{ fontSize: '0.75rem', fontWeight: 800, color: color, background: `${color}22`, padding: '0.15rem 0.5rem', borderRadius: '0.3rem', border: `1px solid ${color}` }}>
                            {isGreen ? '🟢 PASSED' : isYellow ? '🟡 ATTENTION' : '🔴 BLOCKED'}
                          </span>
                        </div>

                        <div style={{ fontSize: '0.8rem', color: '#cbd5e1', marginBottom: '0.5rem' }}>
                          {cp.description}
                        </div>

                        {/* Transition details grid: Input -> Process -> Output -> Destination -> Verification */}
                        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '0.6rem', background: '#070d18', padding: '0.6rem 0.8rem', borderRadius: '0.4rem', border: '1px solid #1e293b', fontFamily: 'monospace', fontSize: '0.72rem' }}>
                          <div>
                            <span style={{ color: '#64748b' }}>INPUT: </span>
                            <span style={{ color: '#38bdf8' }}>{cp.input}</span>
                          </div>
                          <div>
                            <span style={{ color: '#64748b' }}>PROCESS: </span>
                            <span style={{ color: '#a78bfa' }}>{cp.process}</span>
                          </div>
                          <div>
                            <span style={{ color: '#64748b' }}>OUTPUT: </span>
                            <span style={{ color: '#34d399' }}>{cp.output}</span>
                          </div>
                          <div>
                            <span style={{ color: '#64748b' }}>DESTINATION: </span>
                            <span style={{ color: '#f59e0b' }}>{cp.destination}</span>
                          </div>
                          <div>
                            <span style={{ color: '#64748b' }}>VERIFICATION: </span>
                            <span style={{ color: '#e2e8f0' }}>{cp.verification}</span>
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
