import { useState, useEffect } from 'react';
import { Card } from '../components/Card';
import { Button } from '../components/Button';

const PRIORITY_ORDER = { high: 0, medium: 1, low: 2 };
const PRIORITY_WEIGHT = { high: 4, medium: 3, low: 2 };
const PRIORITY_BADGE = { high: 'badge-high', medium: 'badge-medium', low: 'badge-low' };

function priorityColor(priority) {
  if (priority === 'high') return 'var(--red)';
  if (priority === 'medium') return 'var(--yellow)';
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
  const [runError, setRunError] = useState(null);

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
    setRunError(null);
    setRunResult(null);
    try {
      const result = await runSuggestedPatches(optimization.patches);
      setRunResult(result);
    } catch (err) {
      setRunError(err.message || String(err));
    } finally {
      setRunning(false);
    }
  }

  const patches = optimization?.patches || [];
  const sortedPatches = [...patches]
    .map((p, i) => ({ ...p, _idx: i }))
    .sort((a, b) => (PRIORITY_ORDER[a.priority] ?? 1) - (PRIORITY_ORDER[b.priority] ?? 1));

  const counts = patches.reduce((acc, p) => {
    const key = p.priority || 'low';
    acc[key] = (acc[key] || 0) + 1;
    return acc;
  }, {});

  const confidence = optimization?.confidence != null ? Math.round(optimization.confidence * 100) : null;

  return (
    <div id="suggested-patches">
      {/* Decision hero */}
      <div style={{ background: 'var(--panel2)', border: '1px solid var(--line)', borderRadius: 'var(--radius-lg)', padding: '28px 24px', marginBottom: 16 }}>
        <div style={{ fontSize: '0.65rem', letterSpacing: '0.2em', textTransform: 'uppercase', color: 'var(--ink3)', marginBottom: 16 }}>
          Predictive Patch Density
        </div>

        {loading && (
          <div className="muted text-sm"><span className="spinner" style={{ marginRight: 8 }} />Generating optimized patch set…</div>
        )}
        {error && !loading && (
          <div className="red text-sm" style={{ padding: '12px 14px', background: 'var(--red-dim)', borderRadius: 'var(--radius)', border: '1px solid var(--line)' }}>
            {error}
          </div>
        )}

        {optimization && !loading && (
          <>
            <div style={{ display: 'flex', alignItems: 'flex-start', gap: 28, flexWrap: 'wrap' }}>
              {/* Confidence */}
              <div>
                <div style={{ fontSize: '3rem', fontWeight: 700, lineHeight: 1, fontFamily: 'var(--mono)', fontVariantNumeric: 'tabular-nums', color: confidence != null && confidence >= 70 ? 'var(--green)' : 'var(--ink)' }}>
                  {confidence != null ? `${confidence}%` : '—'}
                </div>
                <div style={{ fontSize: '0.65rem', letterSpacing: '0.12em', textTransform: 'uppercase', color: 'var(--ink3)', marginTop: 6 }}>
                  Model Confidence
                </div>
              </div>

              {/* Patch count */}
              <div>
                <div style={{ fontSize: '3rem', fontWeight: 700, lineHeight: 1, fontFamily: 'var(--mono)', fontVariantNumeric: 'tabular-nums', color: 'var(--accent)' }}>
                  {optimization.patch_count ?? patches.length}
                </div>
                <div style={{ fontSize: '0.65rem', letterSpacing: '0.12em', textTransform: 'uppercase', color: 'var(--ink3)', marginTop: 6 }}>
                  Patches Recommended
                </div>
              </div>

              {/* Auto-apply state */}
              <div style={{ alignSelf: 'center' }}>
                {optimization.auto_apply ? (
                  <span className="badge" style={{ background: 'var(--green-dim)', color: 'var(--green)', border: '1px solid rgba(56,210,154,0.35)' }}>
                    ● AUTO-APPLY READY
                  </span>
                ) : (
                  <span className="badge" style={{ background: 'var(--panel3)', color: 'var(--ink2)', border: '1px solid var(--line)' }}>
                    REVIEW BEFORE APPLYING
                  </span>
                )}
              </div>
            </div>

            {optimization.rationale && (
              <div style={{ marginTop: 20, padding: '12px 16px', background: 'var(--accent-dim)', border: '1px solid rgba(46,199,230,0.25)', borderLeft: '3px solid var(--accent)', borderRadius: 'var(--radius)', fontSize: '0.85rem', lineHeight: 1.55, color: 'var(--ink)' }}>
                {optimization.rationale}
              </div>
            )}

            <div style={{ marginTop: 20, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              <Button primary disabled={running || !patches.length} onClick={handleRun}>
                {running ? 'Sending to ZRO…' : 'Run These Patches'}
              </Button>
              <Button onClick={loadOptimization} disabled={loading}>Refresh</Button>
            </div>

            {runResult && (
              <div style={{ marginTop: 16, padding: '12px 14px', background: 'var(--panel)', border: '1px solid var(--line)', borderRadius: 'var(--radius)', fontSize: '0.85rem' }}>
                <div style={{ fontWeight: 600, marginBottom: 4 }}>
                  {runResult.succeeded}/{runResult.accepted} patches measured successfully
                </div>
                {runResult.results?.some(r => !r.ok) && (
                  <div className="red" style={{ fontSize: '0.8rem', marginTop: 4 }}>
                    Failed: {runResult.results.filter(r => !r.ok).map(r => r.label).join(', ')}
                  </div>
                )}
              </div>
            )}

            {runError && (
              <div className="red text-sm" style={{ marginTop: 16, padding: '12px 14px', background: 'var(--red-dim)', borderRadius: 'var(--radius)', border: '1px solid var(--line)' }}>
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
      </div>

      {/* Density map */}
      {optimization && patches.length > 0 && (
        <Card title="Patch Density Map">
          <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', marginBottom: 16 }}>
            {['high', 'medium', 'low'].filter(k => counts[k]).map(k => (
              <div key={k} style={{ display: 'flex', alignItems: 'center', gap: 7, fontSize: '0.76rem', color: 'var(--ink2)' }}>
                <span style={{ width: 10, height: 10, borderRadius: 2, background: priorityColor(k) }} />
                <span style={{ textTransform: 'capitalize' }}>{k}</span>
                <span style={{ fontFamily: 'var(--mono)', color: 'var(--ink)' }}>{counts[k]}</span>
              </div>
            ))}
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {sortedPatches.map((p) => {
              const color = priorityColor(p.priority);
              const weight = PRIORITY_WEIGHT[p.priority] ?? 2;
              return (
                <div
                  key={p._idx}
                  style={{
                    display: 'flex', alignItems: 'center', gap: 14,
                    background: 'var(--panel2)', border: '1px solid var(--line)',
                    borderLeft: `${weight}px solid ${color}`,
                    borderRadius: 'var(--radius-lg)', padding: '12px 16px',
                  }}
                >
                  <div style={{
                    width: 28, height: 28, borderRadius: 4, flexShrink: 0,
                    background: patchColor(p.r, p.g, p.b), border: '1px solid var(--line2)',
                  }} title={`rgb(${p.r}, ${p.g}, ${p.b})`} />

                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                      <span style={{ fontWeight: 600, fontSize: '0.86rem' }}>{p.label || `Patch ${p._idx + 1}`}</span>
                      <span className={`badge ${PRIORITY_BADGE[p.priority] || 'badge-low'}`} style={{ marginLeft: 0 }}>
                        {(p.priority || 'low').toUpperCase()}
                      </span>
                    </div>
                    {p.rationale && (
                      <div className="muted" style={{ fontSize: '0.76rem', marginTop: 3 }}>{p.rationale}</div>
                    )}
                  </div>

                  <div style={{ textAlign: 'right', flexShrink: 0 }}>
                    <div style={{ fontFamily: 'var(--mono)', fontSize: '0.8rem', fontVariantNumeric: 'tabular-nums' }}>
                      {p.r},{p.g},{p.b}
                    </div>
                    <div className="muted" style={{ fontFamily: 'var(--mono)', fontSize: '0.74rem', marginTop: 2 }}>
                      {p.nits != null ? `${p.nits.toFixed(0)} nits` : '—'}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </Card>
      )}

      <div className="btn-group">
        <Button onClick={onPrev}>← Back</Button>
        <Button primary onClick={onNext}>Continue to Report →</Button>
      </div>
    </div>
  );
}
