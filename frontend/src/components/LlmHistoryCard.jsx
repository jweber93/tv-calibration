import { useState, useEffect } from 'react';
import * as api from '../api/client';

export function LlmHistoryCard({ sid }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(false);
    Promise.resolve(sid ? api.getLlmHistorySummary(sid) : null)
      .then((res) => {
        if (!cancelled) setData(res);
      })
      .catch(() => {
        if (!cancelled) setError(true);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [sid]);

  if (loading) return null;

  if (error) {
    return (
      <div className="card">
        <div style={{ fontSize: '1rem', fontWeight: 700, color: 'var(--ink)', marginBottom: 12 }}>
          Bootstrap from History
        </div>
        <div style={{ fontSize: '0.82rem', color: 'var(--line2)', fontStyle: 'italic' }}>
          Unable to load history.
        </div>
      </div>
    );
  }

  const sessionCount = data?.session_count ?? 0;
  const hasFinals = data?.has_final_settings ?? false;

  return (
    <div className="card">
      <div style={{ fontSize: '1rem', fontWeight: 700, color: 'var(--ink)', marginBottom: 12 }}>
        Bootstrap from History
      </div>
      <div style={{ fontSize: '0.82rem', color: 'var(--ink2)', lineHeight: 1.6 }}>
        {sessionCount === 0 ? (
          <>First calibration for this TV — the LLM will learn from this run.</>
        ) : (
          <>Priming the LLM from {sessionCount} prior session{sessionCount !== 1 ? 's' : ''}.</>
        )}
      </div>
      {sessionCount > 0 && !hasFinals && (
        <div style={{ fontSize: '0.78rem', color: 'var(--line2)', marginTop: 10, lineHeight: 1.5 }}>
          Prior runs have no saved settings yet — predictions improve after the next completed run.
        </div>
      )}
    </div>
  );
}
