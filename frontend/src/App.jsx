import React, { useState, useEffect } from 'react';
import './index.css';

const API_BASE = 'http://localhost:8000/api';

const PIPELINE_STAGES = [
  { id: 'USER_INPUT', label: 'User Input' },
  { id: 'URL_DISCOVERY', label: 'URL Discovery' },
  { id: 'CRAWLING', label: 'Crawling' },
  { id: 'RAW_CONTENT', label: 'Raw Content' },
  { id: 'CONTENT_EXTRACTION', label: 'Content Extraction' },
  { id: 'DOMAIN_CLASSIFICATION', label: 'Domain Detection' },
  { id: 'SCHEMA_MAPPING', label: 'Schema Mapping' },
  { id: 'NORMALIZATION', label: 'Normalization' },
  { id: 'VALIDATION', label: 'Validation' },
  { id: 'POSTGRESQL', label: 'PostgreSQL' }
];

export default function App() {
  const [url, setUrl] = useState('');
  const [query, setQuery] = useState("Find the company's products, services, leadership, locations and contact information.");
  const [domain, setDomain] = useState('Technology');
  const [maxPages, setMaxPages] = useState(10);
  const [maxDepth, setMaxDepth] = useState(2);

  const [jobId, setJobId] = useState(null);
  const [jobStatus, setJobStatus] = useState(null);
  const [pages, setPages] = useState([]);
  const [results, setResults] = useState([]);

  const [activeTab, setActiveTab] = useState('structured'); // 'pages' | 'structured' | 'evidence' | 'resources' | 'raw'
  const [rawTab, setRawTab] = useState('html'); // 'html' | 'markdown' | 'text' | 'json'
  const [selectedDocId, setSelectedDocId] = useState(null);
  const [rawContent, setRawContent] = useState({ html: '', markdown: '', text: '' });
  const [error, setError] = useState(null);

  // Poll job status
  useEffect(() => {
    if (!jobId) return;

    const fetchJobData = async () => {
      try {
        const res = await fetch(`${API_BASE}/crawl/${jobId}`);
        if (!res.ok) return;
        const data = await res.json();
        setJobStatus(data);

        // Fetch pages and results
        const pagesRes = await fetch(`${API_BASE}/crawl/${jobId}/pages`);
        if (pagesRes.ok) {
          const pData = await pagesRes.json();
          setPages(pData);
          setSelectedDocId(prev => (prev ? prev : (pData.length > 0 ? pData[0].document_id : null)));
        }

        const resultsRes = await fetch(`${API_BASE}/crawl/${jobId}/results`);
        if (resultsRes.ok) {
          const rData = await resultsRes.json();
          setResults(rData);
        }

        if (data.status === 'completed' || data.status === 'failed') {
          clearInterval(interval);
        }
      } catch (err) {
        console.error("Polling error:", err);
      }
    };

    fetchJobData();
    const interval = setInterval(fetchJobData, 1500);

    return () => clearInterval(interval);
  }, [jobId]);

  // Fetch raw content when selected document changes
  useEffect(() => {
    if (!selectedDocId) return;
    fetch(`${API_BASE}/documents/${selectedDocId}/raw`)
      .then(res => res.json())
      .then(data => {
        setRawContent({
          html: data.html || 'No raw HTML content available',
          markdown: data.markdown || 'No Markdown content available',
          text: data.text || 'No plain text available'
        });
      })
      .catch(err => console.error("Error loading raw doc content:", err));
  }, [selectedDocId]);

  const handleStartCrawl = async (e) => {
    e.preventDefault();
    setError(null);
    setJobId(null);
    setJobStatus(null);
    setPages([]);
    setResults([]);
    setMaxStageIndex(0);

    try {
      const res = await fetch(`${API_BASE}/crawl`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          url,
          query,
          domain,
          max_pages: Number(maxPages),
          max_depth: Number(maxDepth)
        })
      });

      if (!res.ok) {
        const errData = await res.json();
        throw new Error(errData.detail || "Failed to initiate crawl job");
      }

      const data = await res.json();
      setJobId(data.job_id);
      setJobStatus({ status: 'pending', pipeline_stage: 'URL_DISCOVERY' });
    } catch (err) {
      setError(err.message);
    }
  };

  const [maxStageIndex, setMaxStageIndex] = useState(0);

  const getStageIndex = (stageName) => {
    if (!stageName) return 0;
    if (stageName === 'PERSISTENCE_COMPLETE' || stageName === 'completed' || stageName === 'POSTGRESQL') return PIPELINE_STAGES.length - 1;
    if (stageName === 'EXTRACTION_AND_VALIDATION') return 7;
    if (stageName === 'RAW_CONTENT_STORAGE') return 3;
    const idx = PIPELINE_STAGES.findIndex(s => s.id === stageName);
    return idx >= 0 ? idx : 1;
  };

  useEffect(() => {
    const calculatedIdx = getStageIndex(jobStatus?.pipeline_stage);
    if (calculatedIdx > maxStageIndex) {
      setMaxStageIndex(calculatedIdx);
    }
  }, [jobStatus]);

  const currentStageIndex = Math.max(maxStageIndex, getStageIndex(jobStatus?.pipeline_stage));
  const activeResult = results.find(r => r.document_id === selectedDocId) || results[0];

  return (
    <div className="app-container">
      <header>
        <h1>OPENDB CRAWLER LAB</h1>
        <p className="subtitle">Universal Domain-Aware Web Discovery & Extraction Engine</p>
      </header>

      {/* Crawl Request Form */}
      <div className="card" style={{ marginBottom: '2rem' }}>
        <h2 className="card-title">Crawl Configuration</h2>
        <form onSubmit={handleStartCrawl} style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1.5rem' }}>
          <div style={{ gridColumn: 'span 2' }}>
            <label className="data-label">Starting URL (Optional: Leave blank to crawl open web)</label>
            <input
              type="url"
              className="search-input"
              style={{ maxWidth: '100%' }}
              placeholder="e.g. https://www.example.com or leave blank"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
            />
          </div>

          <div>
            <label className="data-label">Query / Information Requirement (Optional)</label>
            <input
              type="text"
              className="search-input"
              style={{ maxWidth: '100%' }}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </div>

          <div>
            <label className="data-label">Target Domain Schema</label>
            <select
              className="search-input"
              style={{ maxWidth: '100%', borderRadius: '0.5rem' }}
              value={domain}
              onChange={(e) => setDomain(e.target.value)}
            >
              <option value="Technology">Technology</option>
              <option value="Healthcare">Healthcare</option>
              <option value="Education">Education</option>
              <option value="Business">Business</option>
            </select>
          </div>

          <div style={{ gridColumn: 'span 2', textAlign: 'right' }}>
            <button
              type="submit"
              className="search-btn"
              disabled={jobStatus?.status === 'running'}
            >
              {jobStatus?.status === 'running' ? 'Autonomous Crawling in Progress...' : 'START AUTONOMOUS CRAWL'}
            </button>
          </div>
        </form>
      </div>

      {error && <div className="error-message">Error: {error}</div>}

      {/* PIPELINE VISUALIZATION STAGES (Section 36 Requirement) */}
      {jobStatus && (
        <div className="card" style={{ marginBottom: '2rem' }}>
          <h2 className="card-title">Extraction Pipeline Stages</h2>
          <div style={{ display: 'flex', overflowX: 'auto', gap: '0.5rem', paddingBottom: '1rem' }}>
            {PIPELINE_STAGES.map((st, idx) => {
              const isPassed = idx <= currentStageIndex;
              const isCurrent = idx === currentStageIndex && jobStatus.status === 'running';

              return (
                <div
                  key={st.id}
                  style={{
                    flex: 1,
                    minWidth: '110px',
                    padding: '0.75rem',
                    borderRadius: '0.5rem',
                    textAlign: 'center',
                    background: isCurrent ? 'rgba(59, 130, 246, 0.2)' : isPassed ? 'rgba(16, 185, 129, 0.15)' : '#0f172a',
                    border: `1px solid ${isCurrent ? '#3b82f6' : isPassed ? '#10b981' : '#334155'}`,
                    transition: 'all 0.3s ease'
                  }}
                >
                  <div style={{ fontSize: '0.75rem', color: isCurrent ? '#60a5fa' : isPassed ? '#34d399' : '#94a3b8', fontWeight: 700 }}>
                    STAGE {idx + 1}
                  </div>
                  <div style={{ fontSize: '0.85rem', fontWeight: 600, marginTop: '0.25rem' }}>
                    {st.label}
                  </div>
                  <div style={{ fontSize: '0.7rem', marginTop: '0.25rem', color: isCurrent ? '#93c5fd' : '#64748b' }}>
                    {isCurrent ? 'PROCESSING' : isPassed ? 'PASSED' : 'WAITING'}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* CRAWL JOB STATUS SUMMARY CARD */}
      {jobStatus && (
        <div className="card" style={{ marginBottom: '2rem' }}>
          <h2 className="card-title">Crawl Job Overview</h2>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: '1rem', textAlign: 'center' }}>
            <div style={{ padding: '1rem', background: '#0f172a', borderRadius: '0.5rem' }}>
              <span className="data-label">Status</span>
              <span className="source-badge" style={{ marginTop: '0.5rem' }}>{jobStatus.status}</span>
            </div>
            <div style={{ padding: '1rem', background: '#0f172a', borderRadius: '0.5rem' }}>
              <span className="data-label">Discovered Pages</span>
              <div style={{ fontSize: '1.8rem', fontWeight: 700, color: '#60a5fa' }}>{jobStatus.pages_discovered || 0}</div>
            </div>
            <div style={{ padding: '1rem', background: '#0f172a', borderRadius: '0.5rem' }}>
              <span className="data-label">Crawled Pages</span>
              <div style={{ fontSize: '1.8rem', fontWeight: 700, color: '#34d399' }}>{jobStatus.pages_crawled || 0}</div>
            </div>
            <div style={{ padding: '1rem', background: '#0f172a', borderRadius: '0.5rem' }}>
              <span className="data-label">Documents</span>
              <div style={{ fontSize: '1.8rem', fontWeight: 700, color: '#a78bfa' }}>{jobStatus.documents_count || 0}</div>
            </div>
            <div style={{ padding: '1rem', background: '#0f172a', borderRadius: '0.5rem' }}>
              <span className="data-label">Resources</span>
              <div style={{ fontSize: '1.8rem', fontWeight: 700, color: '#f59e0b' }}>{jobStatus.resources_count || 0}</div>
            </div>
            <div style={{ padding: '1rem', background: '#0f172a', borderRadius: '0.5rem' }}>
              <span className="data-label">Successful</span>
              <div style={{ fontSize: '1.8rem', fontWeight: 700, color: '#10b981' }}>{jobStatus.successful_count || 0}</div>
            </div>
            <div style={{ padding: '1rem', background: '#0f172a', borderRadius: '0.5rem' }}>
              <span className="data-label">Failed</span>
              <div style={{ fontSize: '1.8rem', fontWeight: 700, color: '#ef4444' }}>{jobStatus.failed_count || 0}</div>
            </div>
          </div>
        </div>
      )}

      {/* RESULT TABS NAVIGATION */}
      {jobStatus && (
        <>
          <div style={{ display: 'flex', gap: '0.5rem', marginBottom: '1.5rem', borderBottom: '1px solid #334155', paddingBottom: '0.5rem' }}>
            <button
              onClick={() => setActiveTab('structured')}
              style={{
                padding: '0.75rem 1.5rem',
                borderRadius: '0.5rem',
                border: 'none',
                background: activeTab === 'structured' ? '#3b82f6' : '#1e293b',
                color: 'white',
                fontWeight: 600,
                cursor: 'pointer'
              }}
            >
              Extracted Structured Data
            </button>
            <button
              onClick={() => setActiveTab('pages')}
              style={{
                padding: '0.75rem 1.5rem',
                borderRadius: '0.5rem',
                border: 'none',
                background: activeTab === 'pages' ? '#3b82f6' : '#1e293b',
                color: 'white',
                fontWeight: 600,
                cursor: 'pointer'
              }}
            >
              Discovered Pages ({pages.length})
            </button>
            <button
              onClick={() => setActiveTab('evidence')}
              style={{
                padding: '0.75rem 1.5rem',
                borderRadius: '0.5rem',
                border: 'none',
                background: activeTab === 'evidence' ? '#3b82f6' : '#1e293b',
                color: 'white',
                fontWeight: 600,
                cursor: 'pointer'
              }}
            >
              Evidence & Provenance
            </button>
            <button
              onClick={() => setActiveTab('resources')}
              style={{
                padding: '0.75rem 1.5rem',
                borderRadius: '0.5rem',
                border: 'none',
                background: activeTab === 'resources' ? '#3b82f6' : '#1e293b',
                color: 'white',
                fontWeight: 600,
                cursor: 'pointer'
              }}
            >
              Raw Resources
            </button>
            <button
              onClick={() => setActiveTab('raw')}
              style={{
                padding: '0.75rem 1.5rem',
                borderRadius: '0.5rem',
                border: 'none',
                background: activeTab === 'raw' ? '#3b82f6' : '#1e293b',
                color: 'white',
                fontWeight: 600,
                cursor: 'pointer'
              }}
            >
              Raw Page Content
            </button>
          </div>

          {/* TAB 1: DISCOVERED PAGES TABLE */}
          {activeTab === 'pages' && (
            <div className="card">
              <h2 className="card-title">Discovered Pages Table</h2>
              <div style={{ overflowX: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', textAlign: 'left' }}>
                  <thead>
                    <tr style={{ borderBottom: '1px solid #334155', color: '#94a3b8' }}>
                      <th style={{ padding: '0.75rem' }}>URL</th>
                      <th style={{ padding: '0.75rem' }}>Title</th>
                      <th style={{ padding: '0.75rem' }}>Status</th>
                      <th style={{ padding: '0.75rem' }}>Content Type</th>
                      <th style={{ padding: '0.75rem' }}>Words</th>
                      <th style={{ padding: '0.75rem' }}>Domain</th>
                      <th style={{ padding: '0.75rem' }}>Confidence</th>
                    </tr>
                  </thead>
                  <tbody>
                    {pages.map((p) => (
                      <tr
                        key={p.document_id}
                        onClick={() => setSelectedDocId(p.document_id)}
                        style={{
                          borderBottom: '1px solid #334155',
                          cursor: 'pointer',
                          background: selectedDocId === p.document_id ? 'rgba(59, 130, 246, 0.1)' : 'transparent'
                        }}
                      >
                        <td style={{ padding: '0.75rem', color: '#60a5fa', maxWidth: '250px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          <a href={p.url} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}>{p.url}</a>
                        </td>
                        <td style={{ padding: '0.75rem' }}>{p.title || 'Untitled'}</td>
                        <td style={{ padding: '0.75rem' }}><span style={{ color: p.status === 200 ? '#10b981' : '#ef4444' }}>{p.status}</span></td>
                        <td style={{ padding: '0.75rem' }}>{p.content_type}</td>
                        <td style={{ padding: '0.75rem' }}>{p.word_count}</td>
                        <td style={{ padding: '0.75rem' }}>{p.domain}</td>
                        <td style={{ padding: '0.75rem' }}>{p.confidence ? (p.confidence * 100).toFixed(0) + '%' : 'N/A'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* TAB 2: EXTRACTED STRUCTURED DATA */}
          {activeTab === 'structured' && activeResult && (
            <div className="results-grid">
              {/* Universal Data Record */}
              <div className="card">
                <h3 className="card-title">UNIVERSAL DATA RECORD</h3>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem' }}>
                  <div className="data-row">
                    <span className="data-label">Canonical Name</span>
                    <div className="data-value">{activeResult.universal.canonical_name || 'N/A'}</div>
                  </div>
                  <div className="data-row">
                    <span className="data-label">Entity Type</span>
                    <div className="data-value">{activeResult.universal.entity_type || 'N/A'}</div>
                  </div>
                  <div className="data-row" style={{ gridColumn: 'span 2' }}>
                    <span className="data-label">Description</span>
                    <div className="data-value">{activeResult.universal.description || 'N/A'}</div>
                  </div>
                  <div className="data-row">
                    <span className="data-label">Domain / Subdomain</span>
                    <div className="data-value">{activeResult.universal.domain} / {activeResult.universal.subdomain}</div>
                  </div>
                  <div className="data-row">
                    <span className="data-label">Country / Language</span>
                    <div className="data-value">{activeResult.universal.country || 'N/A'} ({activeResult.universal.language || 'en'})</div>
                  </div>
                  <div className="data-row">
                    <span className="data-label">Confidence Score</span>
                    <div className="data-value" style={{ color: '#10b981', fontWeight: 700 }}>
                      {(activeResult.universal.confidence * 100).toFixed(1)}%
                    </div>
                  </div>
                </div>
              </div>

              {/* Domain Specific Data */}
              <div className="card">
                <h3 className="card-title">{activeResult.classification.domain.toUpperCase()} DOMAIN DATA</h3>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem' }}>
                  {Object.entries(activeResult.domain_data).map(([k, v]) => {
                    const isMissing = v === null || (Array.isArray(v) && v.length === 0);
                    const isArray = Array.isArray(v);

                    return (
                      <div key={k} className="data-row" style={{ gridColumn: isArray ? 'span 2' : 'span 1' }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                          <span className="data-label">{k.replace(/_/g, ' ').toUpperCase()}</span>
                          <span
                            style={{
                              fontSize: '0.65rem',
                              padding: '0.15rem 0.5rem',
                              borderRadius: '0.25rem',
                              fontWeight: 700,
                              background: isMissing ? 'rgba(239, 68, 68, 0.15)' : 'rgba(16, 185, 129, 0.15)',
                              color: isMissing ? '#f87171' : '#34d399',
                              border: `1px solid ${isMissing ? '#ef4444' : '#10b981'}`
                            }}
                          >
                            {isMissing ? 'MISSING' : 'FOUND'}
                          </span>
                        </div>
                        <div className="data-value">
                          {isMissing ? (
                            <span style={{ color: '#64748b', italic: 'true' }}>null</span>
                          ) : isArray ? (
                            <div className="tag-list">
                              {v.map((item, i) => (
                                <span key={i} className="tag">{item}</span>
                              ))}
                            </div>
                          ) : (
                            String(v)
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            </div>
          )}

          {/* TAB 3: EVIDENCE & PROVENANCE */}
          {activeTab === 'evidence' && activeResult && (
            <div className="card">
              <h2 className="card-title">Fact Evidence & Source Provenance</h2>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
                {activeResult.evidence.length === 0 ? (
                  <p style={{ color: '#94a3b8' }}>No specific evidence snippets recorded for this page.</p>
                ) : (
                  activeResult.evidence.map((ev, idx) => (
                    <div
                      key={idx}
                      style={{
                        background: '#0f172a',
                        padding: '1rem',
                        borderRadius: '0.5rem',
                        borderLeft: '4px solid #3b82f6'
                      }}
                    >
                      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '0.5rem' }}>
                        <span style={{ fontWeight: 700, color: '#60a5fa' }}>Field: {ev.field}</span>
                        <span style={{ fontSize: '0.8rem', color: '#10b981' }}>Confidence: {(ev.confidence * 100).toFixed(0)}%</span>
                      </div>
                      <div style={{ marginBottom: '0.5rem' }}>
                        <strong>Extracted Value:</strong> <span className="tag">{ev.value}</span>
                      </div>
                      <div style={{ marginBottom: '0.5rem', fontSize: '0.9rem', color: '#cbd5e1' }}>
                        <strong>Evidence Snippet:</strong> "{ev.evidence_text}"
                      </div>
                      <div style={{ fontSize: '0.8rem', color: '#94a3b8' }}>
                        <strong>Source URL:</strong> <a href={ev.source_url} target="_blank" rel="noreferrer" style={{ color: '#60a5fa' }}>{ev.source_url}</a>
                      </div>
                    </div>
                  ))
                )}
              </div>
            </div>
          )}

          {/* TAB 4: RAW RESOURCES */}
          {activeTab === 'resources' && activeResult && (
            <div className="card">
              <h2 className="card-title">Discovered Linked Resources</h2>
              <div style={{ overflowX: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', textAlign: 'left' }}>
                  <thead>
                    <tr style={{ borderBottom: '1px solid #334155', color: '#94a3b8' }}>
                      <th style={{ padding: '0.75rem' }}>Resource URL</th>
                      <th style={{ padding: '0.75rem' }}>Type</th>
                      <th style={{ padding: '0.75rem' }}>MIME Type</th>
                      <th style={{ padding: '0.75rem' }}>Size (bytes)</th>
                      <th style={{ padding: '0.75rem' }}>Downloaded</th>
                      <th style={{ padding: '0.75rem' }}>Stored Disk Path</th>
                    </tr>
                  </thead>
                  <tbody>
                    {activeResult.resources.length === 0 ? (
                      <tr><td colSpan="6" style={{ padding: '1rem', color: '#94a3b8' }}>No external document or media resources discovered on this page.</td></tr>
                    ) : (
                      activeResult.resources.map((res) => (
                        <tr key={res.id} style={{ borderBottom: '1px solid #334155' }}>
                          <td style={{ padding: '0.75rem', color: '#60a5fa', maxWidth: '250px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                            <a href={res.url} target="_blank" rel="noreferrer">{res.url}</a>
                          </td>
                          <td style={{ padding: '0.75rem' }}><span className="tag">{res.type}</span></td>
                          <td style={{ padding: '0.75rem' }}>{res.mime_type || 'N/A'}</td>
                          <td style={{ padding: '0.75rem' }}>{res.size || 'N/A'}</td>
                          <td style={{ padding: '0.75rem' }}>{res.downloaded ? <span style={{ color: '#10b981' }}>Yes</span> : <span style={{ color: '#94a3b8' }}>No</span>}</td>
                          <td style={{ padding: '0.75rem', fontFamily: 'monospace', fontSize: '0.8rem' }}>{res.stored_path || 'N/A'}</td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* TAB 5: RAW PAGE CONTENT */}
          {activeTab === 'raw' && (
            <div className="card">
              <h2 className="card-title">Raw Content & Extractions</h2>
              <div style={{ display: 'flex', gap: '0.5rem', marginBottom: '1rem' }}>
                {['html', 'markdown', 'text', 'json'].map(sub => (
                  <button
                    key={sub}
                    onClick={() => setRawTab(sub)}
                    style={{
                      padding: '0.5rem 1rem',
                      borderRadius: '0.25rem',
                      border: 'none',
                      background: rawTab === sub ? '#3b82f6' : '#0f172a',
                      color: 'white',
                      cursor: 'pointer',
                      fontSize: '0.85rem'
                    }}
                  >
                    {sub.toUpperCase()}
                  </button>
                ))}
              </div>

              <pre className="raw-json" style={{ maxHeight: '500px', overflowY: 'auto' }}>
                {rawTab === 'html' && rawContent.html}
                {rawTab === 'markdown' && rawContent.markdown}
                {rawTab === 'text' && rawContent.text}
                {rawTab === 'json' && JSON.stringify(activeResult || {}, null, 2)}
              </pre>
            </div>
          )}
        </>
      )}
    </div>
  );
}
