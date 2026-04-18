import { useState, useEffect } from 'react';
import { Card } from '../components/Card';
import { Button } from '../components/Button';

function priorityColor(priority) {
  if (priority === 'high') return 'var(--red)';
  if (priority === 'medium') return 'var(--amber, #f59e0b)';
  return 'var(--green)';
}

function patchColor(r, g, b) {
  return `rgb(${Math.min(255, Math.round(r))}, ${Math.min(255, Math.round(g))}, ${Math.min(255, Math.round(b))})`;
}

export function SuggestedPatches({ session, getSuggestedPatches, runSuggestedPatches, onPrev, onNext }) {
  const [optimization, setOptimization] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [running, setRunning] = useState(false);
  const [runResult, setRunResult] = useState(null);
  const [runError, setRunRunError] = useState(null);

  useEffect(() => {
    if (!session?.id) return;
    loadOptimization();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session?.id]);

  async function loadOptimization() {
    if (!getSuggestedPatches || !session?.id) return;
    setLoading(true);
    setError(null);
    try {
      const result = await getSuggestedPatches(30);
      setOptimization(result?.optimization || null);
      if (!result?.optimization && result?.reason) {
        setError(result.reason);
      }
    } catch (err) {
      setError(err.message || String(err));
    } finally {
      setLoading(false);
    }
  }

  async function handleRun() {
    if (!optimization?.patches?.length) return;
    setRunning(true);
    setRunRunError(null);
    setRunResult(null);
    try {
      const result = await runSuggestedPatches(optimization.patches);
      setRunResult(result);
    } catch (err) {
      setRunRunError(err.message || String(err));
    } finally {
      setRunning(false);
    }
  }

  return (
    <div>
      <Card title="LLM-Optimized Patch Set" id="suggested-patches">
        {loading && <div className="muted text-sm">Generating optimized patch set...</div>}
        {error && <div style={{ color: 'var(--red)', fontSize: '0.85rem', marginBottom: 8 }}>Error: {error}</div>}

        {optimization && (
          <>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12, flexWrap: 'wrap' }}>
              <span style={{ fontSize: '0.85rem' }}>
                {optimization.patch_count} patches recommended
              </span>
              <span style={{ fontSize: '0.85rem', color: 'var(--muted)' }}>
                Confidence: {(optimization.confidence * 100).toFixed(0)}%
              </span>
              {optimization.auto_apply && (
                <span style={{ fontSize: '0.75rem', padding: '2px 8px', background: 'var(--green)', color: '#fff', borderRadius: 4 }}>
                  Auto-apply ready
                </span>
              )}
              <Button small onClick={loadOptimization}>Refresh</Button>
            </div>

            {optimization.rationale && (
              <div className="muted text-sm" style={{ marginBottom: 12, fontStyle: 'italic' }}>
                {optimization.rationale}
              </div>
            )}

            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.82rem' }}>
                <thead>
                  <tr style={{ borderBottom: '2px solid var(--border)' }}>
                    <th style={{ textAlign: 'left', padding: '6px 8px' }}>#</th>
                    <th style={{ textAlign: 'left', padding: '6px 8px' }}>Label</th>
                    <th style={{ textAlign: 'left', padding: '6px 8px' }}>Color</th>
                    <th style={{ textAlign: 'right', padding: '6px 8px' }}>RGB</th>
                    <th style={{ textAlign: 'right', padding: '6px 8px' }}>Nits</th>
                    <th style={{ textAlign: 'center', padding: '6px 8px' }}>Priority</th>
                  </tr>
                </thead>
                <tbody>
                  {optimization.patches.map((p, i) => (
                    <tr key={i} style={{ borderBottom: '1px solid var(--border)' }}>
                      <td style={{ padding: '5px 8px', color: 'var(--muted)' }}>{i + 1}</td>
                      <td style={{ padding: '5px 8px' }}>
                        <div>{p.label || `Patch ${i + 1}`}</div>
                        {p.rationale && (
                          <div className="muted text-sm" style={{ fontSize: '0.75rem', maxWidth: 300 }}>
                            {p.rationale}
                          </div>
                        )}
                      </td>
                      <td style={{ padding: '5px 8px' }}>
                        <div style={{
                          width: 20, height: 20, borderRadius: 3,
                          background: patchColor(p.r, p.g, p.b),
                          border: '1px solid var(--border)',
                        }} />
                      </td>
                      <td style={{ padding: '5px 8px', fontFamily: 'monospace', textAlign: 'right' }}>
                        {p.r},{p.g},{p.b}
                      </td>
                      <td style={{ padding: '5px 8px', fontFamily: 'monospace', textAlign: 'right' }}>
                        {p.nits.toFixed(0)}
                      </td>
                      <td style={{ padding: '5px 8px', textAlign: 'center' }}>
                        <span style={{
                          display: 'inline-block', width: 8, height: 8, borderRadius: '50%',
                          background: priorityColor(p.priority),
                        }} title={p.priority} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div style={{ marginTop: 16, display: 'flex', gap: 8 }}>
              <Button primary disabled={running || !optimization.patches.length} onClick={handleRun}>
                {running ? 'Sending to ZRO...' : 'Run These Patches'}
              </Button>
            </div>

            {runResult && (
              <div style={{ marginTop: 12, padding: 10, background: 'var(--surface2)', borderRadius: 6, fontSize: '0.85rem' }}>
                <div style={{ fontWeight: 600, marginBottom: 4 }}>
                  {runResult.succeeded}/{runResult.accepted} patches measured successfully
                </div>
                {runResult.results?.some(r => !r.ok) && (
                  <div style={{ color: 'var(--red)', fontSize: '0.8rem', marginTop: 4 }}>
                    Failed: {runResult.results.filter(r => !r.ok).map(r => r.label).join(', ')}
                  </div>
                )}
              </div>
            )}

            {runError && (
              <div style={{ marginTop: 12, padding: 10, background: 'rgba(220,38,38,0.1)', borderRadius: 6, fontSize: '0.85rem', color: 'var(--red)' }}>
                {runError}
              </div>
            )}
          </>
        )}

        {!optimization && !loading && !error && (
          <div className="muted text-sm">
            No optimization data available. Click Refresh to generate, or configure the LLM first.
          </div>
        )}
      </Card>

      <div className="btn-group">
        <Button onClick={onPrev}>← Back</Button>
        <Button primary onClick={onNext}>Continue to Report →</Button>
      </div>
    </div>
  );
}
