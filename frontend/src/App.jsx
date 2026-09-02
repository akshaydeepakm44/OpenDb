import React, { useState, useEffect } from 'react';
import './index.css';

const API_BASE = import.meta.env.VITE_API_BASE || '/api';

export default function App() {
  // Agent & Operations State
  const [agentStatus, setAgentStatus] = useState(null);
  const [servicesHealth, setServicesHealth] = useState(null);
  const [operationsData, setOperationsData] = useState(null);
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

  // Lead Repository Tab State
  const [leadView, setLeadView] = useState('crawled'); // 'crawled' | 'verified'
  const [crawledDocs, setCrawledDocs] = useState([]);
  const [crawledPage, setCrawledPage] = useState(1);
  const [crawledMeta, setCrawledMeta] = useState({ total: 0, pages: 1 });

  // Poll Services Health and Operations Dashboard Data
  const fetchOperations = async () => {
    const results = await Promise.allSettled([
      fetch(`${API_BASE}/agent/status`).then(r => r.ok ? r.json() : null),
      fetch(`${API_BASE}/health/services`).then(r => r.ok ? r.json() : null),
      fetch(`${API_BASE}/agent/operations`).then(r => r.ok ? r.json() : null)
    ]);

    if (results[0].status === 'fulfilled' && results[0].value) setAgentStatus(results[0].value);
    if (results[1].status === 'fulfilled' && results[1].value) setServicesHealth(results[1].value);
    if (results[2].status === 'fulfilled' && results[2].value) setOperationsData(results[2].value);
  };

  const fetchFilteredEntities = async () => {
    try {
      const params = new URLSearchParams();
      if (searchQuery) params.append('query', searchQuery);
      if (selectedDomain && selectedDomain !== 'All') params.append('domain', selectedDomain);
      if (selectedCountry && selectedCountry !== 'All') params.append('country', selectedCountry);
      if (selectedCompanyTier && selectedCompanyTier !== 'All' && !selectedCompanyTier.includes('All Company Tiers')) {
        params.append('company_tier', selectedCompanyTier);
      }

      const res = await fetch(`${API_BASE}/agent/entities?${params.toString()}`);
      if (res.ok) {
        const data = await res.json();
        setEntitiesList(Array.isArray(data) ? data : []);
      }
    } catch (err) {
      console.error("Error fetching filtered entities:", err);
    }
  };

  const fetchCrawledDocuments = async () => {
    try {
      const params = new URLSearchParams();
      params.append('page', crawledPage);
      params.append('limit', CARDS_PER_PAGE);
      if (searchQuery) params.append('query', searchQuery);
      const res = await fetch(`${API_BASE}/agent/documents?${params.toString()}`);
      if (res.ok) {
        const data = await res.json();
        setCrawledDocs(data.results || []);
        setCrawledMeta({ total: data.total || 0, pages: data.pages || 1 });
      }
    } catch (err) {
      console.error('Error fetching crawled documents:', err);
    }
  };

  const handleResetData = async () => {
    if (!window.confirm("Are you sure you want to clean all stored records from the database?")) return;
    try {
      setLoading(true);
      const res = await fetch(`${API_BASE}/agent/reset`, { method: 'POST' });
      if (res.ok) {
        await fetchOperations();
        await fetchFilteredEntities();
      }
    } catch (err) {
      console.error("Error resetting data:", err);
    } finally {
      setLoading(false);
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

  // Poll every 3 seconds for live dashboard updates
  useEffect(() => {
    fetchOperations();
    fetchFilteredEntities();
    fetchCrawledDocuments();

    const interval = setInterval(() => {
      fetchOperations();
      if (leadView === 'verified') fetchFilteredEntities();
      if (leadView === 'crawled') fetchCrawledDocuments();
    }, 4000);

    return () => clearInterval(interval);
  }, [searchQuery, selectedDomain, selectedCountry, selectedCompanyTier, leadView, crawledPage]);

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

        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
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
      {servicesHealth && (servicesHealth.redis === 'down' || servicesHealth.minio === 'down' || servicesHealth.searxng === 'down') && (
        <div style={{ background: 'rgba(245, 158, 11, 0.12)', border: '1px solid #f59e0b', borderRadius: '0.75rem', padding: '0.85rem 1.25rem', marginBottom: '1.5rem', display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '1rem' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
            <span style={{ fontSize: '1.2rem' }}>🐳</span>
            <div>
              <strong style={{ color: '#fbbf24', fontSize: '0.9rem' }}>Infrastructure Alert: Redis, MinIO or SearXNG is Offline</strong>
              <div style={{ fontSize: '0.8rem', color: '#cbd5e1', marginTop: '0.15rem' }}>
                System is running in safe fallback mode. Connect full infrastructure using Docker Compose.
              </div>
            </div>
          </div>
          <code style={{ background: '#0f172a', color: '#34d399', padding: '0.4rem 0.8rem', borderRadius: '0.375rem', fontSize: '0.85rem', border: '1px solid #334155' }}>
            docker-compose up -d
          </code>
        </div>
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
        <div style={{ background: '#1e293b', borderRadius: '1rem', padding: '1.25rem', border: '1px solid #334155', borderTop: '4px solid #10b981', boxShadow: '0 4px 6px -1px rgba(0, 0, 0, 0.2)' }}>
          <span style={{ fontSize: '0.8rem', fontWeight: 600, color: '#94a3b8', display: 'block', marginBottom: '0.4rem', textTransform: 'uppercase', letterSpacing: '0.05em' }}>PERSISTED COMPANIES</span>
          <div style={{ fontSize: '2.2rem', fontWeight: 800, color: '#34d399', marginBottom: '0.2rem', lineHeight: '1' }}>
            {operationsData?.stat_cards?.persisted_companies ?? 0}
          </div>
          <span style={{ fontSize: '0.72rem', color: '#64748b' }}>PostgreSQL Lake Records</span>
        </div>

        <div style={{ background: '#1e293b', borderRadius: '1rem', padding: '1.25rem', border: '1px solid #334155', borderTop: '4px solid #059669', boxShadow: '0 4px 6px -1px rgba(0, 0, 0, 0.2)' }}>
          <span style={{ fontSize: '0.8rem', fontWeight: 600, color: '#94a3b8', display: 'block', marginBottom: '0.4rem', textTransform: 'uppercase', letterSpacing: '0.05em' }}>VERIFIED LEADS</span>
          <div style={{ fontSize: '2.2rem', fontWeight: 800, color: '#6ee7b7', marginBottom: '0.2rem', lineHeight: '1' }}>
            {operationsData?.stat_cards?.verified_leads ?? 0}
          </div>
          <span style={{ fontSize: '0.72rem', color: '#64748b' }}>Audited Company Leads</span>
        </div>

        <div style={{ background: '#1e293b', borderRadius: '1rem', padding: '1.25rem', border: '1px solid #334155', borderTop: '4px solid #3b82f6', boxShadow: '0 4px 6px -1px rgba(0, 0, 0, 0.2)' }}>
          <span style={{ fontSize: '0.8rem', fontWeight: 600, color: '#94a3b8', display: 'block', marginBottom: '0.4rem', textTransform: 'uppercase', letterSpacing: '0.05em' }}>ACTIVE CRAWL QUEUE</span>
          <div style={{ fontSize: '2.2rem', fontWeight: 800, color: '#60a5fa', marginBottom: '0.2rem', lineHeight: '1' }}>
            {operationsData?.stat_cards?.active_crawl_queue ?? 0}
          </div>
          <span style={{ fontSize: '0.72rem', color: '#64748b' }}>Celery Redis Queue Depth</span>
        </div>

        <div style={{ background: '#1e293b', borderRadius: '1rem', padding: '1.25rem', border: '1px solid #334155', borderTop: '4px solid #a78bfa', boxShadow: '0 4px 6px -1px rgba(0, 0, 0, 0.2)' }}>
          <span style={{ fontSize: '0.8rem', fontWeight: 600, color: '#94a3b8', display: 'block', marginBottom: '0.4rem', textTransform: 'uppercase', letterSpacing: '0.05em' }}>RAW DOCUMENTS</span>
          <div style={{ fontSize: '2.2rem', fontWeight: 800, color: '#c084fc', marginBottom: '0.2rem', lineHeight: '1' }}>
            {operationsData?.stat_cards?.crawled_documents ?? 0}
          </div>
          <span style={{ fontSize: '0.72rem', color: '#64748b' }}>Ingested Page Documents</span>
        </div>

        <div style={{ background: '#1e293b', borderRadius: '1rem', padding: '1.25rem', border: '1px solid #334155', borderTop: '4px solid #f59e0b', boxShadow: '0 4px 6px -1px rgba(0, 0, 0, 0.2)' }}>
          <span style={{ fontSize: '0.8rem', fontWeight: 600, color: '#94a3b8', display: 'block', marginBottom: '0.4rem', textTransform: 'uppercase', letterSpacing: '0.05em' }}>STORAGE USAGE</span>
          <div style={{ fontSize: '1.1rem', fontWeight: 800, color: '#fbbf24', marginBottom: '0.4rem', marginTop: '0.5rem', lineHeight: '1.2' }}>
            {operationsData?.stat_cards?.storage_usage?.formatted || "MinIO: 0 objs / Postgres: 12 MB"}
          </div>
          <span style={{ fontSize: '0.72rem', color: '#64748b' }}>S3 Object Count & DB Size</span>
        </div>
      </div>


      {/* 3. LIVE CRAWL ACTIVITY MONITOR */}
      <div className="card" style={{ marginBottom: '2rem' }}>
        {/* Tab Bar */}
        <div style={{ display: 'flex', gap: '0.5rem', borderBottom: '1px solid #334155', paddingBottom: '0.75rem', marginBottom: '1rem', flexWrap: 'wrap' }}>
          {[
            { key: 'activity', label: `⚡ Live Crawl Activity (${operationsData?.crawl_activity_stream?.length || 0})`, color: '#a78bfa' },
            { key: 'searxng', label: `🔍 Search Logs (${operationsData?.search_stream?.length || 0})`, color: '#38bdf8' },
            { key: 'failures', label: `⛔ Failures & Rejections (${operationsData?.failure_stream?.length || 0})`, color: '#ef4444' },
            { key: 'feedback', label: '🏢 Company Tier Intelligence', color: '#10b981' },
          ].map(tab => (
            <button key={tab.key}
              onClick={() => { setActiveStreamTab(tab.key); if (tab.key === 'feedback') fetchFeedback(); }}
              style={{
                padding: '0.45rem 1.1rem', borderRadius: '0.5rem', border: 'none', cursor: 'pointer', fontWeight: 700, fontSize: '0.82rem',
                background: activeStreamTab === tab.key ? tab.color : '#1e293b',
                color: activeStreamTab === tab.key ? '#fff' : '#64748b',
                boxShadow: activeStreamTab === tab.key ? `0 0 10px ${tab.color}55` : 'none',
                transition: 'all 0.2s',
              }}
            >{tab.label}</button>
          ))}
        </div>

        {/* LIVE CRAWL ACTIVITY STREAM */}
        {activeStreamTab === 'activity' && (
          <div style={{ maxHeight: '300px', overflowY: 'auto', background: '#060e1e', padding: '0.75rem', borderRadius: '0.5rem', fontFamily: 'monospace', fontSize: '0.8rem' }}>
            {(!operationsData?.crawl_activity_stream || operationsData.crawl_activity_stream.length === 0) ? (
              <div style={{ color: '#475569', padding: '1rem 0' }}>
                <span style={{ color: '#38bdf8' }}>$</span> agent --listen --mode=autonomous<br/>
                <span style={{ color: '#64748b' }}>Waiting for crawl activity events... Press <strong style={{ color: '#10b981' }}>RUN</strong> to start.</span>
              </div>
            ) : (
              operationsData.crawl_activity_stream.map(ev => {
                const stageIcons = { SEARCH: '🔍', CRAWL: '🌐', EXTRACT: '⚗️', FILTER: '🚫', VERIFY: '✅', DUPLICATE: '♻️' };
                const statusColors = { OK: '#34d399', QUEUED: '#a78bfa', FILTERED: '#f59e0b', DUPLICATE: '#64748b', ERROR: '#f87171', EMPTY: '#94a3b8' };
                return (
                  <div key={ev.id} style={{ marginBottom: '0.3rem', paddingBottom: '0.3rem', borderBottom: '1px solid #0f172a', display: 'flex', alignItems: 'flex-start', gap: '0.5rem' }}>
                    <span style={{ flexShrink: 0, fontSize: '0.75rem', color: '#334155', width: '54px' }}>
                      {ev.timestamp ? new Date(ev.timestamp).toLocaleTimeString('en', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' }) : ''}
                    </span>
                    <span style={{ flexShrink: 0, fontSize: '0.72rem', fontWeight: 800, padding: '0.1rem 0.4rem', borderRadius: '0.25rem', background: `${ev.stage_color}22`, color: ev.stage_color, border: `1px solid ${ev.stage_color}44`, width: '60px', textAlign: 'center' }}>
                      {stageIcons[ev.stage] || ''} {ev.stage}
                    </span>
                    <span style={{ flexShrink: 0, fontSize: '0.72rem', fontWeight: 700, color: statusColors[ev.status] || '#94a3b8', width: '70px' }}>
                      [{ev.status}]
                    </span>
                    <span style={{ color: '#94a3b8', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {ev.entity_name && <strong style={{ color: '#f8fafc' }}>{ev.entity_name} — </strong>}
                      <span style={{ color: '#60a5fa' }}>{ev.url?.length > 60 ? ev.url.slice(0, 60) + '…' : ev.url}</span>
                      {ev.message && <span style={{ color: '#475569' }}> | {ev.message}</span>}
                    </span>
                  </div>
                );
              })
            )}
          </div>
        )}

        {/* SEARXNG SEARCH LOGS */}
        {activeStreamTab === 'searxng' && (
          <div style={{ maxHeight: '260px', overflowY: 'auto', background: '#060e1e', padding: '0.75rem', borderRadius: '0.5rem', fontFamily: 'monospace', fontSize: '0.82rem' }}>
            {(!operationsData?.search_stream || operationsData.search_stream.length === 0) ? (
              <div style={{ color: '#475569' }}>No SearXNG search events yet. Press <strong style={{ color: '#10b981' }}>RUN</strong> to start discovery.</div>
            ) : (
              operationsData.search_stream.map(item => (
                <div key={item.id} style={{ marginBottom: '0.4rem', borderBottom: '1px solid #0f172a', paddingBottom: '0.3rem', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <span>
                    <strong style={{ color: item.is_fallback ? '#f59e0b' : '#38bdf8' }}>
                      {item.is_fallback ? '[FALLBACK]' : '[SEARXNG]'}
                    </strong>{' '}
                    <span style={{ color: '#a78bfa' }}>{item.domain}</span>{' › '}
                    <span style={{ color: '#f8fafc' }}>{item.keyword}</span>
                    <span style={{ color: '#64748b' }}> → {item.sources_found} URLs</span>
                  </span>
                  <span style={{ color: '#334155', fontSize: '0.72rem', whiteSpace: 'nowrap', marginLeft: '1rem' }}>
                    {item.timestamp ? new Date(item.timestamp).toLocaleTimeString() : ''}
                  </span>
                </div>
              ))
            )}
          </div>
        )}

        {/* FAILURES & REJECTIONS */}
        {activeStreamTab === 'failures' && (
          <div style={{ maxHeight: '260px', overflowY: 'auto', background: '#060e1e', padding: '0.75rem', borderRadius: '0.5rem', fontFamily: 'monospace', fontSize: '0.82rem' }}>
            {(!operationsData?.failure_stream || operationsData.failure_stream.length === 0) ? (
              <div style={{ color: '#34d399' }}>✓ No failures or rejections logged. Pipeline running cleanly.</div>
            ) : (
              operationsData.failure_stream.map(ev => (
                <div key={ev.id} style={{ marginBottom: '0.35rem', borderBottom: '1px solid #0f172a', paddingBottom: '0.3rem' }}>
                  <span style={{ color: ev.status === 'DUPLICATE' ? '#64748b' : '#f87171', fontWeight: 700 }}>
                    [{ev.status}][{ev.stage}]
                  </span>{' '}
                  <span style={{ color: '#94a3b8' }}>{ev.url?.length > 50 ? ev.url.slice(0, 50) + '…' : ev.url}</span>
                  {ev.message && <span style={{ color: '#475569' }}> — {ev.message}</span>}
                  <span style={{ color: '#334155', marginLeft: '0.5rem', fontSize: '0.72rem' }}>
                    {ev.timestamp ? new Date(ev.timestamp).toLocaleTimeString() : ''}
                  </span>
                </div>
              ))
            )}
          </div>
        )}

        {/* COMPANY TIER INTELLIGENCE */}
        {activeStreamTab === 'feedback' && (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '1rem' }}>
            {(feedbackData?.company_tier_taxonomy || [
              { tier: "Early-Stage Startups (1-20)", icon: "🌱", count: 0, avg_confidence: "N/A", description: "Seed, Series-A & stealth stage ventures." },
              { tier: "Growth SMBs (20-100)", icon: "🚀", count: 0, avg_confidence: "N/A", description: "Fast-scaling tech & product companies." },
              { tier: "Mid-Market Challengers (100-1,000)", icon: "🏢", count: 0, avg_confidence: "N/A", description: "Established corporate market leaders." },
              { tier: "Enterprise Leaders (1,000+)", icon: "🏛️", count: 0, avg_confidence: "N/A", description: "Fortune 2000 multinational organizations." }
            ]).map((t, i) => (
              <div key={i} style={{ background: '#0f172a', borderRadius: '0.75rem', border: '1px solid #334155', padding: '1.1rem' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.5rem' }}>
                  <span style={{ fontSize: '1.3rem' }}>{t.icon}</span>
                  <span style={{ fontSize: '0.72rem', fontWeight: 700, color: '#34d399', background: 'rgba(52,211,153,0.1)', padding: '0.15rem 0.5rem', borderRadius: '9999px' }}>
                    {t.avg_confidence} precision
                  </span>
                </div>
                <div style={{ fontSize: '0.85rem', fontWeight: 800, color: '#f8fafc', marginBottom: '0.3rem' }}>{t.tier}</div>
                <div style={{ fontSize: '0.75rem', color: '#64748b', marginBottom: '0.75rem' }}>{t.description}</div>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', borderTop: '1px solid #1e293b', paddingTop: '0.6rem' }}>
                  <span style={{ fontSize: '0.82rem', color: '#60a5fa', fontWeight: 700 }}>{t.count} leads</span>
                  <button onClick={() => setSelectedCompanyTier(t.tier)}
                    style={{ padding: '0.25rem 0.6rem', background: '#3b82f6', color: 'white', border: 'none', borderRadius: '0.375rem', fontSize: '0.72rem', fontWeight: 700, cursor: 'pointer' }}>
                    Filter
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* 4. LEAD REPOSITORY — CRAWLED + VERIFIED TAB VIEWS */}
      <div className="card">
        {/* Header row */}
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1.25rem', flexWrap: 'wrap', gap: '1rem' }}>
          <div>
            <h2 className="card-title" style={{ margin: 0 }}>COMPANY LEAD DISCOVERY PANELS</h2>
            <div style={{ fontSize: '0.85rem', color: '#64748b', marginTop: '0.2rem' }}>
              {leadView === 'crawled'
                ? <>Showing <strong style={{ color: '#f59e0b' }}>{crawledMeta.total?.toLocaleString() || 0}</strong> Crawled Data Cards — Parallel Search & Playwright Engine</>
                : <>Showing <strong style={{ color: '#10b981' }}>{entitiesList.length}</strong> Verified Data Cards — Haystack Continuous Enrichment Agent</>}
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
              ⚡ Crawled Data Cards ({(crawledMeta.total || 0).toLocaleString()})
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
              ✅ Verified Data Cards ({entitiesList.length})
            </button>
          </div>

          {/* Pagination */}
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
            <button
              onClick={() => leadView === 'crawled' ? setCrawledPage(p => Math.max(1, p - 1)) : setCurrentPage(p => Math.max(1, p - 1))}
              disabled={(leadView === 'crawled' ? crawledPage : currentPage) === 1}
              style={{ padding: '0.4rem 1rem', background: '#1e293b', color: 'white', border: '1px solid #334155', borderRadius: '0.375rem', cursor: 'pointer', fontWeight: 700 }}
            >◀ Prev</button>
            <span style={{ color: '#94a3b8', fontSize: '0.9rem', whiteSpace: 'nowrap' }}>
              Page {leadView === 'crawled' ? crawledPage : currentPage} of {leadView === 'crawled' ? crawledMeta.pages || 1 : Math.max(1, Math.ceil(entitiesList.length / CARDS_PER_PAGE))}
            </span>
            <button
              onClick={() => leadView === 'crawled' ? setCrawledPage(p => Math.min(crawledMeta.pages || 1, p + 1)) : setCurrentPage(p => Math.min(Math.ceil(entitiesList.length / CARDS_PER_PAGE), p + 1))}
              style={{ padding: '0.4rem 1rem', background: '#3b82f6', color: 'white', border: '1px solid #334155', borderRadius: '0.375rem', cursor: 'pointer', fontWeight: 700 }}
            >Next ▶</button>
          </div>
        </div>

        {/* Search bar (common for both views) */}
        <div style={{ display: 'grid', gridTemplateColumns: '1.8fr 1fr 1fr 1.4fr', gap: '1rem', marginBottom: '1.5rem' }}>
          <div>
            <label className="data-label">Full-Text Search</label>
            <input type="text" className="search-input" style={{ maxWidth: '100%', borderRadius: '0.5rem', padding: '0.6rem 1rem', fontSize: '0.95rem' }}
              placeholder="Search by company name or URL..."
              value={searchQuery} onChange={(e) => { setSearchQuery(e.target.value); setCurrentPage(1); setCrawledPage(1); }} />
          </div>
          <div>
            <label className="data-label">Filter Industry / Domain</label>
            <select className="search-input" style={{ maxWidth: '100%', borderRadius: '0.5rem', padding: '0.6rem 1rem', fontSize: '0.95rem' }}
              value={selectedDomain} onChange={(e) => { setSelectedDomain(e.target.value); setCurrentPage(1); }}>
              <option value="All">All Domains</option>
              {Array.isArray(operationsData?.filter_options?.domains) && operationsData.filter_options.domains.map(d => <option key={d} value={d}>{d}</option>)}
            </select>
          </div>
          <div>
            <label className="data-label">Filter Country Region</label>
            <select className="search-input" style={{ maxWidth: '100%', borderRadius: '0.5rem', padding: '0.6rem 1rem', fontSize: '0.95rem' }}
              value={selectedCountry} onChange={(e) => { setSelectedCountry(e.target.value); setCurrentPage(1); }}>
              <option value="All">All Countries</option>
              {Array.isArray(operationsData?.filter_options?.countries) && operationsData.filter_options.countries.map(c => <option key={c} value={c}>{c}</option>)}
            </select>
          </div>
          <div>
            <label className="data-label" style={{ color: '#38bdf8' }}>Filter Company Tier & Level</label>
            <select className="search-input" style={{ maxWidth: '100%', borderRadius: '0.5rem', padding: '0.6rem 1rem', fontSize: '0.95rem', background: '#0f172a', color: '#38bdf8', border: '1px solid #0284c7', fontWeight: 700 }}
              value={selectedCompanyTier} onChange={(e) => { setSelectedCompanyTier(e.target.value); setCurrentPage(1); }}>
              <option value="All">🏢 All Company Tiers & Ranges</option>
              <option value="Early-Stage Startups (1-20)">🌱 Early-Stage Startups (1-20)</option>
              <option value="Growth SMBs (20-100)">🚀 Growth SMBs (20-100)</option>
              <option value="Mid-Market Challengers (100-1,000)">🏢 Mid-Market Challengers (100-1,000)</option>
              <option value="Enterprise Leaders (1,000+)">🏛️ Enterprise Leaders (1,000+)</option>
            </select>
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
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(270px, 1fr))', gap: '1rem' }}>
              {crawledDocs.map((doc) => {
                const initial = (doc.canonical_name || doc.domain || '?')[0].toUpperCase();
                const isVerified = doc.status === 'Verified';
                const isQueued = doc.status === 'Queued';
                const borderColor = isVerified ? '#10b981' : isQueued ? '#3b82f6' : '#a78bfa';
                const bgColor = ['#3b82f6','#a78bfa','#10b981','#f59e0b','#ef4444','#06b6d4'][Math.abs(doc.domain?.charCodeAt(0) || 65) % 6];
                return (
                  <div
                    key={doc.id}
                    style={{
                      background: 'linear-gradient(145deg, #1a2332, #0f172a)',
                      border: `1px solid ${borderColor}`,
                      borderRadius: '0.875rem',
                      padding: '1rem',
                      cursor: 'pointer',
                      transition: 'all 0.2s ease',
                      position: 'relative',
                    }}
                    onClick={() => setSelectedDocumentId(doc.id)}
                    onMouseEnter={e => { e.currentTarget.style.borderColor = isVerified ? '#34d399' : '#60a5fa'; e.currentTarget.style.transform = 'translateY(-2px)'; }}
                    onMouseLeave={e => { e.currentTarget.style.borderColor = borderColor; e.currentTarget.style.transform = 'none'; }}
                  >
                    {/* Status badge top-right */}
                    <div style={{
                      position: 'absolute', top: '0.75rem', right: '0.75rem',
                      fontSize: '0.65rem', fontWeight: 800, padding: '0.18rem 0.5rem', borderRadius: '9999px',
                      background: isVerified ? 'rgba(16,185,129,0.2)' : isQueued ? 'rgba(59,130,246,0.15)' : 'rgba(167,139,250,0.15)',
                      color: isVerified ? '#34d399' : isQueued ? '#60a5fa' : '#fbbf24',
                      border: `1px solid ${isVerified ? '#10b981' : isQueued ? '#3b82f6' : '#f59e0b'}`,
                    }}>
                      {isVerified ? '✅ Verified' : isQueued ? '🔄 Queued' : '⚡ Raw Ingested'}
                    </div>

                    {/* Logo initial + name */}
                    <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginBottom: '0.75rem', paddingRight: '5rem' }}>
                      <div style={{
                        width: '38px', height: '38px', borderRadius: '0.5rem', flexShrink: 0,
                        background: bgColor, display: 'flex', alignItems: 'center', justifyContent: 'center',
                        fontWeight: 900, fontSize: '1.1rem', color: '#fff'
                      }}>{initial}</div>
                      <div style={{ overflow: 'hidden' }}>
                        <div style={{ fontWeight: 800, fontSize: '0.9rem', color: '#f8fafc', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                          {doc.canonical_name || doc.domain}
                        </div>
                        <a href={doc.url} target="_blank" rel="noreferrer" onClick={e => e.stopPropagation()}
                          style={{ fontSize: '0.72rem', color: '#60a5fa', textDecoration: 'none', display: 'flex', alignItems: 'center', gap: '0.2rem' }}>
                          🔗 {doc.domain}
                        </a>
                      </div>
                    </div>

                    {/* Data rows */}
                    <div style={{ fontSize: '0.78rem', color: '#94a3b8', lineHeight: '1.7' }}>
                      <div>📍 Location: <span style={{ color: '#f8fafc' }}>{doc.country}</span></div>
                      <div>🏭 Industry: <span style={{ color: '#f8fafc' }}>{doc.industry}</span></div>
                      <div>📊 Size Tier: <span style={{ color: '#f8fafc' }}>{doc.company_tier === 'Unknown' ? 'Unknown' : doc.company_tier}</span></div>
                    </div>

                    {/* Action buttons */}
                    <div style={{ display: 'flex', gap: '0.5rem', marginTop: '0.85rem', borderTop: '1px solid #1e293b', paddingTop: '0.75rem' }}>
                      <button
                        onClick={e => { e.stopPropagation(); setSelectedDocumentId(doc.id); }}
                        style={{
                          flex: 1, padding: '0.4rem 0', background: '#1e293b', border: '1px solid #334155',
                          color: '#38bdf8', borderRadius: '0.375rem', fontSize: '0.72rem', fontWeight: 700, cursor: 'pointer'
                        }}
                      >
                        📄 Crawled Data ↗
                      </button>
                      {isVerified ? (
                        <button
                          onClick={e => { e.stopPropagation(); setSelectedEntityId(doc.verified_entity_id); }}
                          style={{ flex: 1, padding: '0.4rem 0', background: 'linear-gradient(90deg, #10b981, #059669)', border: 'none', color: '#fff', borderRadius: '0.375rem', fontSize: '0.72rem', fontWeight: 700, cursor: 'pointer' }}
                        >
                          View Dossier ↗
                        </button>
                      ) : (
                        <button
                          onClick={e => { e.stopPropagation(); setSelectedDocumentId(doc.id); }}
                          style={{
                            flex: 1.4, padding: '0.4rem 0', background: 'linear-gradient(90deg, #7c3aed, #4f46e5)', border: 'none',
                            color: '#fff', borderRadius: '0.375rem', fontSize: '0.72rem', fontWeight: 700, cursor: 'pointer'
                          }}
                        >
                          ⚡ View Details ↗
                        </button>
                      )}
                    </div>
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
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: '1.25rem' }}>
              {entitiesList.slice((currentPage - 1) * CARDS_PER_PAGE, currentPage * CARDS_PER_PAGE).map((ent) => {
                const conf = Math.round((ent.confidence || 0.85) * 100);
                const confColor = conf >= 90 ? '#10b981' : conf >= 75 ? '#3b82f6' : '#f59e0b';
                const tierIcon = ent.company_tier?.includes('Enterprise') ? '🏛️' : ent.company_tier?.includes('Mid') ? '🏢' : ent.company_tier?.includes('Growth') ? '🚀' : '🌱';
                let domain = '';
                try { domain = new URL(ent.url.startsWith('http') ? ent.url : 'https://' + ent.url).hostname.replace('www.', ''); } catch {}
                const bgColor = ['#3b82f6','#a78bfa','#10b981','#f59e0b','#ef4444','#06b6d4'][Math.abs((domain.charCodeAt(0) || 65)) % 6];
                const initial = (ent.canonical_name || domain || '?')[0].toUpperCase();
                return (
                  <div
                    key={ent.id}
                    onClick={() => setSelectedEntityId(ent.id)}
                    style={{
                      background: 'linear-gradient(145deg, #1e293b, #0f172a)',
                      border: '1px solid #10b981',
                      borderRadius: '1rem',
                      padding: '1.25rem',
                      cursor: 'pointer',
                      transition: 'all 0.25s ease',
                      position: 'relative',
                      overflow: 'hidden',
                    }}
                    onMouseEnter={e => { e.currentTarget.style.borderColor = '#34d399'; e.currentTarget.style.transform = 'translateY(-3px)'; e.currentTarget.style.boxShadow = '0 12px 30px rgba(16,185,129,0.2)'; }}
                    onMouseLeave={e => { e.currentTarget.style.borderColor = '#10b981'; e.currentTarget.style.transform = 'none'; e.currentTarget.style.boxShadow = 'none'; }}
                  >
                    {/* Confidence badge */}
                    <div style={{ position: 'absolute', top: '0.85rem', right: '0.85rem', fontSize: '0.65rem', fontWeight: 800, padding: '0.18rem 0.5rem', borderRadius: '9999px', background: 'rgba(16,185,129,0.2)', color: '#34d399', border: '1px solid #10b981' }}>
                      ✅ Verified ({conf}/100)
                    </div>

                    {/* Logo + Name */}
                    <div style={{ display: 'flex', alignItems: 'center', gap: '0.85rem', marginBottom: '0.85rem', paddingRight: '5rem' }}>
                      <div style={{ width: '38px', height: '38px', borderRadius: '0.5rem', flexShrink: 0, background: bgColor, display: 'flex', alignItems: 'center', justifyContent: 'center', fontWeight: 900, fontSize: '1.1rem', color: '#fff' }}>{initial}</div>
                      <div style={{ overflow: 'hidden' }}>
                        <div style={{ fontWeight: 800, fontSize: '0.95rem', color: '#f8fafc', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{ent.canonical_name}</div>
                        <a href={ent.url} target="_blank" rel="noreferrer" onClick={e => e.stopPropagation()} style={{ fontSize: '0.72rem', color: '#60a5fa', textDecoration: 'none' }}>🔗 {domain}</a>
                      </div>
                    </div>

                    {/* Data rows */}
                    <div style={{ fontSize: '0.78rem', color: '#94a3b8', lineHeight: '1.7' }}>
                      <div>📍 Location: <span style={{ color: '#f8fafc' }}>{ent.country}</span></div>
                      <div>🏭 Industry: <span style={{ color: '#f8fafc' }}>{ent.domain}</span></div>
                      <div>📊 Size Tier: <span style={{ color: '#38bdf8' }}>{tierIcon} {ent.company_tier || 'Growth SMBs (20-100)'}</span></div>
                    </div>

                    {/* Description */}
                    <div style={{ fontSize: '0.78rem', color: '#64748b', lineHeight: '1.4', margin: '0.65rem 0', display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>
                      {ent.description || `${ent.canonical_name} operates in the ${ent.domain || 'Technology'} sector.`}
                    </div>

                    {/* Action footer */}
                    <div style={{ display: 'flex', gap: '0.5rem', borderTop: '1px solid #1e293b', paddingTop: '0.75rem' }}>
                      <button style={{ flex: 1, padding: '0.4rem 0', background: '#1e293b', border: '1px solid #334155', color: '#38bdf8', borderRadius: '0.375rem', fontSize: '0.72rem', fontWeight: 700, cursor: 'pointer' }}>
                        DomCop 10M
                      </button>
                      <button
                        onClick={e => { e.stopPropagation(); setSelectedEntityId(ent.id); }}
                        style={{ flex: 1.4, padding: '0.4rem 0', background: 'linear-gradient(90deg, #10b981, #059669)', border: 'none', color: '#fff', borderRadius: '0.375rem', fontSize: '0.72rem', fontWeight: 700, cursor: 'pointer' }}
                      >
                        View Dossier ↗
                      </button>
                    </div>
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
                  <div style={{ width: '52px', height: '52px', borderRadius: '0.75rem', background: '#3b82f6', display: 'flex', alignItems: 'center', justifyContent: 'center', fontWeight: 900, fontSize: '1.5rem', color: '#fff', flexShrink: 0 }}>
                    {(documentDetail.canonical_name || documentDetail.domain || '?')[0].toUpperCase()}
                  </div>
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

                {/* Extracted Facts & Signals (if any) */}
                {documentDetail.extracted_facts && documentDetail.extracted_facts.length > 0 && (
                  <div style={{ marginBottom: '1.5rem' }}>
                    <h3 style={{ color: '#34d399', fontSize: '1.1rem', marginTop: 0, marginBottom: '0.75rem' }}>
                      Discovered Company Signals & Extracted Facts ({documentDetail.extracted_facts.length})
                    </h3>
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(240px, 1fr))', gap: '0.75rem' }}>
                      {documentDetail.extracted_facts.map((fact, i) => (
                        <div key={i} style={{ background: '#0f172a', padding: '0.75rem', borderRadius: '0.5rem', border: '1px solid #1e293b' }}>
                          <span style={{ fontSize: '0.7rem', color: '#94a3b8', textTransform: 'uppercase', fontWeight: 700 }}>{fact.field}</span>
                          <div style={{ color: '#f8fafc', fontWeight: 700, fontSize: '0.9rem', marginTop: '0.15rem' }}>{fact.value}</div>
                        </div>
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
                  {documentDetail.verified_entity_id && (
                    <button
                      onClick={() => {
                        const entId = documentDetail.verified_entity_id;
                        setSelectedDocumentId(null);
                        setSelectedEntityId(entId);
                      }}
                      style={{ padding: '0.65rem 1.25rem', background: 'linear-gradient(90deg, #10b981, #059669)', border: 'none', color: '#fff', borderRadius: '0.5rem', fontWeight: 700, cursor: 'pointer' }}
                    >
                      View Verified Dossier ↗
                    </button>
                  )}
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
        <div style={{ position: 'fixed', top: 0, left: 0, right: 0, bottom: 0, background: 'rgba(15, 23, 42, 0.85)', backdropFilter: 'blur(8px)', zIndex: 100, display: 'flex', justifyContent: 'center', alignItems: 'center', padding: '2rem' }}>
          <div style={{ background: '#1e293b', border: '1px solid #334155', borderRadius: '1rem', width: '100%', maxWidth: '1100px', maxHeight: '90vh', overflowY: 'auto', padding: '2rem', boxShadow: '0 25px 50px -12px rgba(0,0,0,0.5)', position: 'relative' }}>
            
            {/* Close Button */}
            <button
              onClick={() => setSelectedEntityId(null)}
              style={{ position: 'absolute', top: '1.5rem', right: '1.5rem', background: '#0f172a', border: '1px solid #334155', color: 'white', borderRadius: '50%', width: '36px', height: '36px', cursor: 'pointer', fontSize: '1.2rem', fontWeight: 700 }}
            >
              ✕
            </button>

            {loadingDetail || !entityDetail ? (
              <div style={{ textAlign: 'center', padding: '4rem 0' }}>
                <div className="spinner" style={{ margin: '0 auto 1rem auto' }}></div>
                <div>Synthesizing entity audit & evidence from OpenDB storage...</div>
              </div>
            ) : (
              <div>
                {/* Modal Header */}
                <div style={{ display: 'flex', alignItems: 'center', gap: '1.5rem', borderBottom: '1px solid #334155', paddingBottom: '1.5rem', marginBottom: '1.5rem' }}>
                  <img
                    src={entityDetail.logo_url}
                    alt="Logo"
                    onError={(e) => { e.target.style.display = 'none'; }}
                    style={{ width: '56px', height: '56px', borderRadius: '0.75rem', background: '#0f172a', padding: '0.5rem', border: '1px solid #334155' }}
                  />
                  <div>
                    <h2 style={{ margin: 0, fontSize: '2rem', color: '#f8fafc' }}>{entityDetail.canonical_name}</h2>
                    <div style={{ display: 'flex', gap: '1rem', alignItems: 'center', marginTop: '0.4rem' }}>
                      <span className="tag">{entityDetail.domain}</span>
                      <a href={entityDetail.official_website} target="_blank" rel="noreferrer" style={{ color: '#60a5fa', fontSize: '0.95rem' }}>
                        {entityDetail.official_website}
                      </a>
                    </div>
                  </div>
                </div>

                {/* Two-Column Grid Layout */}
                <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: '2rem' }}>
                  
                  {/* Left Column — Narrative & Content */}
                  <div>
                    {/* Business Overview */}
                    <div style={{ marginBottom: '1.5rem' }}>
                      <h3 style={{ color: '#60a5fa', fontSize: '1.1rem', marginTop: 0, marginBottom: '0.5rem' }}>Business Overview & Fact Synthesis</h3>
                      <p style={{ lineHeight: '1.6', color: '#cbd5e1', background: '#0f172a', padding: '1rem', borderRadius: '0.5rem', borderLeft: '4px solid #3b82f6' }}>
                        {entityDetail.summary}
                      </p>
                      <div style={{ fontSize: '0.75rem', color: '#64748b', marginTop: '0.25rem' }}>
                        Audited & Cached at: {new Date(entityDetail.summary_generated_at).toLocaleString()}
                      </div>
                    </div>

                    {/* Technology Stack */}
                    <div style={{ marginBottom: '1.5rem' }}>
                      <h3 style={{ color: '#60a5fa', fontSize: '1.1rem', marginBottom: '0.5rem' }}>Technology Stack</h3>
                      {entityDetail.technology_stack.length === 0 ? (
                        <div style={{ color: '#64748b', italic: 'true' }}>No technology signals discovered</div>
                      ) : (
                        <div className="tag-list">
                          {entityDetail.technology_stack.map((tech, i) => (
                            <span key={i} className="tag" style={{ background: 'rgba(16, 185, 129, 0.1)', color: '#34d399', border: '1px solid rgba(16, 185, 129, 0.2)' }}>
                              {tech}
                            </span>
                          ))}
                        </div>
                      )}
                    </div>

                    {/* Decision Makers & Leadership */}
                    <div style={{ marginBottom: '1.5rem' }}>
                      <h3 style={{ color: '#60a5fa', fontSize: '1.1rem', marginBottom: '0.5rem' }}>
                        Decision Makers & Leadership ({entityDetail.decision_makers.length})
                      </h3>
                      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
                        {entityDetail.decision_makers.map((person, idx) => (
                          <div key={idx} style={{ background: '#0f172a', padding: '0.75rem 1rem', borderRadius: '0.5rem', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                            <div>
                              <div style={{ fontWeight: 700, color: '#f8fafc' }}>{person.name}</div>
                              <div style={{ fontSize: '0.85rem', color: '#94a3b8' }}>{person.title}</div>
                            </div>
                            <a
                              href={person.linkedin_search_url}
                              target="_blank"
                              rel="noreferrer"
                              style={{ padding: '0.4rem 0.8rem', background: '#0077b5', color: 'white', borderRadius: '0.25rem', fontSize: '0.8rem', fontWeight: 600, textDecoration: 'none' }}
                            >
                              Search LinkedIn
                            </a>
                          </div>
                        ))}
                      </div>
                    </div>

                    {/* Crawled Subpages & Source Vault */}
                    <div>
                      <h3 style={{ color: '#60a5fa', fontSize: '1.1rem', marginBottom: '0.5rem' }}>Crawled Subpages & MinIO Raw Vault</h3>
                      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
                        {entityDetail.crawled_subpages.map((pg, idx) => (
                          <div key={idx} style={{ background: '#0f172a', padding: '0.75rem', borderRadius: '0.5rem', fontSize: '0.85rem' }}>
                            <div style={{ fontWeight: 600, color: '#38bdf8' }}>{pg.title}</div>
                            <div style={{ color: '#64748b', margin: '0.2rem 0' }}>{pg.url}</div>
                            <div style={{ fontFamily: 'monospace', fontSize: '0.75rem', color: '#a78bfa' }}>
                              MinIO Vault: {pg.minio_raw_path}
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  </div>

                  {/* Right Sidebar — Firmographic Facts & Quality Score */}
                  <div style={{ background: '#0f172a', padding: '1.25rem', borderRadius: '0.75rem', height: 'fit-content' }}>
                    <h3 style={{ color: '#34d399', fontSize: '1.1rem', marginTop: 0, marginBottom: '1rem', borderBottom: '1px solid #334155', paddingBottom: '0.5rem' }}>
                      Firmographics & Audit
                    </h3>

                    {/* Lead Quality Score */}
                    <div style={{ marginBottom: '1.25rem', textAlign: 'center', background: '#1e293b', padding: '1rem', borderRadius: '0.5rem' }}>
                      <span className="data-label">LEAD QUALITY SCORE</span>
                      <div style={{ fontSize: '2.5rem', fontWeight: 900, color: '#10b981' }}>
                        {entityDetail.lead_quality_score} / 100
                      </div>
                      <div style={{ fontSize: '0.7rem', color: '#64748b', marginTop: '0.25rem' }} title={entityDetail.score_methodology}>
                        Methodology: 40% Completeness + 40% Confidence + 20% Recency ℹ️
                      </div>
                    </div>

                    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.85rem', fontSize: '0.9rem' }}>
                      <div>
                        <span className="data-label">Headquarters</span>
                        <div style={{ color: '#f8fafc', fontWeight: 600 }}>{entityDetail.firmographics.headquarters}</div>
                      </div>

                      <div>
                        <span className="data-label">Industry</span>
                        <div style={{ color: '#f8fafc', fontWeight: 600 }}>{entityDetail.firmographics.industry}</div>
                      </div>

                      <div>
                        <span className="data-label">Company Size</span>
                        <div style={{ color: '#f8fafc', fontWeight: 600 }}>{entityDetail.firmographics.company_size}</div>
                      </div>

                      <div>
                        <span className="data-label">Revenue / Funding</span>
                        <div style={{ color: '#f8fafc', fontWeight: 600 }}>{entityDetail.firmographics.revenue_funding}</div>
                      </div>

                      <div>
                        <span className="data-label">Verified Contact Emails</span>
                        {entityDetail.firmographics.verified_emails.length === 0 ? (
                          <div style={{ color: '#64748b', italic: 'true' }}>None discovered</div>
                        ) : (
                          entityDetail.firmographics.verified_emails.map((em, i) => (
                            <div key={i} style={{ color: '#60a5fa', fontWeight: 600 }}>{em}</div>
                          ))
                        )}
                      </div>

                      <div style={{ borderTop: '1px solid #334155', paddingTop: '0.85rem' }}>
                        <span className="data-label">Extraction Provenance</span>
                        <div style={{ fontSize: '0.8rem', color: '#94a3b8' }}>
                          Source: {entityDetail.provenance.source_type}<br />
                          Confidence: {(entityDetail.provenance.confidence * 100).toFixed(0)}%
                        </div>
                      </div>

                      <a
                        href={entityDetail.official_website}
                        target="_blank"
                        rel="noreferrer"
                        className="search-btn"
                        style={{ display: 'block', textAlign: 'center', marginTop: '1rem', textDecoration: 'none', padding: '0.75rem 1rem', fontSize: '0.95rem' }}
                      >
                        Visit Official Website
                      </a>
                    </div>
                  </div>

                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
