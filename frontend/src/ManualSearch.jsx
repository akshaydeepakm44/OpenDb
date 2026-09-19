import React, { useState, useCallback, useRef } from 'react';

/**
 * ManualSearch.jsx — Manual Company Search page
 *
 * Self-contained React component. Owns ONLY its own state.
 * Does NOT modify: entitiesList, crawledDocs, agent2Sessions, or any existing App.jsx state.
 *
 * API calls:
 *   POST /api/manual-search/resolve         — Stage 1: resolution
 *   POST /api/manual-search/investigate     — Stage 2: cache-check + dispatch
 *   GET  /api/manual-search/track/{domain}  — Unified real-time tracking (Agent 1 + Agent 2)
 *   GET  /api/manual-search/status/{id}     — Progress polling (proxy over existing VerificationSession)
 *   GET  /api/manual-search/result/{domain} — Final dossier
 */

function apiBase() {
  try {
    return import.meta.env.VITE_API_BASE || '/api';
  } catch {
    return '/api';
  }
}

const ANIMATION_STYLES = `
@keyframes spin {
  0% { transform: rotate(0deg); }
  100% { transform: rotate(360deg); }
}
@keyframes pulseGlow {
  0%, 100% { opacity: 1; transform: scale(1); }
  50% { opacity: 0.35; transform: scale(1.35); }
}
@keyframes shimmerWave {
  0% { background-position: -200% 0; }
  100% { background-position: 200% 0; }
}
.manual-search-spinner {
  animation: spin 0.85s linear infinite !important;
}
.manual-search-pulse {
  animation: pulseGlow 1.4s ease-in-out infinite !important;
}
`;

const TERMINAL_STATUSES = new Set([
  'VERIFIED', 'POSTGRES_VERIFIED', 'VERIFICATION_FAILED',
  'INSUFFICIENT_EVIDENCE', 'CRAWL_FAILED', 'LINKEDIN_EVIDENCE_INSUFFICIENT', 'PHASE1_BLOCKED',
]);

function StatusBadge({ status, style = {} }) {
  const map = {
    VERIFIED: { bg: '#ecfdf5', color: '#059669', border: '#a7f3d0', label: '✅ VERIFIED', inProgress: false },
    POSTGRES_VERIFIED: { bg: '#ecfdf5', color: '#059669', border: '#a7f3d0', label: '✅ VERIFIED', inProgress: false },
    VERIFICATION_FAILED: { bg: '#fef2f2', color: '#dc2626', border: '#fecaca', label: '❌ FAILED', inProgress: false },
    CRAWL_FAILED: { bg: '#fef2f2', color: '#dc2626', border: '#fecaca', label: '❌ CRAWL FAILED', inProgress: false },
    INSUFFICIENT_EVIDENCE: { bg: '#fffbeb', color: '#d97706', border: '#fde68a', label: '⚠️ INSUFFICIENT', inProgress: false },
    AGENT2_QUEUED: { bg: '#eff6ff', color: '#2563eb', border: '#bfdbfe', label: 'QUEUED FOR AGENT 2', inProgress: true },
    PHASE1_VERIFYING: { bg: '#f0f9ff', color: '#0284c7', border: '#bae6fd', label: 'PHASE 1 VERIFYING', inProgress: true },
    PHASE2_SYNTHESIS: { bg: '#f0f9ff', color: '#0284c7', border: '#bae6fd', label: 'PHASE 2 SYNTHESIS', inProgress: true },
    LINKEDIN_DISCOVERY: { bg: '#f5f3ff', color: '#7c3aed', border: '#ddd6fe', label: 'LINKEDIN DISCOVERY', inProgress: true },
    FINAL_VERIFICATION: { bg: '#f0fdf4', color: '#16a34a', border: '#bbf7d0', label: 'FINALIZING', inProgress: true },
    CACHE_HIT: { bg: '#ecfdf5', color: '#059669', border: '#a7f3d0', label: '✅ CACHED', inProgress: false },
    AGENT1_QUEUED: { bg: '#eff6ff', color: '#2563eb', border: '#bfdbfe', label: 'CRAWLING', inProgress: true },
    ALREADY_RUNNING: { bg: '#eff6ff', color: '#2563eb', border: '#bfdbfe', label: 'RUNNING', inProgress: true },
    RESOLVING: { bg: '#eff6ff', color: '#6366f1', border: '#c7d2fe', label: 'RESOLVING', inProgress: true },
    INVESTIGATING: { bg: '#eff6ff', color: '#2563eb', border: '#bfdbfe', label: 'INVESTIGATING', inProgress: true },
    VERIFICATION_PENDING: { bg: '#fffbeb', color: '#d97706', border: '#fde68a', label: '⏸ PENDING', inProgress: false },
  };
  const s = map[status] || { bg: '#f1f5f9', color: '#64748b', border: '#cbd5e1', label: status, inProgress: false };
  return (
    <span style={{
      display: 'inline-flex',
      alignItems: 'center',
      gap: '0.45rem',
      background: s.bg,
      color: s.color,
      border: `1px solid ${s.border}`,
      borderRadius: '9999px',
      padding: '0.25rem 0.85rem',
      fontSize: '0.72rem',
      fontWeight: 800,
      letterSpacing: '0.05em',
      ...style
    }}>
      {s.inProgress && (
        <span
          className="manual-search-pulse"
          style={{
            width: '8px',
            height: '8px',
            borderRadius: '50%',
            background: s.color,
            boxShadow: `0 0 8px ${s.color}`,
            display: 'inline-block'
          }}
        />
      )}
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
  const isInFlight = !showDossier && job.stage !== 'ERROR' && job.stage !== 'NOT_FOUND' && job.stage !== 'AMBIGUOUS';

  return (
    <div style={{
      background: cardBg,
      borderRadius: '1rem',
      border: `1px solid ${border}`,
      padding: '1.5rem',
      marginBottom: '1.25rem',
      boxShadow: dark ? '0 4px 20px rgba(0,0,0,0.35)' : '0 1px 4px rgba(0,0,0,0.06)',
      transition: 'all 0.2s'
    }}>
      {/* Header row */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: '0.75rem', marginBottom: '1rem' }}>
        <div>
          <div style={{ fontSize: '1.05rem', fontWeight: 800, color: text, marginBottom: '0.15rem' }}>{job.inputName}</div>
          {job.resolvedDomain && <div style={{ fontSize: '0.78rem', color: '#38bdf8', fontWeight: 600 }}>{job.resolvedDomain}</div>}
        </div>
        <StatusBadge status={job.status || job.stage} />
      </div>

      {/* Message with active spinner icon if in flight */}
      {job.message && (
        <div style={{
          display: 'flex',
          alignItems: 'center',
          gap: '0.5rem',
          fontSize: '0.82rem',
          color: muted,
          marginBottom: '0.85rem',
          lineHeight: 1.5
        }}>
          {isInFlight && (
            <span
              className="manual-search-spinner"
              style={{
                width: 13,
                height: 13,
                border: '2px solid #38bdf8',
                borderTopColor: 'transparent',
                borderRadius: '50%',
                display: 'inline-block',
                flexShrink: 0
              }}
            />
          )}
          <span>{job.message}</span>
        </div>
      )}

      {/* Real Backend Latency Telemetry */}
      {job.telemetry && (
        <div style={{
          marginBottom: '0.9rem',
          padding: '0.65rem 0.95rem',
          borderRadius: '0.6rem',
          background: dark ? '#0d1527' : '#f8fafc',
          border: `1px solid ${dark ? '#1e3a5f' : '#e2e8f0'}`,
          display: 'flex',
          flexWrap: 'wrap',
          gap: '1.25rem',
          alignItems: 'center',
          fontSize: '0.75rem'
        }}>
          <div>
            <span style={{ color: muted, display: 'block', fontSize: '0.63rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.04em' }}>Priority</span>
            <span style={{ color: '#10b981', fontWeight: 800 }}>{job.telemetry.priority || 'HIGH (9)'}</span>
          </div>
          {job.telemetry.resolution_ms !== null && job.telemetry.resolution_ms !== undefined && (
            <div>
              <span style={{ color: muted, display: 'block', fontSize: '0.63rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.04em' }}>Resolution</span>
              <span style={{ color: text, fontWeight: 700 }}>{(job.telemetry.resolution_ms / 1000).toFixed(2)}s</span>
            </div>
          )}
          {job.telemetry.crawl_queue_wait_ms !== null && job.telemetry.crawl_queue_wait_ms !== undefined && (
            <div>
              <span style={{ color: muted, display: 'block', fontSize: '0.63rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.04em' }}>Crawl Queue</span>
              <span style={{ color: text, fontWeight: 700 }}>{(job.telemetry.crawl_queue_wait_ms / 1000).toFixed(2)}s</span>
            </div>
          )}
          {job.telemetry.crawl_execution_ms !== null && job.telemetry.crawl_execution_ms !== undefined && (
            <div>
              <span style={{ color: muted, display: 'block', fontSize: '0.63rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.04em' }}>Crawl</span>
              <span style={{ color: text, fontWeight: 700 }}>{(job.telemetry.crawl_execution_ms / 1000).toFixed(1)}s</span>
            </div>
          )}
          {job.telemetry.verification_queue_wait_ms !== null && job.telemetry.verification_queue_wait_ms !== undefined && (
            <div>
              <span style={{ color: muted, display: 'block', fontSize: '0.63rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.04em' }}>Verif Queue</span>
              <span style={{ color: text, fontWeight: 700 }}>{(job.telemetry.verification_queue_wait_ms / 1000).toFixed(2)}s</span>
            </div>
          )}
          <div>
            <span style={{ color: muted, display: 'block', fontSize: '0.63rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.04em' }}>Verification</span>
            <span style={{ color: job.telemetry.verification_execution_ms ? text : '#38bdf8', fontWeight: 700 }}>
              {job.telemetry.verification_execution_ms ? `${(job.telemetry.verification_execution_ms / 1000).toFixed(1)}s` : 'ACTIVE'}
            </span>
          </div>
          {job.telemetry.total_ms !== null && job.telemetry.total_ms !== undefined && (
            <div>
              <span style={{ color: muted, display: 'block', fontSize: '0.63rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.04em' }}>Total</span>
              <span style={{ color: '#6366f1', fontWeight: 800 }}>{(job.telemetry.total_ms / 1000).toFixed(1)}s</span>
            </div>
          )}
        </div>
      )}

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

      {/* Progress steps for in-flight jobs with animated spinner & elapsed timer */}
      {job.progress && !showDossier && (
        <div style={{
          marginTop: '0.75rem',
          padding: '1rem 1.15rem',
          borderRadius: '0.75rem',
          background: dark ? '#0a101d' : '#f8fafc',
          border: `1px solid ${border}`
        }}>
          {/* Subheader with active spinner and elapsed counter */}
          <div style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            marginBottom: '0.85rem',
            paddingBottom: '0.6rem',
            borderBottom: `1px solid ${border}`
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.55rem', fontSize: '0.82rem', color: text, fontWeight: 700 }}>
              <span
                className="manual-search-spinner"
                style={{
                  width: 14,
                  height: 14,
                  border: '2px solid #3b82f6',
                  borderTopColor: 'transparent',
                  borderRadius: '50%',
                  display: 'inline-block'
                }}
              />
              <span>Live Verification Progress</span>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
              <span style={{ fontSize: '0.74rem', color: '#38bdf8', fontWeight: 700 }}>
                ⏱️ {job.elapsed || 0}s elapsed
              </span>
              <button
                onClick={() => onPollStatus && onPollStatus(job)}
                title="Force refresh status"
                style={{
                  fontSize: '0.72rem',
                  fontWeight: 700,
                  padding: '0.25rem 0.65rem',
                  borderRadius: '0.4rem',
                  border: `1px solid ${border}`,
                  background: dark ? '#1e293b' : '#ffffff',
                  color: text,
                  cursor: 'pointer'
                }}
              >
                🔄 Refresh
              </button>
            </div>
          </div>

          {/* Steps */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.6rem' }}>
            {job.progress.map((step, i) => (
              <div key={i} style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', fontSize: '0.8rem' }}>
                <span style={{
                  width: '24px',
                  height: '24px',
                  borderRadius: '50%',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  background: step.done ? '#059669' : step.active ? '#2563eb' : (dark ? '#1e293b' : '#e2e8f0'),
                  color: 'white',
                  fontSize: '0.7rem',
                  fontWeight: 800,
                  flexShrink: 0,
                  boxShadow: step.active ? '0 0 12px rgba(37,99,235,0.7)' : 'none'
                }}>
                  {step.done ? (
                    '✓'
                  ) : step.active ? (
                    <span
                      className="manual-search-spinner"
                      style={{
                        width: 12,
                        height: 12,
                        border: '2px solid #ffffff',
                        borderTopColor: 'transparent',
                        borderRadius: '50%',
                        display: 'inline-block'
                      }}
                    />
                  ) : (
                    <span style={{ color: muted, fontSize: '0.65rem' }}>○</span>
                  )}
                </span>
                <span style={{
                  color: step.done ? '#059669' : step.active ? '#38bdf8' : muted,
                  fontWeight: step.active ? 800 : step.done ? 600 : 400
                }}>
                  {step.label}
                  {step.active && (
                    <span style={{ marginLeft: '0.5rem', fontSize: '0.72rem', color: '#93c5fd', fontWeight: 600 }}>
                      (active)
                    </span>
                  )}
                </span>
              </div>
            ))}
          </div>

          {/* Queue & processing guidance */}
          <div style={{
            marginTop: '0.9rem',
            paddingTop: '0.75rem',
            borderTop: `1px solid ${border}`,
            fontSize: '0.73rem',
            color: muted,
            lineHeight: 1.5
          }}>
            💡 <strong>Why does verification take 60–120 seconds?</strong> OpenDB performs autonomous web crawling, robots.txt compliance checks, and LinkedIn executive discovery. If other companies are actively in the queue, this job runs automatically in sequence.
          </div>
        </div>
      )}

      {/* Final dossier */}
      {showDossier && <Dossier data={job.dossier} isDarkMode={isDarkMode} />}

      {/* Re-investigate button for completed jobs */}
      {job.stage === 'DONE' && (
        <button
          onClick={() => onPollStatus && onPollStatus(job)}
          style={{
            marginTop: '1rem',
            fontSize: '0.78rem',
            padding: '0.4rem 1rem',
            borderRadius: '0.5rem',
            border: dark ? '1px solid #334155' : '1px solid #cbd5e1',
            background: 'transparent',
            color: muted,
            cursor: 'pointer'
          }}
        >
          🔄 Re-check Dossier
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
  const muted = dark ? '#64748b' : '#94a3b8';

  function _makeProgress(statuses, current) {
    const steps = [
      { key: 'RESOLVING', label: 'Resolving company identity' },
      { key: 'AGENT1_QUEUED', label: 'Agent 1 crawling website' },
      { key: 'AGENT2_QUEUED', label: 'Agent 2 queued in verification line' },
      { key: 'PHASE1_VERIFYING', label: 'Agent 2 — Phase 1 core verification' },
      { key: 'PHASE2_SYNTHESIS', label: 'Phase 2 — Business synthesis & overview' },
      { key: 'LINKEDIN_DISCOVERY', label: 'LinkedIn key people discovery' },
      { key: 'FINAL_VERIFICATION', label: 'Final verification & confidence scoring' },
      { key: 'VERIFIED', label: 'Verified & persisted to OpenDB' },
    ];

    let idx = 1;
    if (current === 'RESOLVING') idx = 0;
    else if (current === 'AGENT1_QUEUED') idx = 1;
    else if (current === 'AGENT2_QUEUED') idx = 2;
    else if (current === 'PHASE1_RANKED' || current === 'PHASE1_VERIFYING' || current === 'PHASE1_RECRAWL_REQUIRED') idx = 3;
    else if (current === 'PHASE1_VERIFIED' || current === 'PHASE2_SYNTHESIS') idx = 4;
    else if (current === 'LINKEDIN_DISCOVERY' || current === 'LINKEDIN_CANDIDATES_FOUND' || current === 'LINKEDIN_PROFILE_CRAWL' || current === 'PERSON_MATCHING') idx = 5;
    else if (current === 'FINAL_VERIFICATION' || current === 'POSTGRES_SYNC_PENDING') idx = 6;
    else if (current === 'VERIFIED' || current === 'POSTGRES_VERIFIED') idx = 7;

    return steps.map((s, i) => ({
      ...s,
      done: i < idx,
      active: i === idx,
    }));
  }

  function updateJob(id, patch) {
    setJobs(prev => prev.map(j => j.id === id ? { ...j, ...patch } : j));
  }

  // Unified tracking function: polls /api/manual-search/track/{domain}
  // Continues polling smoothly with live elapsed seconds, animated spinners, and status updates
  const startTracking = useCallback((jobId, domain) => {
    if (pollRefs.current[jobId]) clearInterval(pollRefs.current[jobId]);
    const base = apiBase();
    const startTime = Date.now();

    const doPoll = async () => {
      try {
        const elapsedSec = Math.floor((Date.now() - startTime) / 1000);
        updateJob(jobId, { elapsed: elapsedSec });

        const res = await fetch(`${base}/manual-search/track/${encodeURIComponent(domain)}`);
        if (!res.ok) return;
        const data = await res.json();

        const currentStatus = data.status || 'AGENT1_QUEUED';
        const progress = _makeProgress([], currentStatus);

        let msg = data.message || '';
        if (currentStatus === 'AGENT1_QUEUED') {
          msg = `Agent 1 is crawling website & extracting metadata...`;
        } else if (currentStatus === 'AGENT2_QUEUED') {
          msg = `Website crawled! Queued for Agent 2 verification in background...`;
        } else if (currentStatus === 'PHASE1_VERIFYING') {
          msg = `Agent 2 is verifying domain ownership, emails, and core firmographics...`;
        } else if (currentStatus === 'PHASE2_SYNTHESIS') {
          msg = `Phase 2: Synthesizing business overview and technical profile...`;
        } else if (currentStatus === 'LINKEDIN_DISCOVERY') {
          msg = `Agent 2 is discovering key leadership & decision makers via LinkedIn...`;
        } else if (currentStatus === 'FINAL_VERIFICATION') {
          msg = `Calculating confidence scores and persisting canonical intelligence...`;
        }

        updateJob(jobId, {
          status: currentStatus,
          progress,
          message: msg,
          sessionId: data.session_id || null,
          telemetry: data.telemetry || null,
        });

        if (TERMINAL_STATUSES.has(currentStatus)) {
          clearInterval(pollRefs.current[jobId]);
          delete pollRefs.current[jobId];

          // Load final dossier
          const dresRes = await fetch(`${base}/manual-search/result/${encodeURIComponent(domain)}`);
          if (dresRes.ok) {
            const dossier = await dresRes.json();
            updateJob(jobId, { stage: 'DONE', dossier, status: currentStatus, message: 'Intelligence verification completed successfully.', telemetry: data.telemetry || null });
          } else {
            updateJob(jobId, { stage: 'DONE', dossier: data, status: currentStatus, telemetry: data.telemetry || null });
          }
        }
      } catch (e) {
        console.warn('[ManualSearch] Track error:', e);
      }
    };

    doPoll();
    pollRefs.current[jobId] = setInterval(doPoll, 3500);
  }, []);

  async function handleResolve() {
    const name = inputValue.trim();
    if (!name) return;
    setResolving(true);
    setError(null);
    const base = apiBase();
    const jobId = `job-${Date.now()}`;
    const newJob = {
      id: jobId,
      inputName: name,
      stage: 'RESOLVING',
      status: 'RESOLVING',
      message: 'Resolving company identity...',
      candidates: [],
      resolvedDomain: null,
      progress: null,
      dossier: null,
      elapsed: 0,
      telemetry: null,
      resolutionMs: null,
    };
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
        updateJob(jobId, { stage: 'AMBIGUOUS', status: 'AMBIGUOUS', candidates: data.candidates, message: data.message, resolutionMs: data.resolution_ms });
      } else if (data.status === 'NOT_FOUND' || data.status === 'RESOLUTION_RETRY_PENDING') {
        updateJob(jobId, { stage: data.status, status: data.status, message: data.message, resolutionMs: data.resolution_ms });
      } else {
        // EXISTING or UNIQUE — auto-investigate
        const c = data.candidates[0];
        updateJob(jobId, {
          stage: 'INVESTIGATING',
          status: 'INVESTIGATING',
          resolvedDomain: c.domain,
          candidates: data.candidates,
          resolutionMs: data.resolution_ms,
          message: `Resolved to ${c.canonical_name} (${c.domain}). Starting cache check...`
        });
        await runInvestigate(jobId, name, c.domain, data.resolution_ms);
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
    updateJob(jobId, {
      stage: 'INVESTIGATING',
      status: 'INVESTIGATING',
      resolvedDomain: candidate.domain,
      message: `Investigating ${candidate.canonical_name} (${candidate.domain})...`
    });
    await runInvestigate(jobId, job.inputName, candidate.domain, job.resolutionMs);
  }

  async function runInvestigate(jobId, companyName, domain, resolutionMs = null) {
    const base = apiBase();
    try {
      const res = await fetch(`${base}/manual-search/investigate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ company_name: companyName, domain, resolution_ms: resolutionMs }),
      });
      const data = await res.json();
      if (!res.ok) {
        updateJob(jobId, { stage: 'ERROR', status: 'ERROR', message: data.detail || 'Investigation failed.' });
        return;
      }
      if (data.status === 'CACHE_HIT') {
        updateJob(jobId, { stage: 'INVESTIGATING', status: 'CACHE_HIT', resolvedDomain: domain, message: data.message, telemetry: data.telemetry });
        const dresRes = await fetch(`${base}/manual-search/result/${encodeURIComponent(domain)}`);
        const dossier = dresRes.ok ? await dresRes.json() : data;
        updateJob(jobId, { stage: 'DONE', dossier, status: 'CACHE_HIT', telemetry: data.telemetry });
        return;
      }
      if (data.status === 'ALREADY_RUNNING' || data.status === 'AGENT1_QUEUED') {
        const progress = _makeProgress([], data.status);
        updateJob(jobId, {
          stage: 'TRACKING',
          status: data.status,
          resolvedDomain: domain,
          message: data.message,
          progress,
          telemetry: data.telemetry || { priority: data.priority || 'HIGH (9)' },
          elapsed: 0
        });
        startTracking(jobId, domain);
        return;
      }
      if (data.status === 'VERIFICATION_PENDING') {
        updateJob(jobId, { stage: 'VERIFICATION_PENDING', status: 'VERIFICATION_PENDING', resolvedDomain: domain, message: data.message, telemetry: data.telemetry });
        return;
      }
      updateJob(jobId, { stage: 'DONE', status: data.status, message: data.message, telemetry: data.telemetry });
    } catch (e) {
      updateJob(jobId, { stage: 'ERROR', status: 'ERROR', message: `Investigation failed: ${e.message}` });
    }
  }

  return (
    <div style={{ padding: '0 0 3rem 0' }}>
      <style>{ANIMATION_STYLES}</style>

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
          onPollStatus={(j) => {
            if (j.resolvedDomain) {
              startTracking(j.id, j.resolvedDomain);
            }
          }}
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
