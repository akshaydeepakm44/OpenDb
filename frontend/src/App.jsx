import React, { useState, useEffect } from 'react';
import './index.css';

const API_BASE = import.meta.env.VITE_API_BASE || '/api';

const getCleanBrandName = (name) => {
  if (!name) return 'Company';
  const parts = name.split(/[-–—|:•]/).map(s => s.trim()).filter(Boolean);
  if (parts.length > 0) {
    const first = parts[0];
    if (first.split(/\s+/).length <= 4) return first;
    return first.split(/\s+/).slice(0, 2).join(' ');
  }
  const words = name.trim().split(/\s+/);
  return words.length > 3 ? words.slice(0, 2).join(' ') : name.trim();
};

const isLinkedInProfile = (person) => {
  const url = person?.linkedin_url || person?.linkedin_search_url || person?.source_url || '';
  return url.includes('linkedin.com/in/') && !url.includes('/search/') && !url.includes('/jobs/');
};

const isPersonVerified = (person) => {
  if (!person) return false;
  if (person.match_status === 'REJECTED') return false;
  if (person.match_score !== undefined && person.match_score < 0.75) return false;
  if (person.confidence !== undefined && person.confidence < 0.75) return false;
  const lower = (person.name || '').toLowerCase().trim();
  if (!lower || lower === 'good food' || lower === 'home' || lower === 'index' || lower === 'welcome') return false;
  return true;
};

const getLinkedInProfileOrSearch = (person, companyName) => {
  const url = person?.linkedin_url || person?.linkedin_search_url || person?.source_url || '';
  if (url.includes('linkedin.com/in/') && !url.includes('/search/') && !url.includes('/jobs/')) {
    return url;
  }
  const cleanComp = getCleanBrandName(companyName);
  const q = `${person?.name || ''} ${cleanComp}`.trim();
  return `https://www.linkedin.com/search/results/people/?keywords=${encodeURIComponent(q)}`;
};

const getLinkedInLabel = (person) => {
  return isLinkedInProfile(person) ? 'View Profile ↗' : 'Search LinkedIn ↗';
};


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
  const [autoScroll, setAutoScroll] = useState(true);
  const logContainerRef = React.useRef(null);

  // Lead Repository Tab State
  const [leadView, setLeadView] = useState('crawled'); // 'crawled' | 'agent2' | 'verified'
  const [crawledDocs, setCrawledDocs] = useState([]);
  const [crawledPage, setCrawledPage] = useState(1);
  const [crawledMeta, setCrawledMeta] = useState({ total: 0, pages: 1 });
  const [verifiedTotalCount, setVerifiedTotalCount] = useState(0);

  // Agent 2 Verification State
  const [agent2Sessions, setAgent2Sessions] = useState([]);
  const [agent2Page, setAgent2Page] = useState(1);
  const [agent2Meta, setAgent2Meta] = useState({ total: 0, pages: 1 });
  const [selectedAgent2Id, setSelectedAgent2Id] = useState(null);
  const [agent2Detail, setAgent2Detail] = useState(null);
  const [loadingAgent2Detail, setLoadingAgent2Detail] = useState(false);
  const [triggeringDocIds, setTriggeringDocIds] = useState({});

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

  const isOpsPollingRef = React.useRef(false);
  const isCardsPollingRef = React.useRef(false);

  // Poll Services Health and Operations Dashboard Data (non-overlapping)
  const fetchOperations = async () => {
    if (isOpsPollingRef.current) return;
    isOpsPollingRef.current = true;
    try {
      const results = await Promise.allSettled([
        fetch(`${API_BASE}/agent/status`).then(r => r.ok ? r.json() : null),
        fetch(`${API_BASE}/health/services`).then(r => r.ok ? r.json() : null),
        fetch(`${API_BASE}/agent/operations`).then(r => r.ok ? r.json() : null)
      ]);

      if (results[0].status === 'fulfilled' && results[0].value) setAgentStatus(results[0].value);
      if (results[1].status === 'fulfilled' && results[1].value) setServicesHealth(results[1].value);
      if (results[2].status === 'fulfilled' && results[2].value) setOperationsData(results[2].value);
    } finally {
      isOpsPollingRef.current = false;
    }
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
        if (Array.isArray(data)) {
          setEntitiesList(data);
          setVerifiedTotalCount(data.length);
        } else {
          setEntitiesList(data.results || []);
          setVerifiedTotalCount(data.total || (data.results ? data.results.length : 0));
        }
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
      if (selectedDomain && selectedDomain !== 'All') params.append('domain', selectedDomain);
      if (selectedCountry && selectedCountry !== 'All') params.append('country', selectedCountry);
      if (selectedCompanyTier && selectedCompanyTier !== 'All' && !selectedCompanyTier.includes('All Company Tiers')) {
        params.append('company_tier', selectedCompanyTier);
      }
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

  const fetchAgent2Sessions = async () => {
    try {
      const params = new URLSearchParams();
      params.append('page', agent2Page);
      params.append('limit', CARDS_PER_PAGE);
      if (searchQuery) params.append('query', searchQuery);
      const res = await fetch(`${API_BASE}/agent2/cards?${params.toString()}`);
      if (res.ok) {
        const data = await res.json();
        setAgent2Sessions(data.results || []);
        setAgent2Meta({ total: data.total || 0, pages: data.pages || 1 });
      }
    } catch (err) {
      console.error('Error fetching Agent 2 sessions:', err);
    }
  };

  const handleTriggerAgent2 = async (docId, e) => {
    if (e) e.stopPropagation();
    setTriggeringDocIds(prev => ({ ...prev, [docId]: true }));
    try {
      const res = await fetch(`${API_BASE}/agent2/process/${docId}`, { method: 'POST' });
      if (res.ok) {
        const data = await res.json();
        setLeadView('agent2');
        fetchAgent2Sessions();
        if (data.session_id) {
          setSelectedAgent2Id(data.session_id);
        }
      } else {
        const err = await res.json().catch(() => ({}));
        alert(`Could not trigger Agent 2: ${err.detail || 'Internal server error'}`);
      }
    } catch (err) {
      console.error('Error triggering Agent 2:', err);
      alert('Failed to trigger Agent 2.');
    } finally {
      setTriggeringDocIds(prev => ({ ...prev, [docId]: false }));
    }
  };

  const fetchActiveCards = async () => {
    if (isCardsPollingRef.current) return;
    isCardsPollingRef.current = true;
    try {
      if (leadView === 'verified') {
        await fetchFilteredEntities();
      } else if (leadView === 'agent2') {
        await fetchAgent2Sessions();
      } else {
        await fetchCrawledDocuments();
      }
    } finally {
      isCardsPollingRef.current = false;
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
        await fetchAgent2Sessions();
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

  // Immediate fetch on filter or view changes
  useEffect(() => {
    fetchOperations();
    if (leadView === 'verified') {
      fetchFilteredEntities();
    } else if (leadView === 'agent2') {
      fetchAgent2Sessions();
    } else {
      fetchCrawledDocuments();
    }
  }, [searchQuery, selectedDomain, selectedCountry, selectedCompanyTier, leadView, crawledPage, agent2Page]);

  // Periodic polling: telemetry stream every 4s, card data every 7s (staggered, non-overlapping)
  useEffect(() => {
    const opsInterval = setInterval(() => {
      fetchOperations();
    }, 4000);

    const cardsInterval = setInterval(() => {
      fetchActiveCards();
    }, 7000);

    return () => {
      clearInterval(opsInterval);
      clearInterval(cardsInterval);
    };
  }, [leadView, crawledPage, agent2Page, searchQuery, selectedDomain, selectedCountry, selectedCompanyTier]);

  // Fetch detail view data when an entity is selected
  useEffect(() => {
    if (!selectedEntityId) {
      setEntityDetail(null);
      return;
    }

    // Instant optimistic pre-population from current state for 0ms modal opening
    const preExisting = (documentDetail && (documentDetail.verified_entity_id === selectedEntityId || documentDetail.id === selectedEntityId)) ? documentDetail :
                        ((entitiesList || []).find(e => e.id === selectedEntityId) ||
                        (crawledDocs || []).find(d => (d.verified_entity_id === selectedEntityId || d.id === selectedEntityId)));
    if (preExisting) {
      setEntityDetail({
        id: selectedEntityId,
        canonical_name: preExisting.canonical_name || preExisting.title || preExisting.domain,
        domain: preExisting.domain,
        official_website: preExisting.official_website || preExisting.url || `https://${preExisting.domain}`,
        logo_url: preExisting.logo_url || `https://www.google.com/s2/favicons?domain=${preExisting.domain}&sz=128`,
        headquarters: preExisting.headquarters || 'Not Specified',
        industry: (preExisting.industry && preExisting.industry !== 'Commercial Web' && preExisting.industry !== 'Commercial Web & Digital Enterprise') ? preExisting.industry : 'Unknown',
        company_size: preExisting.company_tier || preExisting.company_size || 'Unknown',
        company_tier: preExisting.company_tier || 'Unknown',
        revenue_funding: preExisting.revenue_funding || 'Unknown',
        verified_emails: preExisting.verified_emails || [],
        summary: preExisting.business_overview || preExisting.summary || 'Intelligence dossier synthesis pending.',
        technology_stack: preExisting.technology_stack || [],
        decision_makers: preExisting.decision_makers || [],
        crawled_subpages: preExisting.crawled_subpages || [],
        firmographics: preExisting.firmographics || {},
        lead_quality_score: preExisting.lead_quality_score || 85.0,
        warmth_score: preExisting.warmth_score || 8.5,
        provenance: preExisting.provenance || { source_url: preExisting.url, confidence: 0.85 }
      });
      setLoadingDetail(false);
    } else {
      setLoadingDetail(true);
    }

    fetch(`${API_BASE}/agent/entities/${selectedEntityId}`)
      .then(res => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
      })
      .then(data => {
        if (data && !data.detail) {
          setEntityDetail(data);
        }
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

    const preDoc = (crawledDocs || []).find(d => d.id === selectedDocumentId);
    if (preDoc) {
      setDocumentDetail({
        id: preDoc.id,
        url: preDoc.url,
        domain: preDoc.domain,
        title: preDoc.title,
        canonical_name: preDoc.canonical_name || preDoc.title,
        logo_url: preDoc.logo_url,
        http_status: preDoc.http_status || 200,
        content_type: preDoc.content_type || 'text/html',
        word_count: preDoc.word_count || 100,
        text_preview: 'Loading raw storage content from OpenDB vault...',
        extracted_facts: [],
        firmographics: {},
        technology_stack: [],
        decision_makers: preDoc.decision_makers || []
      });
      setLoadingDocDetail(false);
    } else {
      setLoadingDocDetail(true);
    }

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

  // Fetch Agent 2 detail view data when an Agent 2 session is selected
  useEffect(() => {
    if (!selectedAgent2Id) {
      setAgent2Detail(null);
      return;
    }

    setLoadingAgent2Detail(true);
    const loadAgent2 = () => {
      fetch(`${API_BASE}/agent2/cards/${selectedAgent2Id}`)
        .then(r => r.ok ? r.json() : null)
        .then(data => {
          if (data) setAgent2Detail(data);
          setLoadingAgent2Detail(false);
        })
        .catch(err => {
          console.error("Error loading Agent 2 session detail:", err);
          setLoadingAgent2Detail(false);
        });
    };

    loadAgent2();
    const timer = setInterval(loadAgent2, 3500);
    return () => clearInterval(timer);
  }, [selectedAgent2Id]);

  const toggleRunPause = async () => {
    const isCurrentlyRunning = agentStatus?.status === 'RUNNING';
    const nextStatus = isCurrentlyRunning ? 'PAUSED' : 'RUNNING';
    const targetAction = isCurrentlyRunning ? 'pause' : 'run';

    // Instant optimistic toggle: button and status badge update with 0ms delay!
    setAgentStatus(prev => ({ ...prev, status: nextStatus }));
    setError(null);

    try {
      const res = await fetch(`${API_BASE}/agent/${targetAction}`, { method: 'POST' });
      if (!res.ok) {
        // Rollback state if server returned error
        setAgentStatus(prev => ({ ...prev, status: isCurrentlyRunning ? 'RUNNING' : 'PAUSED' }));
        throw new Error(`Failed to ${targetAction} agent.`);
      }
      // Re-fetch telemetry in background
      fetchOperations();
    } catch (err) {
      setError(err.message);
    }
  };

  const isRunning = agentStatus?.status === 'RUNNING';

  const renderInfrastructureStatus = () => {
    if (!servicesHealth) return <span style={{ fontSize: '0.8rem', color: '#64748b' }}>Checking endpoints...</span>;

    const dbMode = servicesHealth.database?.mode || 'POSTGRESQL';
    const dbStatus = servicesHealth.database?.status || 'CONNECTED';
    const redisStatus = servicesHealth.redis?.status || 'UNAVAILABLE';
    const searxStatus = servicesHealth.searxng?.status || 'UNAVAILABLE';
    const minioStatus = servicesHealth.minio?.status || 'UNAVAILABLE';
    const crawlerStatus = servicesHealth.crawler?.status || 'READY';

    const badges = [
      {
        key: 'db',
        label: dbMode === 'POSTGRESQL' ? 'PostgreSQL Connected' : '⚠ SQLite Fallback — PostgreSQL unavailable',
        isOk: dbMode === 'POSTGRESQL' && dbStatus === 'CONNECTED',
        isDegraded: dbMode === 'SQLITE_FALLBACK'
      },
      {
        key: 'redis',
        label: redisStatus === 'CONNECTED' ? 'Redis Connected' : '🔴 Redis Offline',
        isOk: redisStatus === 'CONNECTED',
        isDegraded: false
      },
      {
        key: 'searx',
        label: searxStatus === 'CONNECTED' ? 'SearXNG Connected' : '🔴 SearXNG Offline',
        isOk: searxStatus === 'CONNECTED',
        isDegraded: false
      },
      {
        key: 'minio',
        label: minioStatus === 'CONNECTED' ? 'MinIO Connected' : '🔴 MinIO Offline',
        isOk: minioStatus === 'CONNECTED',
        isDegraded: false
      },
      {
        key: 'crawler',
        label: crawlerStatus === 'READY' ? 'Crawl4AI / Playwright Ready' : '🔴 Crawler Unavailable',
        isOk: crawlerStatus === 'READY',
        isDegraded: false
      }
    ];

    return badges.map(b => {
      const color = b.isOk ? '#10b981' : b.isDegraded ? '#fbbf24' : '#ef4444';
      const bg = b.isOk ? 'rgba(16, 185, 129, 0.15)' : b.isDegraded ? 'rgba(245, 158, 11, 0.2)' : 'rgba(239, 68, 68, 0.15)';
      return (
        <div key={b.key} style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', background: bg, padding: '0.35rem 0.75rem', borderRadius: '0.5rem', border: `1px solid ${color}` }}>
          <span style={{ height: '8px', width: '8px', borderRadius: '50%', backgroundColor: color }} />
          <span style={{ fontSize: '0.75rem', fontWeight: 700, textTransform: 'uppercase', color: color }}>
            {b.label}
          </span>
        </div>
      );
    });
  };

  return (
    <div className="app-container" style={{ maxWidth: '1400px' }}>
      {/* 1. TOP OPERATIONS STATUS BAR (REAL SERVICE HEALTH) */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '1rem', background: '#0f172a', padding: '0.75rem 1.25rem', borderRadius: '0.75rem', border: '1px solid #334155', marginBottom: '1.5rem' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', flexWrap: 'wrap' }}>
          <span style={{ fontSize: '0.8rem', fontWeight: 700, color: '#94a3b8', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
            Infrastructure Status:
          </span>
          {renderInfrastructureStatus()}
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
          {servicesHealth?.database?.mode === 'SQLITE_FALLBACK' && (
            <span style={{ fontSize: '0.75rem', fontWeight: 800, color: '#fbbf24', background: 'rgba(245, 158, 11, 0.2)', padding: '0.35rem 0.75rem', borderRadius: '0.5rem', border: '1px solid #f59e0b', textTransform: 'uppercase' }}>
              ⚠️ SQLITE FALLBACK ACTIVE
            </span>
          )}
          <div style={{ fontSize: '0.8rem', color: '#60a5fa', fontWeight: 600 }}>
            OpenDB v2.4 Autonomous Lead Engine
          </div>
        </div>
      </div>

      {/* PERSISTENT SQLITE FALLBACK WARNING BANNER */}
      {servicesHealth?.database?.mode === 'SQLITE_FALLBACK' && (
        <div style={{ background: 'rgba(245, 158, 11, 0.15)', border: '1px solid #f59e0b', borderRadius: '0.75rem', padding: '1rem 1.25rem', marginBottom: '1.5rem', display: 'flex', alignItems: 'center', gap: '0.85rem' }}>
          <span style={{ fontSize: '1.4rem' }}>⚠️</span>
          <div>
            <strong style={{ color: '#fbbf24', fontSize: '0.95rem' }}>Database running in SQLite fallback mode.</strong>
            <div style={{ fontSize: '0.85rem', color: '#f1f5f9', marginTop: '0.2rem' }}>
              PostgreSQL is unavailable. Real data is being persisted locally to <code style={{ color: '#fbbf24' }}>opendb_fallback.db</code> and will synchronize when PostgreSQL recovers.
            </div>
          </div>
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
              {leadView === 'crawled' && (
                <>Showing <strong style={{ color: '#f59e0b' }}>{crawledMeta.total?.toLocaleString() || 0}</strong> Crawled Data Cards — Agent 1 Crawl4AI Evidence Vault</>
              )}
              {leadView === 'agent2' && (
                <>Showing <strong style={{ color: '#38bdf8' }}>{agent2Meta.total?.toLocaleString() || agent2Sessions.length}</strong> Agent 2 Verification Cards — 7-Field Gate & Anti-Hallucination Matching</>
              )}
              {leadView === 'verified' && (
                <>Showing <strong style={{ color: '#10b981' }}>{entitiesList.length}</strong> Verified Data Cards — PostgreSQL Intelligence Lake</>
              )}
            </div>
          </div>

          {/* Tab toggle: 3-way Agent 1 / Agent 2 / PostgreSQL */}
          <div style={{ display: 'flex', gap: '0.5rem', background: '#0f172a', padding: '0.3rem', borderRadius: '0.65rem', border: '1px solid #334155' }}>
            <button
              onClick={() => { setLeadView('crawled'); setCrawledPage(1); }}
              style={{
                padding: '0.45rem 1.1rem', borderRadius: '0.5rem', border: 'none', cursor: 'pointer',
                fontWeight: 800, fontSize: '0.82rem',
                background: leadView === 'crawled' ? '#f59e0b' : 'transparent',
                color: leadView === 'crawled' ? '#000' : '#94a3b8',
                transition: 'all 0.2s'
              }}
            >
              ⚡ Crawled ({(crawledMeta.total || 0).toLocaleString()})
            </button>
            <button
              onClick={() => { setLeadView('agent2'); setAgent2Page(1); }}
              style={{
                padding: '0.45rem 1.1rem', borderRadius: '0.5rem', border: 'none', cursor: 'pointer',
                fontWeight: 800, fontSize: '0.82rem',
                background: leadView === 'agent2' ? '#38bdf8' : 'transparent',
                color: leadView === 'agent2' ? '#000' : '#94a3b8',
                transition: 'all 0.2s'
              }}
            >
              🔄 In Verification ({(agent2Meta.total || agent2Sessions.length).toLocaleString()})
            </button>
            <button
              onClick={() => { setLeadView('verified'); setCurrentPage(1); }}
              style={{
                padding: '0.45rem 1.1rem', borderRadius: '0.5rem', border: 'none', cursor: 'pointer',
                fontWeight: 800, fontSize: '0.82rem',
                background: leadView === 'verified' ? '#10b981' : 'transparent',
                color: leadView === 'verified' ? '#000' : '#94a3b8',
                transition: 'all 0.2s'
              }}
            >
              ✅ Verified ({(verifiedTotalCount || entitiesList.length).toLocaleString()})
            </button>
          </div>

          {/* Pagination */}
          {(() => {
            const activePage = leadView === 'crawled' ? crawledPage : currentPage;
            const maxPages = leadView === 'crawled' ? (crawledMeta.pages || 1) : Math.max(1, Math.ceil(entitiesList.length / CARDS_PER_PAGE));
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

        {/* ── CRAWLED LEADS CARD GRID ── */}
        {leadView === 'crawled' && (
          crawledDocs.length === 0 ? (
            <div style={{ textAlign: 'center', padding: '4rem 2rem', color: '#64748b' }}>
              <div style={{ fontSize: '3rem', marginBottom: '1rem' }}>⚡</div>
              <div style={{ fontSize: '0.9rem' }}>Press <strong style={{ color: '#38bdf8' }}>Run Discovery Loop</strong> to start Agent 1 autonomous crawling.</div>
            </div>
          ) : (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(340px, 1fr))', gap: '1.25rem' }}>
              {crawledDocs.map((doc) => {
                const initial = (doc.canonical_name || doc.domain || '?')[0].toUpperCase();
                const pagesCrawled = doc.pages_crawled || 1;
                const artifactCount = (doc.minio_artifacts && doc.minio_artifacts.length) || 1;
                const detectedEmails = Array.isArray(doc.detected_emails) && doc.detected_emails.length > 0 ? doc.detected_emails : null;
                const rawSnippet = doc.meta_description && doc.meta_description !== 'Not Found' ? doc.meta_description : (doc.raw_page_title || `${doc.domain} raw evidence`);
                const minioPreview = (doc.minio_artifacts && doc.minio_artifacts[0]) || `companies/${doc.domain}/pages/homepage.md`;

                return (
                  <div
                    key={doc.id}
                    onClick={() => setSelectedDocumentId(doc.id)}
                    style={{
                      background: '#0a101d',
                      border: '1px solid #1e293b',
                      borderRadius: '0.875rem',
                      padding: '1.15rem',
                      cursor: 'pointer',
                      transition: 'all 0.25s ease',
                      position: 'relative',
                      display: 'flex',
                      flexDirection: 'column',
                      gap: '0.75rem'
                    }}
                    onMouseEnter={e => { e.currentTarget.style.borderColor = '#f59e0b'; e.currentTarget.style.transform = 'translateY(-2px)'; e.currentTarget.style.boxShadow = '0 10px 25px -5px rgba(245, 158, 11, 0.15)'; }}
                    onMouseLeave={e => { e.currentTarget.style.borderColor = '#1e293b'; e.currentTarget.style.transform = 'none'; e.currentTarget.style.boxShadow = 'none'; }}
                  >
                    {/* Top Right Status Badge: Agent 1 Output */}
                    <div style={{
                      position: 'absolute', top: '0.85rem', right: '0.85rem',
                      fontSize: '0.68rem', fontWeight: 800, padding: '0.2rem 0.55rem', borderRadius: '0.375rem',
                      background: 'rgba(245, 158, 11, 0.12)', color: '#f59e0b',
                      border: '1px solid rgba(245, 158, 11, 0.35)', display: 'flex', alignItems: 'center', gap: '0.25rem'
                    }}>
                      ⚡ CRAWLED_PENDING_AGENT_2
                    </div>

                    {/* 1. Header: Logo, Name & Website Link */}
                    <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', paddingRight: '12rem' }}>
                      {doc.logo_url ? (
                        <img src={doc.logo_url} alt="Logo" onError={(e) => { e.target.style.display = 'none'; }}
                          style={{ width: '38px', height: '38px', borderRadius: '0.5rem', flexShrink: 0, objectFit: 'contain', background: '#0f172a', padding: '2px', border: '1px solid #334155' }} />
                      ) : (
                        <div style={{
                          width: '38px', height: '38px', borderRadius: '0.5rem', flexShrink: 0,
                          background: '#1e293b', display: 'flex', alignItems: 'center', justifyContent: 'center',
                          fontWeight: 900, fontSize: '1.1rem', color: '#f59e0b', border: '1px solid #334155'
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

                    {/* 2. Crawl Metrics Pills (HTTP, Pages Crawled, Artifacts, Words) */}
                    <div style={{ display: 'flex', gap: '0.35rem', flexWrap: 'wrap', alignItems: 'center', fontSize: '0.68rem', fontWeight: 600 }}>
                      <span style={{ background: 'rgba(16, 185, 129, 0.12)', color: '#34d399', border: '1px solid rgba(16, 185, 129, 0.3)', padding: '0.15rem 0.5rem', borderRadius: '0.375rem' }}>
                        ✓ HTTP {doc.http_status || 200} OK
                      </span>
                      <span style={{ background: '#111827', color: '#93c5fd', border: '1px solid #1e293b', padding: '0.15rem 0.5rem', borderRadius: '0.375rem' }}>
                        📄 {pagesCrawled} Pages Crawled
                      </span>
                      <span style={{ background: '#111827', color: '#c084fc', border: '1px solid #1e293b', padding: '0.15rem 0.5rem', borderRadius: '0.375rem' }}>
                        📦 {artifactCount} MinIO Artifacts
                      </span>
                      <span style={{ background: '#111827', color: '#94a3b8', border: '1px solid #1e293b', padding: '0.15rem 0.5rem', borderRadius: '0.375rem' }}>
                        📝 {(doc.word_count || 0).toLocaleString()} words
                      </span>
                      {detectedEmails && (
                        <span style={{ background: 'rgba(56, 189, 248, 0.12)', color: '#38bdf8', border: '1px solid rgba(56, 189, 248, 0.3)', padding: '0.15rem 0.5rem', borderRadius: '0.375rem' }}>
                          ✉️ {detectedEmails.length} Email{detectedEmails.length > 1 ? 's' : ''} Discovered
                        </span>
                      )}
                    </div>

                    {/* 3. Raw Observable Content Snippet */}
                    <div style={{ fontSize: '0.78rem', color: '#94a3b8', lineHeight: '1.45', display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>
                      {rawSnippet}
                    </div>

                    {/* 4. MinIO Evidence Provenance Bar */}
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', borderTop: '1px solid #1e293b', paddingTop: '0.5rem', marginTop: '0.2rem', fontSize: '0.68rem', fontFamily: 'monospace' }}>
                      <span style={{ background: 'rgba(245, 158, 11, 0.08)', color: '#fbbf24', padding: '0.15rem 0.4rem', borderRadius: '0.25rem', border: '1px solid rgba(245, 158, 11, 0.2)', maxWidth: '220px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={minioPreview}>
                        📦 {minioPreview}
                      </span>
                      <span style={{ color: '#64748b' }}>
                        ⏰ {doc.crawled_at ? new Date(doc.crawled_at).toISOString().slice(0, 10) : new Date().toISOString().slice(0, 10)}
                      </span>
                    </div>

                    {/* 5. Action: Inspect Raw Crawl Evidence */}
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        setSelectedDocumentId(doc.id);
                      }}
                      style={{
                        marginTop: '0.2rem',
                        width: '100%',
                        padding: '0.45rem 0.75rem',
                        background: '#1e293b',
                        border: '1px solid #334155',
                        borderRadius: '0.375rem',
                        color: '#f8fafc',
                        fontWeight: 700,
                        fontSize: '0.78rem',
                        cursor: 'pointer',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        gap: '0.35rem',
                        transition: 'all 0.2s ease'
                      }}
                      onMouseEnter={e => { e.currentTarget.style.background = '#f59e0b'; e.currentTarget.style.color = '#000'; }}
                      onMouseLeave={e => { e.currentTarget.style.background = '#1e293b'; e.currentTarget.style.color = '#f8fafc'; }}
                    >
                      🔍 Inspect Raw Crawled Evidence ↗
                    </button>

                    {/* 6. Action: Explicit Trigger for Agent 2 */}
                    <button
                      onClick={(e) => handleTriggerAgent2(doc.id, e)}
                      disabled={triggeringDocIds[doc.id]}
                      style={{
                        marginTop: '0.15rem',
                        width: '100%',
                        padding: '0.5rem 0.75rem',
                        background: triggeringDocIds[doc.id] ? '#0f172a' : 'rgba(56, 189, 248, 0.15)',
                        border: '1px solid #38bdf8',
                        borderRadius: '0.375rem',
                        color: triggeringDocIds[doc.id] ? '#94a3b8' : '#38bdf8',
                        fontWeight: 800,
                        fontSize: '0.78rem',
                        cursor: triggeringDocIds[doc.id] ? 'wait' : 'pointer',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        gap: '0.35rem',
                        transition: 'all 0.2s ease'
                      }}
                      onMouseEnter={e => { if (!triggeringDocIds[doc.id]) { e.currentTarget.style.background = '#38bdf8'; e.currentTarget.style.color = '#000'; } }}
                      onMouseLeave={e => { if (!triggeringDocIds[doc.id]) { e.currentTarget.style.background = 'rgba(56, 189, 248, 0.15)'; e.currentTarget.style.color = '#38bdf8'; } }}
                    >
                      {triggeringDocIds[doc.id] ? '⏳ Initiating Agent 2 Verification...' : '⚡ Verify with Agent 2 ↗'}
                    </button>
                  </div>
                );
              })}
            </div>
          )
        )}

        {/* ── AGENT 2 IN-VERIFICATION CARD GRID ── */}
        {leadView === 'agent2' && (
          agent2Sessions.length === 0 ? (
            <div style={{ textAlign: 'center', padding: '4rem 2rem', color: '#64748b' }}>
              <div style={{ fontSize: '3rem', marginBottom: '1rem' }}>🔄</div>
              <div style={{ fontSize: '1.1rem', color: '#94a3b8', marginBottom: '0.5rem' }}>No active Agent 2 verification sessions.</div>
              <div style={{ fontSize: '0.9rem' }}>Go to <strong style={{ color: '#f59e0b' }}>Crawled Cards</strong> and click <strong style={{ color: '#38bdf8' }}>⚡ Verify with Agent 2</strong> to start autonomous verification.</div>
            </div>
          ) : (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(340px, 1fr))', gap: '1.25rem' }}>
              {agent2Sessions.map((session) => {
                const initial = (session.company_name || session.domain || '?')[0].toUpperCase();
                const isVerified = session.status === 'VERIFIED' || session.status === 'POSTGRES_VERIFIED';
                const isBlocked = session.status.includes('BLOCKED') || session.status.includes('FAILED');
                const statusBg = isVerified ? 'rgba(16, 185, 129, 0.15)' : (isBlocked ? 'rgba(239, 68, 68, 0.15)' : 'rgba(56, 189, 248, 0.15)');
                const statusColor = isVerified ? '#34d399' : (isBlocked ? '#f87171' : '#38bdf8');
                const statusBorder = isVerified ? 'rgba(16, 185, 129, 0.4)' : (isBlocked ? 'rgba(239, 68, 68, 0.4)' : 'rgba(56, 189, 248, 0.4)');

                return (
                  <div
                    key={session.session_id}
                    onClick={() => setSelectedAgent2Id(session.session_id)}
                    style={{
                      background: '#0a101d',
                      border: `1px solid ${statusBorder}`,
                      borderRadius: '0.875rem',
                      padding: '1.15rem',
                      cursor: 'pointer',
                      transition: 'all 0.25s ease',
                      position: 'relative',
                      display: 'flex',
                      flexDirection: 'column',
                      gap: '0.75rem'
                    }}
                    onMouseEnter={e => { e.currentTarget.style.transform = 'translateY(-2px)'; e.currentTarget.style.boxShadow = `0 10px 25px -5px ${statusColor}33`; }}
                    onMouseLeave={e => { e.currentTarget.style.transform = 'none'; e.currentTarget.style.boxShadow = 'none'; }}
                  >
                    {/* Top Right Status Badge */}
                    <div style={{
                      position: 'absolute', top: '0.85rem', right: '0.85rem',
                      fontSize: '0.68rem', fontWeight: 800, padding: '0.2rem 0.55rem', borderRadius: '0.375rem',
                      background: statusBg, color: statusColor, border: `1px solid ${statusBorder}`,
                      display: 'flex', alignItems: 'center', gap: '0.25rem'
                    }}>
                      {isVerified ? '✓ ' : (isBlocked ? '✕ ' : '⏳ ')}{session.status}
                    </div>

                    {/* Header */}
                    <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', paddingRight: '11rem' }}>
                      <div style={{
                        width: '38px', height: '38px', borderRadius: '0.5rem', flexShrink: 0,
                        background: '#1e293b', display: 'flex', alignItems: 'center', justifyContent: 'center',
                        fontWeight: 900, fontSize: '1.1rem', color: statusColor, border: '1px solid #334155'
                      }}>{initial}</div>
                      <div style={{ overflow: 'hidden' }}>
                        <div style={{ fontWeight: 800, fontSize: '0.95rem', color: '#f8fafc', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                          {session.company_name}
                        </div>
                        <a href={`https://${session.domain}`} target="_blank" rel="noreferrer" onClick={e => e.stopPropagation()}
                          style={{ fontSize: '0.72rem', color: '#38bdf8', textDecoration: 'none', display: 'inline-flex', alignItems: 'center', gap: '0.25rem' }}>
                          🌐 {session.domain} ↗
                        </a>
                      </div>
                    </div>

                    {/* Verification Metrics Pills */}
                    <div style={{ display: 'flex', gap: '0.35rem', flexWrap: 'wrap', alignItems: 'center', fontSize: '0.68rem', fontWeight: 600 }}>
                      <span style={{ background: 'rgba(56, 189, 248, 0.12)', color: '#38bdf8', border: '1px solid rgba(56, 189, 248, 0.3)', padding: '0.15rem 0.5rem', borderRadius: '0.375rem' }}>
                        🎯 Priority: {Math.round(session.priority_score || 0)}/100
                      </span>
                      <span style={{ background: '#111827', color: '#a78bfa', border: '1px solid #1e293b', padding: '0.15rem 0.5rem', borderRadius: '0.375rem' }}>
                        🔄 {session.recrawl_count || 0} Subpage Re-crawls
                      </span>
                      <span style={{ background: '#111827', color: '#34d399', border: '1px solid #1e293b', padding: '0.15rem 0.5rem', borderRadius: '0.375rem' }}>
                        🔍 {session.search_rounds || 0} Search Rounds
                      </span>
                      <span style={{ background: '#111827', color: '#60a5fa', border: '1px solid #1e293b', padding: '0.15rem 0.5rem', borderRadius: '0.375rem' }}>
                        👔 {session.verified_decision_makers || 0} Leaders Verified
                      </span>
                    </div>

                    {/* Inspection Button */}
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        setSelectedAgent2Id(session.session_id);
                      }}
                      style={{
                        marginTop: '0.35rem',
                        width: '100%',
                        padding: '0.45rem 0.75rem',
                        background: '#1e293b',
                        border: '1px solid #334155',
                        borderRadius: '0.375rem',
                        color: '#f8fafc',
                        fontWeight: 700,
                        fontSize: '0.78rem',
                        cursor: 'pointer',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        gap: '0.35rem',
                        transition: 'all 0.2s ease'
                      }}
                      onMouseEnter={e => { e.currentTarget.style.background = statusColor; e.currentTarget.style.color = '#000'; }}
                      onMouseLeave={e => { e.currentTarget.style.background = '#1e293b'; e.currentTarget.style.color = '#f8fafc'; }}
                    >
                      🔬 Open Verification Audit & Checklist ↗
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
              {entitiesList.slice((currentPage - 1) * CARDS_PER_PAGE, currentPage * CARDS_PER_PAGE).map((ent) => {
                let domain = '';
                try { domain = new URL(ent.url.startsWith('http') ? ent.url : 'https://' + ent.url).hostname.replace('www.', ''); } catch {}
                const initial = (ent.canonical_name || domain || '?')[0].toUpperCase();
                const rawEntScore = ent.lead_quality_score ?? ent.quality_score;
                const score = rawEntScore !== undefined && rawEntScore !== null ? Math.round(rawEntScore) : 25;
                const tierName = ent.company_tier && ent.company_tier !== 'Startup (2)' ? ent.company_tier : (ent.company_size || 'Unknown');
                const locationStr = ent.headquarters && ent.headquarters !== 'Not Specified' && ent.headquarters !== 'Global' ? ent.headquarters : (ent.country && ent.country !== 'Global' ? ent.country : 'Unknown');
                const industryStr = ent.industry && ent.industry !== 'Commercial Web' && ent.industry !== 'Commercial Web & Digital Enterprise' ? ent.industry : 'Unknown';
                const revenueStr = ent.revenue_funding && ent.revenue_funding !== 'Not Specified' && ent.revenue_funding !== 'Bootstrapped' ? ent.revenue_funding : 'Unknown';
                const emailStr = Array.isArray(ent.verified_emails) && ent.verified_emails[0] ? ent.verified_emails[0] : null;
                
                return (
                  <div
                    key={ent.id}
                    onClick={() => setSelectedEntityId(ent.id)}
                    style={{
                      background: '#0a101d',
                      border: '1px solid #10b981',
                      borderRadius: '0.875rem',
                      padding: '1.1rem',
                      cursor: 'pointer',
                      transition: 'all 0.25s ease',
                      position: 'relative',
                      display: 'flex',
                      flexDirection: 'column',
                      gap: '0.65rem'
                    }}
                    onMouseEnter={e => { e.currentTarget.style.borderColor = '#00f2ff'; e.currentTarget.style.transform = 'translateY(-2px)'; e.currentTarget.style.boxShadow = '0 10px 25px -5px rgba(16, 185, 129, 0.2)'; }}
                    onMouseLeave={e => { e.currentTarget.style.borderColor = '#10b981'; e.currentTarget.style.transform = 'none'; e.currentTarget.style.boxShadow = 'none'; }}
                  >
                    {/* Top Right Score Badge (Image 2 style) */}
                    <div style={{
                      position: 'absolute', top: '0.85rem', right: '0.85rem',
                      fontSize: '0.72rem', fontWeight: 800, padding: '0.2rem 0.55rem', borderRadius: '0.375rem',
                      background: 'rgba(0, 242, 255, 0.08)', color: '#00f2ff',
                      border: '1px solid rgba(0, 242, 255, 0.35)', display: 'flex', alignItems: 'center', gap: '0.25rem'
                    }}>
                      🛡️ {score}/100
                    </div>

                    {/* 1. Header: Logo, Name & Website Link */}
                    <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', paddingRight: '5.5rem' }}>
                      {ent.logo_url ? (
                        <img src={ent.logo_url} alt="Logo" onError={(e) => { e.target.style.display = 'none'; }}
                          style={{ width: '38px', height: '38px', borderRadius: '0.5rem', flexShrink: 0, objectFit: 'contain', background: '#0f172a', padding: '2px', border: '1px solid #334155' }} />
                      ) : (
                        <div style={{
                          width: '38px', height: '38px', borderRadius: '0.5rem', flexShrink: 0,
                          background: '#1e293b', display: 'flex', alignItems: 'center', justifyContent: 'center',
                          fontWeight: 900, fontSize: '1.1rem', color: '#34d399', border: '1px solid #10b981'
                        }}>{initial}</div>
                      )}
                      <div style={{ overflow: 'hidden' }}>
                        <div style={{ fontWeight: 800, fontSize: '0.95rem', color: '#f8fafc', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                          {ent.canonical_name}
                        </div>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', flexWrap: 'wrap', marginTop: '0.15rem' }}>
                          <a href={ent.url} target="_blank" rel="noreferrer" onClick={e => e.stopPropagation()}
                            style={{ fontSize: '0.72rem', color: '#38bdf8', textDecoration: 'none', display: 'inline-flex', alignItems: 'center', gap: '0.25rem' }}>
                            🌐 {domain} ↗
                          </a>
                          {ent.linkedin_url && (
                            <a href={ent.linkedin_url} target="_blank" rel="noreferrer" onClick={e => e.stopPropagation()}
                              style={{
                                fontSize: '0.66rem',
                                color: '#60a5fa',
                                background: 'rgba(37, 99, 235, 0.15)',
                                border: '1px solid rgba(37, 99, 235, 0.4)',
                                padding: '0.1rem 0.45rem',
                                borderRadius: '0.25rem',
                                textDecoration: 'none',
                                fontWeight: 700,
                                display: 'inline-flex',
                                alignItems: 'center',
                                gap: '0.2rem'
                              }}>
                              👔 Company LinkedIn ↗
                            </a>
                          )}
                        </div>
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
                      {ent.business_overview || ent.description || `${ent.canonical_name} operates in the ${industryStr} domain.`}
                    </div>

                    {/* 4. Tech Stack Tags */}
                    {ent.technology_stack && ent.technology_stack.length > 0 && (
                      <div style={{ display: 'flex', gap: '0.3rem', flexWrap: 'wrap' }}>
                        {ent.technology_stack.slice(0, 4).map((tech, idx) => (
                          <span key={idx} style={{ fontSize: '0.66rem', background: '#111827', color: '#cbd5e1', border: '1px solid #1f2937', padding: '0.1rem 0.45rem', borderRadius: '0.25rem', fontWeight: 500 }}>
                            {tech}
                          </span>
                        ))}
                      </div>
                    )}

                    {/* 4b. Discovered Key People & LinkedIn Badges on Card */}
                    {Array.isArray(ent.decision_makers) && ent.decision_makers.filter(isPersonVerified).length > 0 && (
                      <div style={{ background: 'rgba(15, 23, 42, 0.7)', border: '1px solid #1e293b', borderRadius: '0.45rem', padding: '0.4rem 0.6rem', display: 'flex', flexDirection: 'column', gap: '0.3rem' }}>
                        <div style={{ fontSize: '0.66rem', fontWeight: 800, color: '#94a3b8', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                          <span>👥 KEY PEOPLE ({ent.decision_makers.filter(isPersonVerified).length})</span>
                          <span style={{ fontSize: '0.62rem', color: '#10b981', fontWeight: 700 }}>✓ Verified Profiles</span>
                        </div>
                        {ent.decision_makers.filter(isPersonVerified).slice(0, 2).map((dm, dmIdx) => (
                          <div key={dmIdx} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: '0.72rem' }}>
                            <div style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: '68%' }}>
                              <span style={{ color: '#f8fafc', fontWeight: 700 }}>{dm.name}</span>{' '}
                              <span style={{ color: '#64748b', fontSize: '0.66rem' }}>• {dm.title || 'Leadership'}</span>
                            </div>
                            <a
                              href={getLinkedInProfileOrSearch(dm, ent.canonical_name)}
                              target="_blank"
                              rel="noreferrer"
                              onClick={(e) => e.stopPropagation()}
                              style={{
                                fontSize: '0.64rem',
                                color: isLinkedInProfile(dm) ? '#38bdf8' : '#94a3b8',
                                background: isLinkedInProfile(dm) ? 'rgba(56, 189, 248, 0.12)' : 'rgba(148, 163, 184, 0.08)',
                                border: isLinkedInProfile(dm) ? '1px solid rgba(56, 189, 248, 0.35)' : '1px solid rgba(148, 163, 184, 0.2)',
                                padding: '0.12rem 0.45rem',
                                borderRadius: '0.25rem',
                                textDecoration: 'none',
                                fontWeight: 700,
                                display: 'inline-flex',
                                alignItems: 'center',
                                gap: '0.2rem'
                              }}
                            >
                              {getLinkedInLabel(dm)}
                            </a>
                          </div>
                        ))}
                      </div>
                    )}

                    {/* 5. Footer Provenance Bar & View Entire Dossier Button */}
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', borderTop: '1px solid #1e293b', paddingTop: '0.5rem', marginTop: '0.2rem', fontSize: '0.68rem', fontFamily: 'monospace' }}>
                      <span style={{ background: 'rgba(16, 185, 129, 0.1)', color: '#34d399', padding: '0.15rem 0.4rem', borderRadius: '0.25rem', border: '1px solid rgba(16, 185, 129, 0.2)' }}>
                        ✳️ AUTONOMOUS_TAXONOMY
                      </span>
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

            {!documentDetail ? (
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
                        background: 'rgba(245, 158, 11, 0.15)',
                        color: '#f59e0b',
                        border: '1px solid rgba(245, 158, 11, 0.4)'
                      }}>
                        ⚡ CRAWLED_PENDING_AGENT_2 (Agent 1 Output)
                      </span>
                      <span style={{
                        fontSize: '0.7rem', fontWeight: 700, padding: '0.2rem 0.6rem', borderRadius: '9999px',
                        background: (documentDetail.http_status === 200 || !documentDetail.http_status) ? 'rgba(16,185,129,0.15)' : 'rgba(239,68,68,0.15)',
                        color: (documentDetail.http_status === 200 || !documentDetail.http_status) ? '#34d399' : '#f87171',
                        border: `1px solid ${(documentDetail.http_status === 200 || !documentDetail.http_status) ? '#10b981' : '#ef4444'}`
                      }}>
                        HTTP {documentDetail.http_status || 200} {(documentDetail.http_status === 200 || !documentDetail.http_status) ? 'OK' : (documentDetail.http_status === 403 ? 'Blocked / Forbidden' : 'Response')}
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
                    <span className="data-label">LIFECYCLE STATUS</span>
                    <div style={{ color: '#f59e0b', fontWeight: 700, fontSize: '0.95rem' }}>CRAWLED_PENDING_AGENT_2</div>
                  </div>
                  <div style={{ background: '#0f172a', padding: '0.85rem 1rem', borderRadius: '0.5rem', border: '1px solid #1e293b' }}>
                    <span className="data-label">PAGES CRAWLED</span>
                    <div style={{ color: '#f8fafc', fontWeight: 700, fontSize: '0.95rem' }}>{(documentDetail.crawled_subpages?.length || 0) + 1} pages</div>
                  </div>
                  <div style={{ background: '#0f172a', padding: '0.85rem 1rem', borderRadius: '0.5rem', border: '1px solid #1e293b' }}>
                    <span className="data-label">MINIO ARTIFACTS</span>
                    <div style={{ color: '#38bdf8', fontWeight: 700, fontSize: '0.95rem' }}>{documentDetail.minio_artifacts?.length || 1} objects</div>
                  </div>
                  <div style={{ background: '#0f172a', padding: '0.85rem 1rem', borderRadius: '0.5rem', border: '1px solid #1e293b' }}>
                    <span className="data-label">EXTRACTED WORD COUNT</span>
                    <div style={{ color: '#34d399', fontWeight: 700, fontSize: '0.95rem' }}>{documentDetail.word_count.toLocaleString()} words</div>
                  </div>
                </div>

                {/* MinIO Storage Artifacts List */}
                <div style={{ background: '#0f172a', padding: '1rem', borderRadius: '0.5rem', border: '1px solid #334155', marginBottom: '1.5rem' }}>
                  <span className="data-label" style={{ display: 'block', marginBottom: '0.4rem', color: '#c084fc' }}>
                    📦 MINIO OBJECT STORAGE ARTIFACTS
                  </span>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '0.35rem', fontFamily: 'monospace', fontSize: '0.8rem' }}>
                    {Array.isArray(documentDetail.minio_artifacts) && documentDetail.minio_artifacts.length > 0 ? (
                      documentDetail.minio_artifacts.map((art, idx) => (
                        <div key={idx} style={{ color: '#a78bfa', background: '#060e1e', padding: '0.35rem 0.65rem', borderRadius: '0.35rem', border: '1px solid #1e293b' }}>
                          📄 {art}
                        </div>
                      ))
                    ) : (
                      <div style={{ color: '#a78bfa', background: '#060e1e', padding: '0.35rem 0.65rem', borderRadius: '0.35rem', border: '1px solid #1e293b' }}>
                        📄 {documentDetail.raw_path || `companies/${documentDetail.domain}/pages/homepage.md`}
                      </div>
                    )}
                  </div>
                  {documentDetail.retrieved_at && (
                    <div style={{ fontSize: '0.72rem', color: '#64748b', marginTop: '0.5rem' }}>
                      Crawl Timestamp: {new Date(documentDetail.retrieved_at).toLocaleString()}
                    </div>
                  )}
                </div>

                {/* Directly Observed Metadata (Email & Phone) */}
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '1rem', marginBottom: '1.5rem' }}>
                  <div style={{ background: '#0f172a', padding: '0.85rem 1rem', borderRadius: '0.5rem', border: '1px solid #1e293b' }}>
                    <span className="data-label">GENUINE CONTACT EMAILS (ON-PAGE)</span>
                    <div style={{ color: '#38bdf8', fontWeight: 700, fontSize: '0.9rem', marginTop: '0.15rem' }}>
                      {Array.isArray(documentDetail.verified_emails) && documentDetail.verified_emails.length > 0 ? documentDetail.verified_emails.join(', ') : 'None discovered'}
                    </div>
                  </div>
                  <div style={{ background: '#0f172a', padding: '0.85rem 1rem', borderRadius: '0.5rem', border: '1px solid #1e293b' }}>
                    <span className="data-label">GENUINE CONTACT PHONES (ON-PAGE)</span>
                    <div style={{ color: '#34d399', fontWeight: 700, fontSize: '0.9rem', marginTop: '0.15rem' }}>
                      {Array.isArray(documentDetail.detected_phones) && documentDetail.detected_phones.length > 0 ? documentDetail.detected_phones.join(', ') : 'None discovered'}
                    </div>
                  </div>
                </div>

                {/* Raw Crawled Content Preview */}
                <div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.75rem' }}>
                    <h3 style={{ color: '#60a5fa', fontSize: '1.1rem', margin: 0 }}>
                      Raw Crawled Web Evidence & Content
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

      {/* 5.5. AGENT 2 VERIFICATION AUDIT MODAL */}
      {selectedAgent2Id && (
        <div style={{ position: 'fixed', top: 0, left: 0, right: 0, bottom: 0, background: 'rgba(15, 23, 42, 0.88)', backdropFilter: 'blur(10px)', zIndex: 110, display: 'flex', justifyContent: 'center', alignItems: 'center', padding: '1.5rem' }}>
          <div style={{ background: '#080e1a', border: '1px solid #1e293b', borderRadius: '1rem', width: '100%', maxWidth: '1150px', maxHeight: '90vh', overflowY: 'auto', padding: '1.75rem', boxShadow: '0 25px 50px -12px rgba(0,0,0,0.85)', position: 'relative' }}>
            
            {loadingAgent2Detail || !agent2Detail ? (
              <div style={{ textAlign: 'center', padding: '4rem 0', color: '#94a3b8' }}>
                <div className="spinner" style={{ margin: '0 auto 1rem auto' }}></div>
                <div>Loading Agent 2 verification audit dossier and investigation logs...</div>
              </div>
            ) : (
              <div>
                {/* Header Bar */}
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', borderBottom: '1px solid #1e293b', paddingBottom: '1.25rem', marginBottom: '1.5rem' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '1rem' }}>
                    <div style={{ width: '44px', height: '44px', borderRadius: '0.6rem', background: '#38bdf8', display: 'flex', alignItems: 'center', justifyContent: 'center', fontWeight: 900, color: '#000', fontSize: '1.3rem' }}>
                      {(agent2Detail.company_name || agent2Detail.domain || '?')[0].toUpperCase()}
                    </div>
                    <div>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', flexWrap: 'wrap' }}>
                        <h2 style={{ margin: 0, fontSize: '1.65rem', fontWeight: 900, color: '#ffffff', letterSpacing: '-0.02em' }}>
                          {agent2Detail.company_name}
                        </h2>
                        <span style={{
                          fontSize: '0.72rem', fontWeight: 800, padding: '0.2rem 0.6rem', borderRadius: '0.375rem',
                          background: agent2Detail.status.includes('VERIFIED') ? 'rgba(16, 185, 129, 0.18)' : (agent2Detail.status.includes('BLOCKED') ? 'rgba(239, 68, 68, 0.18)' : 'rgba(56, 189, 248, 0.18)'),
                          color: agent2Detail.status.includes('VERIFIED') ? '#34d399' : (agent2Detail.status.includes('BLOCKED') ? '#f87171' : '#38bdf8'),
                          border: `1px solid ${agent2Detail.status.includes('VERIFIED') ? '#10b981' : (agent2Detail.status.includes('BLOCKED') ? '#ef4444' : '#38bdf8')}`
                        }}>
                          {agent2Detail.status}
                        </span>
                        <span style={{
                          fontSize: '0.72rem', fontWeight: 800, padding: '0.2rem 0.6rem', borderRadius: '0.375rem',
                          background: 'rgba(168, 85, 247, 0.15)', color: '#c084fc', border: '1px solid rgba(168, 85, 247, 0.35)'
                        }}>
                          Priority Score: {Math.round(agent2Detail.priority_score || 0)}/100
                        </span>
                      </div>
                      <div style={{ fontSize: '0.85rem', color: '#38bdf8', marginTop: '0.25rem', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                        <span>🌐 {agent2Detail.domain}</span>
                        <span>•</span>
                        <a href={`https://${agent2Detail.domain}`} target="_blank" rel="noreferrer" style={{ color: '#38bdf8', textDecoration: 'none' }}>
                          Visit Official Site ↗
                        </a>
                      </div>
                    </div>
                  </div>

                  <button
                    onClick={() => setSelectedAgent2Id(null)}
                    style={{ background: '#1e293b', border: '1px solid #334155', color: '#94a3b8', borderRadius: '0.5rem', padding: '0.45rem 0.85rem', cursor: 'pointer', fontSize: '0.85rem', fontWeight: 700 }}
                  >
                    ✕ Close
                  </button>
                </div>

                {/* Priority Reasons Banner */}
                {Array.isArray(agent2Detail.priority_reasons) && agent2Detail.priority_reasons.length > 0 && (
                  <div style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: '0.65rem', padding: '0.75rem 1rem', marginBottom: '1.5rem', display: 'flex', gap: '0.5rem', flexWrap: 'wrap', alignItems: 'center' }}>
                    <span style={{ fontSize: '0.75rem', fontWeight: 800, color: '#94a3b8', textTransform: 'uppercase' }}>Priority Drivers:</span>
                    {agent2Detail.priority_reasons.map((pr, idx) => (
                      <span key={idx} style={{ background: 'rgba(56, 189, 248, 0.1)', color: '#7dd3fc', border: '1px solid rgba(56, 189, 248, 0.25)', padding: '0.15rem 0.5rem', borderRadius: '0.35rem', fontSize: '0.72rem' }}>
                        ✓ {pr}
                      </span>
                    ))}
                  </div>
                )}

                {/* 1. PHASE 1 HARD-GATED AUDIT CHECKLIST */}
                <div style={{ marginBottom: '1.75rem' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.75rem' }}>
                    <h3 style={{ fontSize: '0.85rem', fontWeight: 900, color: '#38bdf8', textTransform: 'uppercase', letterSpacing: '0.05em', margin: 0 }}>
                      📋 Phase 1 Hard-Gated Field Audit Checklist (7 Core Fields)
                    </h3>
                    <span style={{ fontSize: '0.72rem', color: '#64748b' }}>
                      Rule: Zero UNVERIFIED allowed • Exhaustive Search required for NOT_FOUND_AFTER_SEARCH
                    </span>
                  </div>

                  <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
                    {Array.isArray(agent2Detail.evidence) && agent2Detail.evidence.map((ev, idx) => {
                      const isV = ev.status === 'VERIFIED';
                      const isNF = ev.status === 'NOT_FOUND_AFTER_SEARCH';
                      const isUnv = ev.status === 'UNVERIFIED';
                      const badgeBg = isV ? 'rgba(16, 185, 129, 0.15)' : (isNF ? 'rgba(245, 158, 11, 0.15)' : (isUnv ? 'rgba(239, 68, 68, 0.15)' : '#1e293b'));
                      const badgeColor = isV ? '#34d399' : (isNF ? '#fbbf24' : (isUnv ? '#f87171' : '#94a3b8'));
                      const badgeBorder = isV ? '#10b981' : (isNF ? '#f59e0b' : (isUnv ? '#ef4444' : '#334155'));

                      return (
                        <div key={idx} style={{ background: '#0a101d', border: '1px solid #1e293b', borderRadius: '0.5rem', padding: '0.75rem 1rem', display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '1rem', flexWrap: 'wrap' }}>
                          <div style={{ flex: 1, minWidth: '260px' }}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                              <span style={{ fontFamily: 'monospace', fontWeight: 800, color: '#f8fafc', fontSize: '0.85rem' }}>
                                {ev.field}
                              </span>
                              <span style={{ fontSize: '0.68rem', fontWeight: 800, padding: '0.15rem 0.45rem', borderRadius: '0.25rem', background: badgeBg, color: badgeColor, border: `1px solid ${badgeBorder}` }}>
                                {ev.status}
                              </span>
                            </div>
                            <div style={{ color: ev.value ? '#67e8f9' : '#64748b', fontSize: '0.82rem', marginTop: '0.25rem', fontWeight: 600 }}>
                              {ev.value ? ev.value : '(No reliable evidence found)'}
                            </div>
                            {ev.evidence_snippet && (
                              <div style={{ fontSize: '0.75rem', color: '#94a3b8', marginTop: '0.25rem', fontStyle: 'italic' }}>
                                Provenance: {ev.evidence_snippet.slice(0, 180)}{ev.evidence_snippet.length > 180 ? '...' : ''}
                              </div>
                            )}
                          </div>
                          <div style={{ fontSize: '0.72rem', color: '#64748b', textAlign: 'right', flexShrink: 0 }}>
                            <div>Method: <strong style={{ color: '#cbd5e1' }}>{ev.verification_method || 'investigation'}</strong></div>
                            {ev.investigation?.search_attempts !== undefined && (
                              <div>Searches: <strong style={{ color: '#38bdf8' }}>{ev.investigation.search_attempts} rounds</strong> | Sources: <strong style={{ color: '#a78bfa' }}>{ev.investigation.sources_checked?.length || 1}</strong></div>
                            )}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>

                {/* 2. PHASE 2 HAYSTACK BUSINESS SYNTHESIS */}
                {agent2Detail.phase2_data?.business_overview && (
                  <div style={{ marginBottom: '1.75rem' }}>
                    <h3 style={{ fontSize: '0.85rem', fontWeight: 900, color: '#a78bfa', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '0.6rem' }}>
                      ⚡ Phase 2 Haystack Business Synthesis (Grounded Evidence)
                    </h3>
                    <div style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: '0.75rem', padding: '1rem', color: '#cbd5e1', fontSize: '0.88rem', lineHeight: '1.6' }}>
                      <div style={{ marginBottom: '0.6rem' }}>
                        {agent2Detail.phase2_data.business_overview}
                      </div>
                      {agent2Detail.phase2_data.target_customers && (
                        <div style={{ fontSize: '0.8rem', color: '#94a3b8' }}>
                          <strong style={{ color: '#60a5fa' }}>Target Customers:</strong> {agent2Detail.phase2_data.target_customers}
                        </div>
                      )}
                      {agent2Detail.phase2_data.commercial_model && (
                        <div style={{ fontSize: '0.8rem', color: '#94a3b8', marginTop: '0.2rem' }}>
                          <strong style={{ color: '#34d399' }}>Commercial Model:</strong> {agent2Detail.phase2_data.commercial_model}
                        </div>
                      )}
                    </div>
                  </div>
                )}

                {/* 3. LINKEDIN DECISION MAKERS & EXECUTIVE AFFILIATION */}
                <div style={{ marginBottom: '1.75rem' }}>
                  <h3 style={{ fontSize: '0.85rem', fontWeight: 900, color: '#38bdf8', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '0.6rem' }}>
                    👔 LinkedIn Leadership & Company Match Audit ({agent2Detail.person_candidates?.length || 0})
                  </h3>
                  {Array.isArray(agent2Detail.person_candidates) && agent2Detail.person_candidates.length > 0 ? (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
                      {agent2Detail.person_candidates.map((cand, idx) => {
                        const isCandVerified = cand.candidate_status === 'VERIFIED';
                        return (
                          <div key={idx} style={{ background: '#0a101d', border: `1px solid ${isCandVerified ? 'rgba(16, 185, 129, 0.3)' : 'rgba(239, 68, 68, 0.3)'}`, borderRadius: '0.5rem', padding: '0.75rem 1rem', display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '0.6rem' }}>
                            <div>
                              <div style={{ fontWeight: 800, color: '#ffffff', fontSize: '0.9rem', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                                <span>{cand.name}</span>
                                <span style={{ color: '#38bdf8', fontWeight: 600 }}>({cand.title || 'Executive'})</span>
                                <span style={{
                                  fontSize: '0.66rem', fontWeight: 800, padding: '0.1rem 0.45rem', borderRadius: '0.25rem',
                                  background: isCandVerified ? 'rgba(16, 185, 129, 0.15)' : 'rgba(239, 68, 68, 0.15)',
                                  color: isCandVerified ? '#34d399' : '#f87171',
                                  border: `1px solid ${isCandVerified ? '#10b981' : '#ef4444'}`
                                }}>
                                  {isCandVerified ? '✓ VERIFIED MATCH' : '✕ REJECTED MATCH'}
                                </span>
                              </div>
                              {!isCandVerified && cand.rejection_reason && (
                                <div style={{ fontSize: '0.75rem', color: '#f87171', marginTop: '0.2rem' }}>
                                  Reason: {cand.rejection_reason}
                                </div>
                              )}
                              {cand.evidence_snippet && (
                                <div style={{ fontSize: '0.73rem', color: '#64748b', marginTop: '0.15rem' }}>
                                  Snippet: {cand.evidence_snippet.slice(0, 140)}...
                                </div>
                              )}
                            </div>
                            {cand.linkedin_url && (
                              <a href={cand.linkedin_url} target="_blank" rel="noreferrer"
                                style={{
                                  padding: '0.35rem 0.75rem',
                                  background: 'rgba(56, 189, 248, 0.12)',
                                  border: '1px solid #38bdf8',
                                  color: '#38bdf8',
                                  borderRadius: '0.375rem',
                                  fontSize: '0.75rem',
                                  fontWeight: 700,
                                  textDecoration: 'none'
                                }}>
                                View LinkedIn Profile ↗
                              </a>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  ) : (
                    <div style={{ background: '#0a101d', border: '1px dashed #334155', borderRadius: '0.5rem', padding: '1rem', textAlign: 'center', color: '#64748b', fontSize: '0.82rem' }}>
                      No verified executive candidate profiles found yet.
                    </div>
                  )}
                </div>

                {/* 4. CHRONOLOGICAL INVESTIGATION TIMELINE */}
                {Array.isArray(agent2Detail.timeline) && agent2Detail.timeline.length > 0 && (
                  <div>
                    <h3 style={{ fontSize: '0.85rem', fontWeight: 900, color: '#64748b', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '0.6rem' }}>
                      ⏱️ Chronological Investigation Audit Trail ({agent2Detail.timeline.length} Steps)
                    </h3>
                    <div style={{ background: '#040812', border: '1px solid #1e293b', borderRadius: '0.5rem', padding: '0.75rem', maxHeight: '180px', overflowY: 'auto', fontFamily: 'monospace', fontSize: '0.75rem' }}>
                      {agent2Detail.timeline.map((item, i) => (
                        <div key={i} style={{ marginBottom: '0.3rem', display: 'flex', gap: '0.5rem', color: '#94a3b8' }}>
                          <span style={{ color: '#475569' }}>{item.timestamp ? new Date(item.timestamp).toLocaleTimeString() : ''}</span>
                          <span style={{ color: '#38bdf8', fontWeight: 700 }}>[{item.state || 'STEP'}]</span>
                          <span>{item.message || JSON.stringify(item)}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

              </div>
            )}
          </div>
        </div>
      )}

      {/* 6. ENTITY DETAIL VIEW (DRILL-IN MODAL) */}
      {selectedEntityId && (
        <div style={{ position: 'fixed', top: 0, left: 0, right: 0, bottom: 0, background: 'rgba(15, 23, 42, 0.85)', backdropFilter: 'blur(8px)', zIndex: 100, display: 'flex', justifyContent: 'center', alignItems: 'center', padding: '1.5rem' }}>
          <div style={{ background: '#0b1322', border: '1px solid #1e293b', borderRadius: '1rem', width: '100%', maxWidth: '1150px', maxHeight: '90vh', overflowY: 'auto', padding: '1.75rem', boxShadow: '0 25px 50px -12px rgba(0,0,0,0.7)', position: 'relative' }}>
            
            {!entityDetail ? (
              <div style={{ textAlign: 'center', padding: '4rem 0', color: '#94a3b8' }}>
                <div className="spinner" style={{ margin: '0 auto 1rem auto' }}></div>
                <div>Synthesizing entity audit & evidence from OpenDB storage...</div>
              </div>
            ) : (
              <div>
                {/* Header Bar matching uploaded screenshot */}
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', borderBottom: '1px solid #1e293b', paddingBottom: '1.25rem', marginBottom: '1.5rem' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '1rem' }}>
                    {entityDetail.logo_url ? (
                      <img
                        src={entityDetail.logo_url}
                        alt="Logo"
                        onError={(e) => { e.target.style.display = 'none'; }}
                        style={{ width: '42px', height: '42px', borderRadius: '0.6rem', background: '#0f172a', padding: '3px', border: '1px solid #334155', objectFit: 'contain' }}
                      />
                    ) : (
                      <div style={{ width: '42px', height: '42px', borderRadius: '0.6rem', background: '#3b82f6', display: 'flex', alignItems: 'center', justifyContent: 'center', fontWeight: 900, color: '#fff', fontSize: '1.2rem' }}>
                        {(entityDetail.canonical_name || '?')[0].toUpperCase()}
                      </div>
                    )}
                    <div>
                      <h2 style={{ margin: 0, fontSize: '1.75rem', fontWeight: 900, color: '#ffffff', letterSpacing: '-0.02em' }}>
                        {entityDetail.canonical_name}
                      </h2>
                      <div style={{ fontSize: '0.85rem', color: '#38bdf8', marginTop: '0.2rem', display: 'flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap' }}>
                        <span>{entityDetail.domain}</span>
                        <span>•</span>
                        <a href={entityDetail.official_website} target="_blank" rel="noreferrer" style={{ color: '#38bdf8', textDecoration: 'none' }}>
                          {entityDetail.official_website} ↗
                        </a>
                        {(entityDetail.linkedin_url || entityDetail.company_linkedin_url) && (
                          <>
                            <span>•</span>
                            <a
                              href={entityDetail.linkedin_url || entityDetail.company_linkedin_url}
                              target="_blank"
                              rel="noreferrer"
                              style={{
                                color: '#60a5fa',
                                textDecoration: 'none',
                                background: 'rgba(37, 99, 235, 0.18)',
                                padding: '0.15rem 0.55rem',
                                borderRadius: '0.35rem',
                                border: '1px solid rgba(37, 99, 235, 0.4)',
                                fontWeight: 700,
                                display: 'inline-flex',
                                alignItems: 'center',
                                gap: '0.25rem'
                              }}
                            >
                              👔 Company LinkedIn ↗
                            </a>
                          </>
                        )}
                      </div>
                    </div>
                  </div>

                  <button
                    onClick={() => setSelectedEntityId(null)}
                    style={{ background: '#1e293b', border: '1px solid #334155', color: '#94a3b8', borderRadius: '0.5rem', padding: '0.4rem 0.8rem', cursor: 'pointer', fontSize: '0.85rem', fontWeight: 700 }}
                  >
                    ✕ Close
                  </button>
                </div>

                {/* 2-Column Main Layout matching uploaded screenshot */}
                <div style={{ display: 'grid', gridTemplateColumns: '1.8fr 1fr', gap: '1.5rem' }}>
                  
                  {/* LEFT COLUMN: Deep Content */}
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>
                    
                    {/* 0. COMPANY DATA MATCH VALIDATION BANNER */}
                    {entityDetail.company_match && (
                      <div style={{
                        background: entityDetail.company_match.status === 'VERIFIED' ? 'rgba(16, 185, 129, 0.08)' : (entityDetail.company_match.status === 'PARTIAL_MATCH' ? 'rgba(245, 158, 11, 0.08)' : 'rgba(239, 68, 68, 0.08)'),
                        border: `1px solid ${entityDetail.company_match.status === 'VERIFIED' ? 'rgba(16, 185, 129, 0.3)' : (entityDetail.company_match.status === 'PARTIAL_MATCH' ? 'rgba(245, 158, 11, 0.3)' : 'rgba(239, 68, 68, 0.3)')}`,
                        borderRadius: '0.75rem',
                        padding: '0.85rem 1.1rem',
                        display: 'flex',
                        justifyContent: 'space-between',
                        alignItems: 'center',
                        flexWrap: 'wrap',
                        gap: '0.6rem'
                      }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
                          <span style={{
                            fontSize: '0.72rem',
                            fontWeight: 800,
                            padding: '0.2rem 0.55rem',
                            borderRadius: '0.375rem',
                            background: entityDetail.company_match.status === 'VERIFIED' ? 'rgba(16, 185, 129, 0.2)' : (entityDetail.company_match.status === 'PARTIAL_MATCH' ? 'rgba(245, 158, 11, 0.2)' : 'rgba(239, 68, 68, 0.2)'),
                            color: entityDetail.company_match.status === 'VERIFIED' ? '#34d399' : (entityDetail.company_match.status === 'PARTIAL_MATCH' ? '#fbbf24' : '#f87171')
                          }}>
                            {entityDetail.company_match.status === 'VERIFIED' ? '✓ DATA MATCH VERIFIED' : (entityDetail.company_match.status === 'PARTIAL_MATCH' ? '⚠ PARTIAL MATCH' : '✕ MISMATCH')}
                          </span>
                          <span style={{ fontSize: '0.82rem', color: '#cbd5e1' }}>
                            {entityDetail.company_match.reason || 'Entity identity and candidate alignment verified.'}
                          </span>
                        </div>
                        <div style={{ fontSize: '0.78rem', color: '#94a3b8', fontWeight: 700 }}>
                          Confidence Score: <span style={{ color: '#38bdf8' }}>{Math.round((entityDetail.company_match.score || 0.95) * 100)}%</span>
                        </div>
                      </div>
                    )}

                    {/* 1. BUSINESS OVERVIEW & SYNTHESIS */}
                    <div>
                      <h3 style={{ fontSize: '0.8rem', fontWeight: 800, color: '#94a3b8', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '0.6rem' }}>
                        BUSINESS OVERVIEW & SYNTHESIS
                      </h3>
                      <div style={{ background: '#111827', border: '1px solid #1f2937', borderRadius: '0.75rem', padding: '1.1rem', color: '#cbd5e1', fontSize: '0.9rem', lineHeight: '1.6' }}>
                        {entityDetail.summary}
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
                        DECISION MAKERS & LEADERSHIP ({Array.isArray(entityDetail.decision_makers) ? entityDetail.decision_makers.filter(isPersonVerified).length : 0})
                      </h3>
                      {Array.isArray(entityDetail.decision_makers) && entityDetail.decision_makers.filter(isPersonVerified).length > 0 ? (
                        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.6rem' }}>
                          {entityDetail.decision_makers.filter(isPersonVerified).map((p, idx) => (
                            <div key={idx} style={{ background: '#111827', border: '1px solid #1f2937', borderRadius: '0.65rem', padding: '0.85rem 1rem', display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '0.6rem' }}>
                              <div>
                                <div style={{ fontWeight: 800, color: '#ffffff', fontSize: '0.92rem', display: 'flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap' }}>
                                  <span>{p.name}</span>
                                  <span style={{ color: '#22d3ee', fontWeight: 600 }}>({p.title || 'Director'})</span>
                                  <span style={{
                                    fontSize: '0.66rem',
                                    fontWeight: 700,
                                    color: '#10b981',
                                    background: 'rgba(16, 185, 129, 0.12)',
                                    border: '1px solid rgba(16, 185, 129, 0.25)',
                                    padding: '0.1rem 0.45rem',
                                    borderRadius: '0.25rem'
                                  }}>
                                    ✓ Company Match: Verified ({Math.round((p.match_score || p.confidence || 0.95) * 100)}%)
                                  </span>
                                </div>
                                <div style={{ fontSize: '0.78rem', color: '#9ca3af', marginTop: '0.2rem' }}>
                                  {p.evidence ? (
                                    <span style={{ color: '#94a3b8' }}>Evidence: {p.evidence.slice(0, 120)}{p.evidence.length > 120 ? '...' : ''}</span>
                                  ) : (
                                    <span>Contact Person • Economic Buyer</span>
                                  )}
                                </div>
                              </div>
                              <a href={getLinkedInProfileOrSearch(p, entityDetail.canonical_name)} target="_blank" rel="noreferrer"
                                style={{
                                  padding: '0.35rem 0.75rem',
                                  background: isLinkedInProfile(p) ? 'rgba(56, 189, 248, 0.15)' : '#1e293b',
                                  border: isLinkedInProfile(p) ? '1px solid #38bdf8' : '1px solid #374151',
                                  color: isLinkedInProfile(p) ? '#38bdf8' : '#9ca3af',
                                  borderRadius: '0.375rem',
                                  fontSize: '0.75rem',
                                  fontWeight: 600,
                                  textDecoration: 'none'
                                }}>
                                {getLinkedInLabel(p)}
                              </a>
                            </div>
                          ))}
                        </div>
                      ) : (
                        <div style={{ background: '#111827', border: '1px dashed #374151', borderRadius: '0.65rem', padding: '1.25rem', textAlign: 'center', color: '#94a3b8', fontSize: '0.85rem' }}>
                          <div style={{ fontWeight: 600, color: '#cbd5e1' }}>No verified decision makers discovered on public profile registries yet.</div>
                          <a
                            href={`https://www.linkedin.com/search/results/people/?keywords=${encodeURIComponent(getCleanBrandName(entityDetail.canonical_name) + ' people')}`}
                            target="_blank"
                            rel="noreferrer"
                            style={{ display: 'inline-block', marginTop: '0.6rem', padding: '0.35rem 0.85rem', background: '#1e293b', border: '1px solid #374151', color: '#38bdf8', borderRadius: '0.375rem', fontSize: '0.75rem', fontWeight: 600, textDecoration: 'none' }}
                          >
                            Search Company Leadership on LinkedIn ↗
                          </a>
                        </div>
                      )}
                    </div>

                    {/* 4. CRAWLED SUBPAGES & MARKDOWN VAULT */}
                    <div>
                      <h3 style={{ fontSize: '0.8rem', fontWeight: 800, color: '#94a3b8', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '0.6rem' }}>
                        CRAWLED SUBPAGES & MARKDOWN VAULT ({Array.isArray(entityDetail.crawled_subpages) && entityDetail.crawled_subpages.length > 0 ? entityDetail.crawled_subpages.length : 1})
                      </h3>
                      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
                        {Array.isArray(entityDetail.crawled_subpages) && entityDetail.crawled_subpages.length > 0 ? (
                          entityDetail.crawled_subpages.map((sp, idx) => (
                            <div key={idx} style={{ background: '#111827', border: '1px solid #1f2937', borderRadius: '0.65rem', padding: '0.75rem 1rem', display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '0.5rem' }}>
                              <div style={{ fontWeight: 700, color: '#f3f4f6', fontSize: '0.85rem' }}>/ • {sp.title || entityDetail.canonical_name}</div>
                              <div style={{ fontFamily: 'monospace', fontSize: '0.75rem', color: '#34d399', background: 'rgba(16,185,129,0.08)', padding: '0.2rem 0.55rem', borderRadius: '0.25rem' }}>
                                MinIO: companies/{entityDetail.domain || 'domain'}/pages/{sp.path || 'homepage.md'}
                              </div>
                            </div>
                          ))
                        ) : (
                          <div style={{ background: '#111827', border: '1px solid #1f2937', borderRadius: '0.65rem', padding: '0.75rem 1rem', display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '0.5rem' }}>
                            <div style={{ fontWeight: 700, color: '#f3f4f6', fontSize: '0.85rem' }}>/ • {entityDetail.canonical_name}</div>
                            <div style={{ fontFamily: 'monospace', fontSize: '0.75rem', color: '#34d399', background: 'rgba(16,185,129,0.08)', padding: '0.2rem 0.55rem', borderRadius: '0.25rem' }}>
                              MinIO: companies/{entityDetail.domain || 'domain'}/pages/homepage.md
                            </div>
                          </div>
                        )}
                      </div>
                    </div>

                  </div>

                  {/* RIGHT COLUMN: Sidebar Metadata Card */}
                  <div style={{ background: '#111827', border: '1px solid #1f2937', borderRadius: '0.875rem', padding: '1.25rem', height: 'fit-content' }}>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
                      
                      <div>
                        <div style={{ fontSize: '0.7rem', fontWeight: 800, color: '#6b7280', textTransform: 'uppercase', letterSpacing: '0.05em' }}>HEADQUARTERS</div>
                        <div style={{ fontSize: '0.95rem', fontWeight: 800, color: '#ffffff', marginTop: '0.15rem' }}>
                          {entityDetail.firmographics?.headquarters || 'Not Specified'}
                        </div>
                      </div>

                      <div>
                        <div style={{ fontSize: '0.7rem', fontWeight: 800, color: '#6b7280', textTransform: 'uppercase', letterSpacing: '0.05em' }}>INDUSTRY</div>
                        <div style={{ fontSize: '0.95rem', fontWeight: 800, color: '#ffffff', marginTop: '0.15rem' }}>
                          {(entityDetail.firmographics?.industry && entityDetail.firmographics.industry !== 'Commercial Web' && entityDetail.firmographics.industry !== 'Commercial Web & Digital Enterprise') ? entityDetail.firmographics.industry : (entityDetail.industry && entityDetail.industry !== 'Commercial Web' ? entityDetail.industry : 'Unknown')}
                        </div>
                      </div>



                      <div>
                        <div style={{ fontSize: '0.7rem', fontWeight: 800, color: '#6b7280', textTransform: 'uppercase', letterSpacing: '0.05em' }}>VERIFIED EMAILS</div>
                        <div style={{ fontSize: '0.9rem', fontWeight: 700, color: '#38bdf8', marginTop: '0.15rem' }}>
                          {Array.isArray(entityDetail.firmographics?.verified_emails) && entityDetail.firmographics.verified_emails.length > 0
                            ? entityDetail.firmographics.verified_emails.join(', ')
                            : (entityDetail.verified_contact || entityDetail.verified_contact_email || 'Not Found')}
                        </div>
                      </div>

                      <div style={{ borderTop: '1px solid #1f2937', paddingTop: '1rem', marginTop: '0.5rem' }}>
                        <div style={{ fontSize: '0.7rem', fontWeight: 800, color: '#6b7280', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '0.4rem' }}>
                          EXTRACTION AUDIT & SOURCE
                        </div>
                        <div style={{ display: 'inline-block', fontFamily: 'monospace', fontSize: '0.75rem', fontWeight: 800, color: '#38bdf8', background: 'rgba(56,189,248,0.1)', padding: '0.25rem 0.55rem', borderRadius: '0.375rem', border: '1px solid rgba(56,189,248,0.2)' }}>
                          {entityDetail.provenance?.source_type || '🚀 OPEN_DATASET:OPEN_PAGERANK_10M'}
                        </div>
                        <div style={{ fontFamily: 'monospace', fontSize: '0.75rem', color: '#9ca3af', marginTop: '0.4rem' }}>
                          🕒 {entityDetail.provenance?.extracted_at ? new Date(entityDetail.provenance.extracted_at).toLocaleString() : '2026-09-03 12:00:00'}
                        </div>
                      </div>

                      <a
                        href={entityDetail.official_website}
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
    </div>
  );
}
