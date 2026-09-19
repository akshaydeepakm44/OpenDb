import React, { useState, useCallback, useRef } from 'react';

/**
 * ManualSearch.jsx — Manual Company Search page
 *
 * Self-contained React component. Owns ONLY its own state.
 * Does NOT modify: entitiesList, crawledDocs, agent2Sessions, or any existing App.jsx state.
 *
 * API calls:
 *   POST /api/manual-search/resolve     — Stage 1: resolution
 *   POST /api/manual-search/investigate — Stage 2: cache-check + dispatch
 *   GET  /api/manual-search/status/{id} — Progress polling (proxy over existing VerificationSession)
 *   GET  /api/manual-search/result/{d}  — Final dossier
 */

const API_BASE = (typeof import_meta_env !== 'undefined' ? import_meta_env.VITE_API_BASE : null) || '/api';

// Re-read at call time so Vite can replace it at bundle time
function apiBase() {
  try {
    return import.meta.env.VITE_API_BASE || '/api';
  } catch {
    return '/api';
  }
}

const STATUS_LABELS = {
  EXISTING: '✅ Already in OpenDB',
  UNIQUE: '🔍 Resolved',
  AMBIGUOUS: '⚠️ Multiple Matches',
  NOT_FOUND: '❌ Not Found',
  RESOLUTION_RETRY_PENDING: '⏳ SearXNG Unavailable',
  CACHE_HIT: '✅ Cached',
  ALREADY_RUNNING: '🔄 Running',
  AGENT1_QUEUED: '⚙️ Crawling',
  VERIFICATION_PENDING: '⏸ Worker Offline',
};

const TERMINAL_STATUSES = new Set([
  'VERIFIED', 'POSTGRES_VERIFIED', 'VERIFICATION_FAILED',
  'INSUFFICIENT_EVIDENCE', 'CRAWL_FAILED', 'LINKEDIN_EVIDENCE_INSUFFICIENT', 'PHASE1_BLOCKED',
]);

function StatusBadge({ status, style = {} }) {
  const map = {
    VERIFIED: { bg: '#ecfdf5', color: '#059669', border: '#a7f3d0', label: '✅ VERIFIED' },
    POSTGRES_VERIFIED: { bg: '#ecfdf5', color: '#059669', border: '#a7f3d0', label: '✅ VERIFIED' },
    VERIFICATION_FAILED: { bg: '#fef2f2', color: '#dc2626', border: '#fecaca', label: '❌ FAILED' },
    CRAWL_FAILED: { bg: '#fef2f2', color: '#dc2626', border: '#fecaca', label: '❌ CRAWL FAILED' },
    INSUFFICIENT_EVIDENCE: { bg: '#fffbeb', color: '#d97706', border: '#fde68a', label: '⚠️ INSUFFICIENT' },
    AGENT2_QUEUED: { bg: '#eff6ff', color: '#2563eb', border: '#bfdbfe', label: '🔵 QUEUED' },
    PHASE1_VERIFYING: { bg: '#f0f9ff', color: '#0284c7', border: '#bae6fd', label: '🔵 VERIFYING' },
    PHASE2_SYNTHESIS: { bg: '#f0f9ff', color: '#0284c7', border: '#bae6fd', label: '🔵 SYNTHESIS' },
    LINKEDIN_DISCOVERY: { bg: '#f5f3ff', color: '#7c3aed', border: '#ddd6fe', label: '🟣 LINKEDIN' },
    FINAL_VERIFICATION: { bg: '#f0fdf4', color: '#16a34a', border: '#bbf7d0', label: '🟢 FINALIZING' },
    CACHE_HIT: { bg: '#ecfdf5', color: '#059669', border: '#a7f3d0', label: '✅ CACHED' },
    AGENT1_QUEUED: { bg: '#eff6ff', color: '#2563eb', border: '#bfdbfe', label: '⚙️ CRAWLING' },
    ALREADY_RUNNING: { bg: '#eff6ff', color: '#2563eb', border: '#bfdbfe', label: '🔄 RUNNING' },
    VERIFICATION_PENDING: { bg: '#fffbeb', color: '#d97706', border: '#fde68a', label: '⏸ PENDING' },
  };
  const s = map[status] || { bg: '#f1f5f9', color: '#64748b', border: '#cbd5e1', label: status };
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: '0.25rem', background: s.bg, color: s.color, border: `1px solid ${s.border}`, borderRadius: '9999px', padding: '0.25rem 0.75rem', fontSize: '0.72rem', fontWeight: 800, letterSpacing: '0.05em', ...style }}>
      {s.label}
    </span>
  );
}

function JobCard({ job, isDarkMode, onSelectCandidate, onPollStatus }) {
  const dark = isDarkMode;
  const cardBg = dark ? '#0f172a' : '#ffffff';
  const border = dark ? '#1e293b' : '#e2e8f0';
  const text = dark ? '#e2e8f0' : '#1e293b';
  const muted = dark ? '#64748b' : '#94a3b8';

  const showCandidates = job.stage === 'AMBIGUOUS' && job.candidates && job.candidates.length > 0;
  const showDossier = job.dossier;

  return (
    <div style={{ background: cardBg, borderRadius: '1rem', border: `1px solid ${border}`, padding: '1.5rem', marginBottom: '1.25rem', boxShadow: dark ? '0 4px 20px rgba(0,0,0,0.35)' : '0 1px 4px rgba(0,0,0,0.06)', transition: 'all 0.2s' }}>
      {/* Header row */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: '0.75rem', marginBottom: '1rem' }}>
        <div>
          <div style={{ fontSize: '1.05rem', fontWeight: 800, color: text, marginBottom: '0.15rem' }}>{job.inputName}</div>
          {job.resolvedDomain && <div style={{ fontSize: '0.78rem', color: '#38bdf8', fontWeight: 600 }}>{job.resolvedDomain}</div>}
        </div>
        <StatusBadge status={job.status || job.stage} />
      </div>

      {/* Message */}
      {job.message && <div style={{ fontSize: '0.8rem', color: muted, marginBottom: '0.85rem', lineHeight: 1.5 }}>{job.message}</div>}

      {/* Candidate selection */}
      {showCandidates && (
        <div style={{ marginTop: '0.5rem' }}>
          <div style={{ fontSize: '0.78rem', fontWeight: 700, color: muted, marginBottom: '0.5rem', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Select the correct company:</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
            {job.candidates.map((c, i) => (
              <button
                key={i}
                onClick={() => onSelectCandidate(job.id, c)}
                style={{
                  display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                  padding: '0.65rem 1rem', borderRadius: '0.6rem',
                  border: dark ? '1px solid #1e3a5f' : '1px solid #bfdbfe',
                  background: dark ? '#0a1628' : '#eff6ff', cursor: 'pointer',
                  fontSize: '0.82rem', fontWeight: 600, color: dark ? '#93c5fd' : '#1d4ed8',
                  textAlign: 'left', transition: 'all 0.15s'
                }}
                onMouseEnter={e => { e.currentTarget.style.background = dark ? '#1e3a5f' : '#dbeafe'; }}
                onMouseLeave={e => { e.currentTarget.style.background = dark ? '#0a1628' : '#eff6ff'; }}
              >
                <span>{c.canonical_name} <span style={{ opacity: 0.6 }}>— {c.domain}</span></span>
                <span style={{ fontSize: '0.7rem', background: c.source === 'postgres' ? '#059669' : '#6366f1', color: 'white', borderRadius: '9999px', padding: '0.15rem 0.5rem' }}>{c.source === 'postgres' ? 'IN DB' : 'WEB'}</span>
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Progress steps for in-flight jobs */}
      {job.progress && !showDossier && (
        <div style={{ marginTop: '0.5rem' }}>
          {job.progress.map((step, i) => (
            <div key={i} style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.3rem', fontSize: '0.78rem' }}>
              <span style={{ width: '18px', height: '18px', borderRadius: '50%', display: 'flex', alignItems: 'center', justifyContent: 'center', background: step.done ? '#059669' : step.active ? '#3b82f6' : (dark ? '#1e293b' : '#f1f5f9'), color: step.done ? 'white' : step.active ? 'white' : muted, fontSize: '0.6rem', fontWeight: 800, flexShrink: 0 }}>{step.done ? '✓' : step.active ? '●' : '○'}</span>
              <span style={{ color: step.done ? '#059669' : step.active ? '#3b82f6' : muted, fontWeight: step.active ? 700 : 400 }}>{step.label}</span>
            </div>
          ))}
        </div>
      )}

      {/* Final dossier */}
      {showDossier && <Dossier data={job.dossier} isDarkMode={isDarkMode} />}

      {/* Re-investigate button for completed jobs */}
      {job.stage === 'DONE' && (
        <button
          onClick={() => onPollStatus && onPollStatus(job)}
          style={{ marginTop: '1rem', fontSize: '0.78rem', padding: '0.4rem 1rem', borderRadius: '0.5rem', border: dark ? '1px solid #334155' : '1px solid #cbd5e1', background: 'transparent', color: muted, cursor: 'pointer' }}
        >
          Refresh
        </button>
      )}
    </div>
  );
}

function Dossier({ data, isDarkMode }) {
  const dark = isDarkMode;
  const muted = dark ? '#94a3b8' : '#64748b';
  const text = dark ? '#e2e8f0' : '#1e293b';
  const sectionBg = dark ? '#111827' : '#f8fafc';
  const border = dark ? '#1e293b' : '#e2e8f0';

  const intel = data.intelligence || {};
  const company = data.company || {};
  const people = data.key_people || [];
  const evidence = data.evidence || [];

  const Field = ({ label, value }) => {
    if (!value) return null;
    return (
      <div style={{ marginBottom: '0.5rem' }}>
        <span style={{ fontSize: '0.7rem', fontWeight: 700, color: muted, textTransform: 'uppercase', letterSpacing: '0.05em', display: 'block' }}>{label}</span>
        <span style={{ fontSize: '0.85rem', color: text }}>{value}</span>
      </div>
    );
  };

  return (
    <div style={{ marginTop: '1rem' }}>
      <StatusBadge status={data.status || data.verification_status} style={{ marginBottom: '1rem' }} />

      {/* Company intelligence */}
      <div style={{ background: sectionBg, borderRadius: '0.75rem', border: `1px solid ${border}`, padding: '1rem', marginBottom: '0.75rem' }}>
        <div style={{ fontSize: '0.8rem', fontWeight: 800, color: muted, textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '0.75rem' }}>Company Intelligence</div>
        <Field label="Company Name" value={data.canonical_name || company.canonical_name} />
        <Field label="Domain" value={data.primary_domain || data.domain} />
        <Field label="Description" value={intel.description || company.description || data.description} />
        <Field label="Industry" value={data.industry || company.industry} />
        <Field label="Headquarters" value={intel.headquarters || company.headquarters || data.headquarters} />
        <Field label="Company Size" value={intel.company_size || company.employee_range || data.employee_range} />
        <Field label="Contact Email" value={(data.verified_emails || company.verified_emails || []).join(', ') || intel.contact_email} />
        <Field label="LinkedIn" value={intel.linkedin_page || company.linkedin_url || data.linkedin_url} />
      </div>

      {/* Key People */}
      {people.length > 0 && (
        <div style={{ background: sectionBg, borderRadius: '0.75rem', border: `1px solid ${border}`, padding: '1rem', marginBottom: '0.75rem' }}>
          <div style={{ fontSize: '0.8rem', fontWeight: 800, color: muted, textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '0.75rem' }}>Key People</div>
          {people.slice(0, 8).map((p, i) => (
            <div key={i} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '0.5rem 0', borderBottom: i < people.length - 1 ? `1px solid ${border}` : 'none' }}>
              <div>
                <div style={{ fontSize: '0.85rem', fontWeight: 700, color: text }}>{p.name}</div>
                <div style={{ fontSize: '0.75rem', color: muted }}>{p.title}</div>
              </div>
              {p.linkedin_url && (
                <a href={p.linkedin_url} target="_blank" rel="noopener noreferrer" style={{ fontSize: '0.72rem', color: '#0ea5e9', fontWeight: 600, textDecoration: 'none' }}>LinkedIn ↗</a>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Evidence */}
      {evidence.length > 0 && (
        <details style={{ background: sectionBg, borderRadius: '0.75rem', border: `1px solid ${border}`, padding: '1rem' }}>
          <summary style={{ fontSize: '0.78rem', fontWeight: 700, color: muted, cursor: 'pointer', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Evidence ({evidence.length} fields)</summary>
          <div style={{ marginTop: '0.75rem' }}>
            {evidence.map((e, i) => (
              <div key={i} style={{ display: 'flex', gap: '0.5rem', padding: '0.3rem 0', borderBottom: i < evidence.length - 1 ? `1px solid ${border}` : 'none', fontSize: '0.78rem' }}>
                <span style={{ color: muted, minWidth: '140px', fontWeight: 600 }}>{e.field}</span>
                <span style={{ color: text, flex: 1 }}>{e.value || '—'}</span>
                <StatusBadge status={e.status} style={{ fontSize: '0.6rem', padding: '0.1rem 0.4rem' }} />
              </div>
            ))}
          </div>
        </details>
      )}
    </div>
  );
}

export default function ManualSearch({ isDarkMode }) {
  const [jobs, setJobs] = useState([]);
  const [inputValue, setInputValue] = useState('');
  const [resolving, setResolving] = useState(false);
  const [error, setError] = useState(null);
  const pollRefs = useRef({});

  const dark = isDarkMode;
  const bg = dark ? '#0a0f1e' : '#f8fafc';
  const cardBg = dark ? '#0f172a' : '#ffffff';
  const border = dark ? '#1e293b' : '#e2e8f0';
  const text = dark ? '#e2e8f0' : '#1e293b';
  const muted = dark ? '#64748b' : '#94a3b8';

  function _makeProgress(statuses, current) {
    const steps = [
      { key: 'RESOLVING', label: 'Resolving company identity' },
      { key: 'AGENT2_QUEUED', label: 'Agent 1 crawling website' },
      { key: 'PHASE1_VERIFYING', label: 'Agent 2 — Phase 1 verification' },
      { key: 'PHASE2_SYNTHESIS', label: 'Phase 2 — Business synthesis' },
      { key: 'LINKEDIN_DISCOVERY', label: 'LinkedIn key people discovery' },
      { key: 'FINAL_VERIFICATION', label: 'Final verification' },
      { key: 'VERIFIED', label: 'Verified & persisted to OpenDB' },
    ];
    const idx = steps.findIndex(s => s.key === current);
    return steps.map((s, i) => ({
      ...s,
      done: i < idx,
      active: i === idx,
    }));
  }

  function updateJob(id, patch) {
    setJobs(prev => prev.map(j => j.id === id ? { ...j, ...patch } : j));
  }

  async function startPolling(jobId, sessionId, domain) {
    if (pollRefs.current[jobId]) clearInterval(pollRefs.current[jobId]);
    const base = apiBase();
    const doFetch = async () => {
      try {
        // Poll the new status endpoint (thin proxy over VerificationSession)
        const res = await fetch(`${base}/manual-search/status/${sessionId}`);
        if (!res.ok) return;
        const data = await res.json();
        const progress = _makeProgress([], data.status);
        updateJob(jobId, { status: data.status, progress });
        if (TERMINAL_STATUSES.has(data.status)) {
          clearInterval(pollRefs.current[jobId]);
          delete pollRefs.current[jobId];
          // Fetch full dossier
          const dresRes = await fetch(`${base}/manual-search/result/${domain}`);
          if (dresRes.ok) {
            const dossier = await dresRes.json();
            updateJob(jobId, { stage: 'DONE', dossier, status: data.status });
          } else {
            updateJob(jobId, { stage: 'DONE', dossier: data, status: data.status });
          }
        }
      } catch (e) {
        console.warn('[ManualSearch] Poll error:', e);
      }
    };
    await doFetch();
    pollRefs.current[jobId] = setInterval(doFetch, 3000);
  }

  // When an AGENT1_QUEUED result comes back with no session_id yet,
  // we poll /api/agent2/cards?query=domain to find the new session.
  async function pollForSession(jobId, domain, maxAttempts = 20) {
    const base = apiBase();
    let attempts = 0;
    const interval = setInterval(async () => {
      attempts++;
      if (attempts > maxAttempts) {
        clearInterval(interval);
        updateJob(jobId, { message: 'Session not yet visible — check Agent 2 tab in the dashboard.', stage: 'WAITING' });
        return;
      }
      try {
        const res = await fetch(`${base}/agent2/cards?query=${encodeURIComponent(domain)}&limit=3`);
        if (!res.ok) return;
        const data = await res.json();
        const sessions = data.results || [];
        const match = sessions.find(s => (s.domain || '').replace('www.', '') === domain.replace('www.', ''));
        if (match) {
          clearInterval(interval);
          updateJob(jobId, { sessionId: match.id || match.session_id, message: 'Agent 2 session found — tracking progress.' });
          await startPolling(jobId, match.id || match.session_id, domain);
        }
      } catch (e) {
        console.warn('[ManualSearch] Session poll error:', e);
      }
    }, 4000);
  }

  async function handleResolve() {
    const name = inputValue.trim();
    if (!name) return;
    setResolving(true);
    setError(null);
    const base = apiBase();
    const jobId = `job-${Date.now()}`;
    const newJob = { id: jobId, inputName: name, stage: 'RESOLVING', status: 'RESOLVING', message: 'Resolving company identity...', candidates: [], resolvedDomain: null, progress: null, dossier: null };
    setJobs(prev => [newJob, ...prev]);
    setInputValue('');
    try {
      const res = await fetch(`${base}/manual-search/resolve`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ company_name: name }),
      });
      const data = await res.json();
      if (!res.ok) {
        updateJob(jobId, { stage: 'ERROR', status: 'ERROR', message: data.detail || 'Resolution failed.' });
        return;
      }
      if (data.status === 'AMBIGUOUS') {
        updateJob(jobId, { stage: 'AMBIGUOUS', status: 'AMBIGUOUS', candidates: data.candidates, message: data.message });
      } else if (data.status === 'NOT_FOUND' || data.status === 'RESOLUTION_RETRY_PENDING') {
        updateJob(jobId, { stage: data.status, status: data.status, message: data.message });
      } else {
        // EXISTING or UNIQUE — auto-investigate
        const c = data.candidates[0];
        updateJob(jobId, { stage: 'INVESTIGATING', status: 'INVESTIGATING', resolvedDomain: c.domain, candidates: data.candidates, message: `Resolved to ${c.canonical_name} (${c.domain}). Starting cache check...` });
        await runInvestigate(jobId, name, c.domain);
      }
    } catch (e) {
      updateJob(jobId, { stage: 'ERROR', status: 'ERROR', message: `Network error: ${e.message}` });
    } finally {
      setResolving(false);
    }
  }

  async function handleSelectCandidate(jobId, candidate) {
    const job = jobs.find(j => j.id === jobId);
    if (!job) return;
    updateJob(jobId, { stage: 'INVESTIGATING', status: 'INVESTIGATING', resolvedDomain: candidate.domain, message: `Investigating ${candidate.canonical_name} (${candidate.domain})...` });
    await runInvestigate(jobId, job.inputName, candidate.domain);
  }

  async function runInvestigate(jobId, companyName, domain) {
    const base = apiBase();
    try {
      const res = await fetch(`${base}/manual-search/investigate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ company_name: companyName, domain }),
      });
      const data = await res.json();
      if (!res.ok) {
        updateJob(jobId, { stage: 'ERROR', status: 'ERROR', message: data.detail || 'Investigation failed.' });
        return;
      }
      if (data.status === 'CACHE_HIT') {
        updateJob(jobId, { stage: 'INVESTIGATING', status: 'CACHE_HIT', resolvedDomain: domain, message: data.message });
        // Fetch full dossier from result endpoint
        const dresRes = await fetch(`${base}/manual-search/result/${domain}`);
        const dossier = dresRes.ok ? await dresRes.json() : data;
        updateJob(jobId, { stage: 'DONE', dossier, status: 'CACHE_HIT' });
        return;
      }
      if (data.status === 'ALREADY_RUNNING') {
        updateJob(jobId, { stage: 'TRACKING', status: 'ALREADY_RUNNING', resolvedDomain: domain, message: data.message, sessionId: data.session_id });
        await startPolling(jobId, data.session_id, domain);
        return;
      }
      if (data.status === 'AGENT1_QUEUED') {
        const progress = _makeProgress([], 'AGENT2_QUEUED');
        updateJob(jobId, { stage: 'TRACKING', status: 'AGENT1_QUEUED', resolvedDomain: domain, message: data.message, progress });
        // No session_id yet (Agent 1 hasn't written the Document row yet)
        // Poll /api/agent2/cards?query=domain to find the new session
        await pollForSession(jobId, domain);
        return;
      }
      if (data.status === 'VERIFICATION_PENDING') {
        updateJob(jobId, { stage: 'VERIFICATION_PENDING', status: 'VERIFICATION_PENDING', resolvedDomain: domain, message: data.message });
        return;
      }
      updateJob(jobId, { stage: 'DONE', status: data.status, message: data.message });
    } catch (e) {
      updateJob(jobId, { stage: 'ERROR', status: 'ERROR', message: `Investigation failed: ${e.message}` });
    }
  }

  return (
    <div style={{ padding: '0 0 3rem 0' }}>
      {/* Page header */}
      <div style={{ marginBottom: '2rem' }}>
        <h2 style={{ margin: 0, fontSize: '1.6rem', fontWeight: 900, color: dark ? '#f1f5f9' : '#0f172a', letterSpacing: '-0.02em' }}>
          🔍 Manual Company Search
        </h2>
        <p style={{ margin: '0.35rem 0 0 0', fontSize: '0.85rem', color: muted }}>
          Search any company by name — OpenDB will resolve, verify, and enrich it through the full intelligence pipeline.
        </p>
      </div>

      {/* Search input */}
      <div style={{ display: 'flex', gap: '0.75rem', marginBottom: '2rem', flexWrap: 'wrap' }}>
        <input
          id="manual-search-input"
          type="text"
          value={inputValue}
          onChange={e => setInputValue(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter' && !resolving) handleResolve(); }}
          placeholder="Enter company name (e.g. Stripe, Databricks, OpenAI)..."
          style={{
            flex: 1, minWidth: '260px',
            padding: '0.8rem 1.2rem',
            borderRadius: '0.75rem',
            border: dark ? '1px solid #334155' : '1px solid #cbd5e1',
            background: dark ? '#1e293b' : '#ffffff',
            color: dark ? '#f1f5f9' : '#0f172a',
            fontSize: '0.95rem',
            outline: 'none',
            boxShadow: dark ? 'none' : '0 1px 3px rgba(0,0,0,0.06)',
          }}
          disabled={resolving}
        />
        <button
          id="manual-search-btn"
          onClick={handleResolve}
          disabled={resolving || !inputValue.trim()}
          style={{
            padding: '0.8rem 1.8rem',
            borderRadius: '0.75rem',
            border: 'none',
            background: resolving ? '#94a3b8' : 'linear-gradient(135deg, #6366f1, #4f46e5)',
            color: 'white',
            fontSize: '0.95rem',
            fontWeight: 800,
            cursor: resolving || !inputValue.trim() ? 'not-allowed' : 'pointer',
            boxShadow: resolving ? 'none' : '0 4px 14px rgba(99, 102, 241, 0.4)',
            transition: 'all 0.2s',
          }}
        >
          {resolving ? 'Resolving...' : '🔍 Search'}
        </button>
      </div>

      {error && (
        <div style={{ background: '#fef2f2', border: '1px solid #fecaca', borderRadius: '0.75rem', padding: '0.85rem 1.2rem', marginBottom: '1.5rem', fontSize: '0.85rem', color: '#dc2626' }}>
          {error}
        </div>
      )}

      {/* Job cards */}
      {jobs.length === 0 && (
        <div style={{ textAlign: 'center', padding: '4rem 2rem', color: muted }}>
          <div style={{ fontSize: '3rem', marginBottom: '0.75rem' }}>🏢</div>
          <div style={{ fontSize: '1rem', fontWeight: 600, marginBottom: '0.4rem' }}>No searches yet</div>
          <div style={{ fontSize: '0.82rem' }}>Type a company name above and press Search to begin.</div>
        </div>
      )}

      {jobs.map(job => (
        <JobCard
          key={job.id}
          job={job}
          isDarkMode={isDarkMode}
          onSelectCandidate={handleSelectCandidate}
          onPollStatus={null}
        />
      ))}

      {/* Info footer */}
      {jobs.length > 0 && (
        <div style={{ marginTop: '1.5rem', padding: '1rem 1.25rem', borderRadius: '0.75rem', border: dark ? '1px solid #1e293b' : '1px solid #e2e8f0', background: dark ? '#0f172a' : '#f8fafc', fontSize: '0.78rem', color: muted, lineHeight: 1.6 }}>
          <strong style={{ color: dark ? '#94a3b8' : '#475569' }}>How it works:</strong>{' '}
          Manual Search resolves company identity using existing OpenDB data, then invokes the same Agent 1 → Agent 2 verification pipeline as autonomous discovery.
          Results are persisted to PostgreSQL and will appear in the main dashboard under "Verified Leads".
        </div>
      )}
    </div>
  );
}
