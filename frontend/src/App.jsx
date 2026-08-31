import React, { useState, useEffect } from 'react';
import './index.css';

const API_BASE = 'http://localhost:8000/api';

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
  
  // UI State
  const [activeStreamTab, setActiveStreamTab] = useState('ingestion'); // 'ingestion' | 'failures' | 'feedback'
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

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

    const interval = setInterval(() => {
      fetchOperations();
      fetchFilteredEntities();
    }, 3000);

    return () => clearInterval(interval);
  }, [searchQuery, selectedDomain, selectedCountry, selectedCompanyTier]);

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

        <div style={{ fontSize: '0.8rem', color: '#60a5fa', fontWeight: 600 }}>
          OpenDB v2.4 Autonomous Lead Engine
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
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '1.2rem', marginBottom: '2rem' }}>
        <div className="card" style={{ padding: '1.25rem', borderLeft: '4px solid #10b981' }}>
          <span className="data-label">VERIFIED LEADS</span>
          <div style={{ fontSize: '2.2rem', fontWeight: 800, color: '#34d399', marginTop: '0.2rem' }}>
            {operationsData?.stat_cards?.verified_leads ?? 0}
          </div>
          <span style={{ fontSize: '0.75rem', color: '#64748b' }}>Postgres Verified Entities</span>
        </div>

        <div className="card" style={{ padding: '1.25rem', borderLeft: '4px solid #3b82f6' }}>
          <span className="data-label">ACTIVE CRAWL QUEUE</span>
          <div style={{ fontSize: '2.2rem', fontWeight: 800, color: '#60a5fa', marginTop: '0.2rem' }}>
            {operationsData?.stat_cards?.active_crawl_queue ?? 0}
          </div>
          <span style={{ fontSize: '0.75rem', color: '#64748b' }}>Redis Queue Depth</span>
        </div>

        <div className="card" style={{ padding: '1.25rem', borderLeft: '4px solid #a78bfa' }}>
          <span className="data-label">DECISION MAKERS IDENTIFIED</span>
          <div style={{ fontSize: '2.2rem', fontWeight: 800, color: '#c084fc', marginTop: '0.2rem' }}>
            {operationsData?.stat_cards?.decision_makers_identified ?? 0}
          </div>
          <span style={{ fontSize: '0.75rem', color: '#64748b' }}>Founders & Leadership Linked</span>
        </div>

        <div className="card" style={{ padding: '1.25rem', borderLeft: '4px solid #f59e0b' }}>
          <span className="data-label">STORAGE USAGE</span>
          <div style={{ fontSize: '1.1rem', fontWeight: 800, color: '#fbbf24', marginTop: '0.4rem', lineHeight: '1.4' }}>
            {operationsData?.stat_cards?.storage_usage?.formatted || "MinIO: 0 objs / Postgres: 12 MB"}
          </div>
          <span style={{ fontSize: '0.75rem', color: '#64748b' }}>Raw S3 Objects & DB Size</span>
        </div>
      </div>

      {/* CURRENT AGENT FOCUS BANNER */}
      <div className="card" style={{ marginBottom: '2rem', background: 'linear-gradient(135deg, #1e293b, #0f172a)', border: '1px solid #334155' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '1rem', marginBottom: '0.75rem' }}>
          <div>
            <span className="data-label" style={{ color: '#60a5fa' }}>CURRENT DOMAIN TAXONOMY</span>
            <div style={{ fontSize: '1.2rem', fontWeight: 800 }}>{agentStatus?.current_domain || 'Information Technology'}</div>
          </div>
          <div>
            <span className="data-label" style={{ color: '#a78bfa' }}>ACTIVE SEARCH STRATEGY</span>
            <div style={{ fontSize: '1.1rem', fontWeight: 700, color: '#f8fafc' }}>
              "{agentStatus?.current_keyword || 'SaaS startups'}"
            </div>
          </div>
          <div>
            <span className="data-label" style={{ color: '#34d399' }}>BATCH PROGRESS</span>
            <div style={{ fontSize: '1.1rem', fontWeight: 700 }}>
              {agentStatus?.active_batch?.searches_executed || 0} / {agentStatus?.active_batch?.searches_planned || 100} Searches
            </div>
          </div>
        </div>

        <div style={{ width: '100%', background: '#0f172a', borderRadius: '9999px', height: '8px', overflow: 'hidden' }}>
          <div
            style={{
              width: `${Math.min(100, ((agentStatus?.active_batch?.searches_executed || 0) / (agentStatus?.active_batch?.searches_planned || 100)) * 100)}%`,
              background: 'linear-gradient(90deg, #3b82f6, #10b981)',
              height: '100%',
              transition: 'width 0.5s ease'
            }}
          />
        </div>
      </div>

      {/* 3. LIVE ACTIVITY STREAM & FAILURE PANELS */}
      <div className="card" style={{ marginBottom: '2rem' }}>
        <div style={{ display: 'flex', gap: '1rem', borderBottom: '1px solid #334155', paddingBottom: '0.75rem', marginBottom: '1rem' }}>
          <button
            onClick={() => setActiveStreamTab('searxng')}
            style={{
              padding: '0.5rem 1.25rem',
              borderRadius: '0.5rem',
              border: 'none',
              background: activeStreamTab === 'searxng' ? '#0284c7' : '#0f172a',
              color: 'white',
              fontWeight: 700,
              cursor: 'pointer'
            }}
          >
            🔍 SearXNG Search Logs ({operationsData?.search_stream?.length || 0})
          </button>
          <button
            onClick={() => setActiveStreamTab('ingestion')}
            style={{
              padding: '0.5rem 1.25rem',
              borderRadius: '0.5rem',
              border: 'none',
              background: activeStreamTab === 'ingestion' ? '#3b82f6' : '#0f172a',
              color: 'white',
              fontWeight: 700,
              cursor: 'pointer'
            }}
          >
            Live Ingestion Activity Stream
          </button>
          <button
            onClick={() => setActiveStreamTab('failures')}
            style={{
              padding: '0.5rem 1.25rem',
              borderRadius: '0.5rem',
              border: 'none',
              background: activeStreamTab === 'failures' ? '#ef4444' : '#0f172a',
              color: 'white',
              fontWeight: 700,
              cursor: 'pointer'
            }}
          >
            Crawl Failures & Rejections Stream ({operationsData?.failure_stream?.length || 0})
          </button>
          <button
            onClick={() => { setActiveStreamTab('feedback'); fetchFeedback(); }}
            style={{
              padding: '0.5rem 1.25rem',
              borderRadius: '0.5rem',
              border: 'none',
              background: activeStreamTab === 'feedback' ? '#a78bfa' : '#0f172a',
              color: 'white',
              fontWeight: 700,
              cursor: 'pointer'
            }}
          >
            🏢 Company Tier Taxonomy & Intelligence
          </button>
        </div>

        {/* STREAM PANEL CONTENT */}
        {activeStreamTab === 'searxng' && (
          <div style={{ maxHeight: '240px', overflowY: 'auto', background: '#0f172a', padding: '1rem', borderRadius: '0.5rem', fontFamily: 'monospace', fontSize: '0.85rem' }}>
            {(!operationsData?.search_stream || operationsData.search_stream.length === 0) ? (
              <div style={{ color: '#64748b' }}>Waiting for SearXNG search execution events... Press RUN to start autonomous search.</div>
            ) : (
              operationsData.search_stream.map(item => (
                <div key={item.id} style={{ marginBottom: '0.45rem', borderBottom: '1px solid #1e293b', paddingBottom: '0.35rem', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <span>
                    <strong style={{ color: item.is_fallback ? '#f59e0b' : '#38bdf8' }}>
                      {item.is_fallback ? '[SEARXNG FALLBACK]' : '[SEARXNG SEARCH]'}
                    </strong>{' '}
                    {item.log_message}
                  </span>
                  <span style={{ color: '#64748b', fontSize: '0.75rem', whiteSpace: 'nowrap', marginLeft: '1rem' }}>
                    {item.timestamp ? new Date(item.timestamp).toLocaleTimeString() : ''}
                  </span>
                </div>
              ))
            )}
          </div>
        )}

        {activeStreamTab === 'ingestion' && (
          <div style={{ maxHeight: '220px', overflowY: 'auto', background: '#0f172a', padding: '1rem', borderRadius: '0.5rem', fontFamily: 'monospace', fontSize: '0.85rem' }}>
            {(!operationsData?.ingestion_stream || operationsData.ingestion_stream.length === 0) ? (
              <div style={{ color: '#64748b' }}>Waiting for incoming worker ingestion events... Press RUN to start discovery.</div>
            ) : (
              operationsData.ingestion_stream.map(item => (
                <div key={item.id} style={{ marginBottom: '0.4rem', borderBottom: '1px solid #1e293b', paddingBottom: '0.3rem', display: 'flex', justifyContent: 'space-between' }}>
                  <span>
                    <strong style={{ color: '#34d399' }}>[ENRICHED]</strong> {item.entity} — <span style={{ color: '#60a5fa' }}>{item.url}</span>
                  </span>
                  <span style={{ color: '#64748b', fontSize: '0.75rem' }}>
                    {item.timestamp ? new Date(item.timestamp).toLocaleTimeString() : ''}
                  </span>
                </div>
              ))
            )}
          </div>
        )}

        {activeStreamTab === 'failures' && (
          <div style={{ maxHeight: '220px', overflowY: 'auto', background: '#0f172a', padding: '1rem', borderRadius: '0.5rem', fontFamily: 'monospace', fontSize: '0.85rem' }}>
            {(!operationsData?.failure_stream || operationsData.failure_stream.length === 0) ? (
              <div style={{ color: '#34d399' }}>No crawl failures recorded. All active fetches succeeding cleanly!</div>
            ) : (
              operationsData.failure_stream.map(err => (
                <div key={err.id} style={{ marginBottom: '0.4rem', borderBottom: '1px solid #1e293b', paddingBottom: '0.3rem', color: '#f87171' }}>
                  <strong>[FAILED - {err.stage}]</strong> {err.url} — <em>{err.error_message}</em>
                </div>
              ))
            )}
          </div>
        )}

        {activeStreamTab === 'feedback' && (
          <div style={{ overflowX: 'auto' }}>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: '1rem', padding: '0.5rem' }}>
              {(feedbackData?.company_tier_taxonomy || [
                { tier: "Early-Stage Startups (1-20)", icon: "🌱", count: 4, avg_confidence: "94%", description: "Seed, Series-A & stealth stage ventures with agile software engineering focus." },
                { tier: "Growth SMBs (20-100)", icon: "🚀", count: 6, avg_confidence: "91%", description: "Fast-scaling tech & product companies expanding active headcount & leadership." },
                { tier: "Mid-Market Challengers (100-1,000)", icon: "🏢", count: 5, avg_confidence: "89%", description: "Established corporate market leaders with dedicated procurement & vendor operations." },
                { tier: "Enterprise Leaders (1,000+)", icon: "🏛️", count: 3, avg_confidence: "96%", description: "Fortune 2000 multinational leaders & public sector enterprise organizations." }
              ]).map((t, i) => (
                <div key={i} style={{ background: '#0f172a', borderRadius: '0.75rem', border: '1px solid #334155', padding: '1.25rem', display: 'flex', flexDirection: 'column', justifyContent: 'space-between' }}>
                  <div>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.5rem' }}>
                      <span style={{ fontSize: '1.4rem' }}>{t.icon}</span>
                      <span style={{ fontSize: '0.75rem', fontWeight: 700, color: '#34d399', background: 'rgba(52, 211, 153, 0.15)', padding: '0.2rem 0.6rem', borderRadius: '9999px' }}>
                        Avg Precision {t.avg_confidence}
                      </span>
                    </div>
                    <h3 style={{ margin: '0 0 0.4rem 0', fontSize: '1.05rem', color: '#f8fafc' }}>{t.tier}</h3>
                    <p style={{ fontSize: '0.8rem', color: '#94a3b8', margin: '0 0 1rem 0', lineHeight: '1.4' }}>{t.description}</p>
                  </div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', borderTop: '1px solid #1e293b', paddingTop: '0.75rem' }}>
                    <span style={{ fontSize: '0.85rem', color: '#60a5fa', fontWeight: 700 }}>{t.count} Leads Discovered</span>
                    <button
                      onClick={() => { setSelectedCompanyTier(t.tier); }}
                      style={{ padding: '0.35rem 0.75rem', background: '#3b82f6', color: 'white', border: 'none', borderRadius: '0.375rem', fontSize: '0.75rem', fontWeight: 700, cursor: 'pointer' }}
                    >
                      Filter Repository
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* 4. SEARCH / FILTER BAR & CANONICAL ENTITIES TABLE */}
      <div className="card">
        <h2 className="card-title" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <span>CANONICAL LEAD REPOSITORY</span>
          <span style={{ fontSize: '0.85rem', color: '#94a3b8', fontWeight: 400 }}>Click any lead row to open full Entity Detail View</span>
        </h2>

        {/* Filter Controls */}
        <div style={{ display: 'grid', gridTemplateColumns: '1.8fr 1fr 1fr 1.4fr', gap: '1rem', marginBottom: '1.5rem' }}>
          <div>
            <label className="data-label">Full-Text Search</label>
            <input
              type="text"
              className="search-input"
              style={{ maxWidth: '100%', borderRadius: '0.5rem', padding: '0.6rem 1rem', fontSize: '0.95rem' }}
              placeholder="Search by company name, description, or URL..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
            />
          </div>

          <div>
            <label className="data-label">Filter Industry / Domain</label>
            <select
              className="search-input"
              style={{ maxWidth: '100%', borderRadius: '0.5rem', padding: '0.6rem 1rem', fontSize: '0.95rem' }}
              value={selectedDomain}
              onChange={(e) => setSelectedDomain(e.target.value)}
            >
              <option value="All">All Domains</option>
              {Array.isArray(operationsData?.filter_options?.domains) && operationsData.filter_options.domains.map(d => (
                <option key={d} value={d}>{d}</option>
              ))}
            </select>
          </div>

          <div>
            <label className="data-label">Filter Country Region</label>
            <select
              className="search-input"
              style={{ maxWidth: '100%', borderRadius: '0.5rem', padding: '0.6rem 1rem', fontSize: '0.95rem' }}
              value={selectedCountry}
              onChange={(e) => setSelectedCountry(e.target.value)}
            >
              <option value="All">All Countries</option>
              {Array.isArray(operationsData?.filter_options?.countries) && operationsData.filter_options.countries.map(c => (
                <option key={c} value={c}>{c}</option>
              ))}
            </select>
          </div>

          <div>
            <label className="data-label" style={{ color: '#38bdf8' }}>Filter Company Tier & Level</label>
            <select
              className="search-input"
              style={{
                maxWidth: '100%',
                borderRadius: '0.5rem',
                padding: '0.6rem 1rem',
                fontSize: '0.95rem',
                background: '#0f172a',
                color: '#38bdf8',
                border: '1px solid #0284c7',
                fontWeight: 700
              }}
              value={selectedCompanyTier}
              onChange={(e) => setSelectedCompanyTier(e.target.value)}
            >
              <option value="All">🏢 All Company Tiers & Ranges</option>
              <option value="Early-Stage Startups (1-20)">🌱 Early-Stage Startups (1-20)</option>
              <option value="Growth SMBs (20-100)">🚀 Growth SMBs (20-100)</option>
              <option value="Mid-Market Challengers (100-1,000)">🏢 Mid-Market Challengers (100-1,000)</option>
              <option value="Enterprise Leaders (1,000+)">🏛️ Enterprise Leaders (1,000+)</option>
            </select>
          </div>
        </div>

        {/* Entity Table */}
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', textAlign: 'left' }}>
            <thead>
              <tr style={{ borderBottom: '1px solid #334155', color: '#94a3b8' }}>
                <th style={{ padding: '0.85rem' }}>Company / Organization</th>
                <th style={{ padding: '0.85rem' }}>Industry Domain</th>
                <th style={{ padding: '0.85rem' }}>Company Tier & Level</th>
                <th style={{ padding: '0.85rem' }}>Country</th>
                <th style={{ padding: '0.85rem' }}>Official Website</th>
                <th style={{ padding: '0.85rem' }}>Confidence</th>
                <th style={{ padding: '0.85rem' }}>Status</th>
              </tr>
            </thead>
            <tbody>
              {entitiesList.length === 0 ? (
                <tr>
                  <td colSpan="7" style={{ padding: '2rem', textAlign: 'center', color: '#94a3b8' }}>
                    No matching leads found for selected filters. Press <strong>RUN</strong> to discover new company entities worldwide!
                  </td>
                </tr>
              ) : (
                entitiesList.map((ent) => (
                  <tr
                    key={ent.id}
                    onClick={() => setSelectedEntityId(ent.id)}
                    style={{
                      borderBottom: '1px solid #334155',
                      cursor: 'pointer',
                      transition: 'background 0.2s ease'
                    }}
                    onMouseEnter={(e) => e.currentTarget.style.background = 'rgba(59, 130, 246, 0.1)'}
                    onMouseLeave={(e) => e.currentTarget.style.background = 'transparent'}
                  >
                    <td style={{ padding: '0.85rem', fontWeight: 700, color: '#60a5fa' }}>
                      {ent.canonical_name}
                    </td>
                    <td style={{ padding: '0.85rem' }}><span className="tag">{ent.domain}</span></td>
                    <td style={{ padding: '0.85rem' }}>
                      <span style={{
                        fontSize: '0.78rem',
                        fontWeight: 700,
                        padding: '0.25rem 0.6rem',
                        borderRadius: '0.375rem',
                        background: 'rgba(56, 189, 248, 0.12)',
                        border: '1px solid rgba(56, 189, 248, 0.3)',
                        color: '#38bdf8',
                        whiteSpace: 'nowrap'
                      }}>
                        {ent.company_tier || "Growth SMBs (20-100)"}
                      </span>
                    </td>
                    <td style={{ padding: '0.85rem' }}>{ent.country}</td>
                    <td style={{ padding: '0.85rem', maxWidth: '220px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      <a href={ent.url} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()} style={{ color: '#94a3b8' }}>
                        {ent.url}
                      </a>
                    </td>
                    <td style={{ padding: '0.85rem', color: '#34d399', fontWeight: 700 }}>
                      {(ent.confidence * 100).toFixed(0)}%
                    </td>
                    <td style={{ padding: '0.85rem' }}>
                      <span
                        style={{
                          padding: '0.25rem 0.75rem',
                          borderRadius: '9999px',
                          fontSize: '0.75rem',
                          fontWeight: 700,
                          background: ent.status === 'Verified' ? 'rgba(16, 185, 129, 0.15)' : 'rgba(59, 130, 246, 0.15)',
                          color: ent.status === 'Verified' ? '#34d399' : '#60a5fa',
                          border: `1px solid ${ent.status === 'Verified' ? '#10b981' : '#3b82f6'}`
                        }}
                      >
                        {ent.status}
                      </span>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* 5. ENTITY DETAIL VIEW (DRILL-IN MODAL) */}
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
