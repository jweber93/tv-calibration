import { useState, useCallback } from 'react';
import { Card } from '../components/Card';
import { Button } from '../components/Button';
import { MeasurementTable } from '../components/MeasurementTable';
import { Tooltip } from '../components/Tooltip';
import { OverlayGammaChart } from '../charts/OverlayGammaChart';
import { OverlayDeChart } from '../charts/OverlayDeChart';
import { OverlayCIEScatter } from '../charts/OverlayCIEScatter';
import { api } from '../api/client';

export function ComparisonPage({ profiles }) {
  const [tvKey, setTvKey] = useState('');
  const [history, setHistory] = useState(null);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [sessionIdA, setSessionIdA] = useState('');
  const [sessionIdB, setSessionIdB] = useState('');
  const [comparison, setComparison] = useState(null);
  const [comparisonLoading, setComparisonLoading] = useState(false);
  const [deltaSummary, setDeltaSummary] = useState(null);
  const [deltaSummaryLoading, setDeltaSummaryLoading] = useState(false);
  const [error, setError] = useState(null);

  const hasSession = sessionIdA && sessionIdB;

  const loadHistory = useCallback(async (key) => {
    if (!key) {
      setHistory(null);
      return;
    }
    setHistoryLoading(true);
    setError(null);
    try {
      const data = await api.getReportHistory(key, 20);
      setHistory(data.sessions || []);
    } catch (err) {
      setError(err.message);
    } finally {
      setHistoryLoading(false);
    }
  }, []);

  const handleTvChange = (e) => {
    const key = e.target.value;
    setTvKey(key);
    setSessionIdA('');
    setSessionIdB('');
    setComparison(null);
    setDeltaSummary(null);
    loadHistory(key);
  };

  const handleCompare = async () => {
    if (!sessionIdA || !sessionIdB) return;
    setComparisonLoading(true);
    setError(null);
    try {
      const data = await api.getCompare(sessionIdA, sessionIdB);
      setComparison(data);
    } catch (err) {
      setError(err.message);
    } finally {
      setComparisonLoading(false);
    }
  };

  const handleDeltaSummary = async () => {
    if (!sessionIdA || !sessionIdB) return;
    setDeltaSummaryLoading(true);
    setError(null);
    try {
      const data = await api.getCompareDeltaSummary(sessionIdA, sessionIdB);
      setDeltaSummary(data);
    } catch (err) {
      setError(err.message);
    } finally {
      setDeltaSummaryLoading(false);
    }
  };

  function downloadComparePdf() {
    if (!sessionIdA || !sessionIdB) return;
    api.downloadComparePdf(sessionIdA, sessionIdB).catch(e => alert(`PDF export failed: ${e.message}`));
  }

  const sessA = comparison?.session_a;
  const sessB = comparison?.session_b;
  const deltas = comparison?.deltas;
  const repA = sessA?.report;
  const repB = sessB?.report;
  const tvMismatch = comparison?.tv_mismatch;
  const modeMismatch = comparison?.mode_mismatch;

  function fmtValue(v, digits = 2, suffix = '') {
    if (v == null) return '—';
    return `${Number(v).toFixed(digits)}${suffix}`;
  }

  function deltaColor(val, lowerIsBetter) {
    if (val == null) return null;
    if (val < 0) return lowerIsBetter ? 'var(--green)' : 'var(--red)';
    if (val > 0) return lowerIsBetter ? 'var(--red)' : 'var(--green)';
    return 'var(--ink2)';
  }

  return (
    <div>
      <div style={{ background: 'var(--panel)', border: '1px solid var(--line)', borderRadius: 'var(--radius-lg)', padding: '20px 24px', marginBottom: 20 }}>
        <div style={{ fontSize: '0.72rem', letterSpacing: '0.18em', textTransform: 'uppercase', color: 'var(--ink2)', marginBottom: 12 }}>
          Session Comparison
        </div>
        <div style={{ display: 'flex', gap: 12, alignItems: 'flex-end', flexWrap: 'wrap' }}>
          <div style={{ flex: '1 1 200px' }}>
            <label style={{ fontSize: '0.72rem', color: 'var(--ink2)', display: 'block', marginBottom: 4 }}>TV Model</label>
            <select value={tvKey} onChange={handleTvChange}>
              <option value="">Select a TV…</option>
              {profiles.map(p => (
                <option key={p.key} value={p.key}>{p.name}</option>
              ))}
            </select>
          </div>
          <div style={{ flex: '1 1 180px' }}>
            <label style={{ fontSize: '0.72rem', color: 'var(--ink2)', display: 'block', marginBottom: 4 }}>
              Session A (before)
              <Tooltip text="The baseline or earlier calibration session" />
            </label>
            <select
              value={sessionIdA}
              onChange={e => setSessionIdA(e.target.value)}
              disabled={!history || historyLoading}
            >
              <option value="">Select session A…</option>
              {history?.map(s => (
                <option key={s.session_id} value={s.session_id}>
                  {s.is_baseline ? '★ ' : ''}{s.mode} — {s.date ? new Date(s.date).toLocaleDateString() : 'unknown'}
                </option>
              ))}
            </select>
          </div>
          <div style={{ flex: '1 1 180px' }}>
            <label style={{ fontSize: '0.72rem', color: 'var(--ink2)', display: 'block', marginBottom: 4 }}>
              Session B (after)
              <Tooltip text="The more recent calibration session" />
            </label>
            <select
              value={sessionIdB}
              onChange={e => setSessionIdB(e.target.value)}
              disabled={!history || historyLoading}
            >
              <option value="">Select session B…</option>
              {history?.map(s => (
                <option key={s.session_id} value={s.session_id}>
                  {s.is_baseline ? '★ ' : ''}{s.mode} — {s.date ? new Date(s.date).toLocaleDateString() : 'unknown'}
                </option>
              ))}
            </select>
          </div>
          <Button
            onClick={handleCompare}
            disabled={!hasSession || comparisonLoading}
          >
            {comparisonLoading ? 'Loading…' : 'Compare'}
          </Button>
        </div>
      </div>

      {error && <div className="red text-sm" style={{ padding: 16, background: 'var(--red-dim)', borderRadius: 'var(--radius)', border: '1px solid var(--line)' }}>Error: {error}</div>}

      {historyLoading && !history && (
        <div className="muted text-sm" style={{ padding: 16 }}>
          <span className="spinner" style={{ marginRight: 8 }} />Loading history…
        </div>
      )}

      {!tvKey && !history && (
        <div className="muted text-sm" style={{ padding: 16, textAlign: 'center' }}>
          Select a TV model above to view calibration history and compare sessions.
        </div>
      )}

      {comparison && (
        <>
          {tvMismatch && (
            <div style={{ padding: '12px 16px', borderRadius: 'var(--radius)', marginBottom: 16, background: 'var(--yellow-dim)', border: '1px solid var(--yellow)', fontSize: '0.83rem', color: 'var(--yellow)' }}>
              ⚠ Sessions are from different TV models — delta values may not be meaningful.
            </div>
          )}
          {modeMismatch && (
            <div style={{ padding: '12px 16px', borderRadius: 'var(--radius)', marginBottom: 16, background: 'var(--yellow-dim)', border: '1px solid var(--yellow)', fontSize: '0.83rem', color: 'var(--yellow)' }}>
              ⚠ Sessions use different calibration modes — some metrics may not be directly comparable.
            </div>
          )}

          {/* Delta hero */}
          <div style={{ background: 'var(--panel2)', border: '1px solid var(--line)', borderRadius: 'var(--radius-lg)', padding: '32px 24px 28px', marginBottom: 16, textAlign: 'center' }}>
            <div style={{ fontSize: '0.65rem', letterSpacing: '0.2em', textTransform: 'uppercase', color: 'var(--ink3)', marginBottom: 10 }}>
              Session B vs A — Improvement Δ
            </div>

            {/* Big improvement delta */}
            {(() => {
              const impD = deltas?.improvement_pct;
              const impPositive = impD > 0;
              return (
                <>
                  <div style={{ fontSize: '3.5rem', fontWeight: 700, lineHeight: 1, fontFamily: 'var(--mono)', fontVariantNumeric: 'tabular-nums', color: impD == null ? 'var(--ink)' : deltaColor(impD, false), marginBottom: 4 }}>
                    {impD != null ? `${impPositive ? '+' : ''}${impD.toFixed(1)}%` : '—'}
                  </div>
                  <div style={{ fontSize: '0.75rem', color: 'var(--ink2)', marginBottom: 24 }}>
                    accuracy improvement Δ (B − A)
                  </div>
                </>
              );
            })()}

            {/* Post-Cal ΔE A → B */}
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', maxWidth: 460, margin: '0 auto' }}>
              <div style={{ flex: 1, textAlign: 'right' }}>
                <div style={{ fontSize: '1.5rem', fontWeight: 700, fontFamily: 'var(--mono)', color: 'var(--red)' }}>
                  {repA?.post_cal?.avg_de != null ? repA.post_cal.avg_de.toFixed(2) : '—'}
                </div>
                <div style={{ fontSize: '0.65rem', letterSpacing: '0.12em', textTransform: 'uppercase', color: 'var(--ink3)', marginTop: 2 }}>
                  Post-Cal ΔE · A
                </div>
              </div>
              <div style={{ padding: '0 20px', color: 'var(--ink3)', fontSize: '1.2rem', userSelect: 'none' }}>→</div>
              <div style={{ flex: 1, textAlign: 'left' }}>
                <div style={{ fontSize: '1.5rem', fontWeight: 700, fontFamily: 'var(--mono)', color: 'var(--green)' }}>
                  {repB?.post_cal?.avg_de != null ? repB.post_cal.avg_de.toFixed(2) : '—'}
                </div>
                <div style={{ fontSize: '0.65rem', letterSpacing: '0.12em', textTransform: 'uppercase', color: 'var(--ink3)', marginTop: 2 }}>
                  Post-Cal ΔE · B
                </div>
              </div>
            </div>
          </div>

          {/* Key Metrics Table */}
          <Card title="Key Metrics">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Metric</th>
                  <th style={{ textAlign: 'right', color: 'var(--red)' }}>
                    Session A <br /><span style={{ color: 'var(--ink3)', fontWeight: 400, textTransform: 'none', letterSpacing: 0 }}>{sessA?.date ? new Date(sessA.date).toLocaleDateString() : ''}</span>
                  </th>
                  <th style={{ textAlign: 'right', color: 'var(--green)' }}>
                    Session B <br /><span style={{ color: 'var(--ink3)', fontWeight: 400, textTransform: 'none', letterSpacing: 0 }}>{sessB?.date ? new Date(sessB.date).toLocaleDateString() : ''}</span>
                  </th>
                  <th style={{ textAlign: 'right' }}>Δ (B − A)</th>
                </tr>
              </thead>
              <tbody>
                {[
                  { label: 'Pre-Cal Avg ΔE', a: repA?.pre_cal?.avg_de, b: repB?.pre_cal?.avg_de, d: deltas?.pre_cal_avg_de, lowerIsBetter: true },
                  { label: 'Post-Cal Avg ΔE', a: repA?.post_cal?.avg_de, b: repB?.post_cal?.avg_de, d: deltas?.post_cal_avg_de, lowerIsBetter: true },
                  { label: 'Pre-Cal Max ΔE', a: repA?.pre_cal?.max_de, b: repB?.pre_cal?.max_de, d: deltas?.pre_cal_max_de, lowerIsBetter: true },
                  { label: 'Post-Cal Max ΔE', a: repA?.post_cal?.max_de, b: repB?.post_cal?.max_de, d: deltas?.post_cal_max_de, lowerIsBetter: true },
                  { label: 'WB Avg ΔE', a: repA?.white_balance?.avg_de, b: repB?.white_balance?.avg_de, d: deltas?.wb_avg_de, lowerIsBetter: true },
                  { label: 'CMS Avg ΔE', a: repA?.color_tuner?.avg_de, b: repB?.color_tuner?.avg_de, d: deltas?.cms_avg_de, lowerIsBetter: true },
                  { label: 'Avg Gamma', a: repA?.gamma?.avg_gamma, b: repB?.gamma?.avg_gamma, d: deltas?.gamma_avg, digits: 3 },
                  { label: 'Peak Luminance', a: repA?.peak_luminance, b: repB?.peak_luminance, d: deltas?.peak_luminance, digits: 1, suffix: ' nits', lowerIsBetter: false },
                  { label: 'Improvement %', a: repA?.improvement_pct, b: repB?.improvement_pct, d: deltas?.improvement_pct, digits: 1, suffix: '%', lowerIsBetter: false },
                ].map(row => (
                  <tr key={row.label}>
                    <td style={{ fontFamily: 'inherit', fontWeight: 500 }}>{row.label}</td>
                    <td style={{ textAlign: 'right' }}>{fmtValue(row.a, row.digits || 2, row.suffix || '')}</td>
                    <td style={{ textAlign: 'right' }}>{fmtValue(row.b, row.digits || 2, row.suffix || '')}</td>
                    <td style={{ textAlign: 'right', color: deltaColor(row.d, row.lowerIsBetter), fontWeight: 600 }}>{fmtValue(row.d, row.digits || 2, row.suffix || '')}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>

          {/* LLM Delta Summary */}
          <Card title="LLM Analysis">
            <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
              <Button onClick={handleDeltaSummary} disabled={deltaSummaryLoading}>
                {deltaSummaryLoading ? 'Generating…' : 'Generate Analysis'}
              </Button>
              <Button onClick={downloadComparePdf}>Download PDF</Button>
            </div>
            {deltaSummary?.summary ? (
              <div style={{ fontSize: '0.85rem', lineHeight: 1.6, color: 'var(--ink)', whiteSpace: 'pre-wrap' }}>
                {deltaSummary.summary}
              </div>
            ) : deltaSummary?.reason ? (
              <div className="muted text-sm">{deltaSummary.reason}</div>
            ) : deltaSummaryLoading ? (
              <div className="muted text-sm"><span className="spinner" style={{ marginRight: 8 }} />Analyzing delta…</div>
            ) : (
              <div className="muted text-sm">Click "Generate Analysis" to get a plain-English summary of what improved and regressed.</div>
            )}
          </Card>

          {/* Overlay Charts */}
          <Card title="Gamma Overlay — Session A vs B">
            <OverlayGammaChart
              measurementsA={repA?.gamma_measurements || []}
              measurementsB={repB?.gamma_measurements || []}
            />
          </Card>

          <Card title="ΔE Overlay — Session A vs B">
            <OverlayDeChart
              measurementsA={repA?.post_cal?.measurements || []}
              measurementsB={repB?.post_cal?.measurements || []}
            />
          </Card>

          <Card title="CIE xy Scatter Overlay — Session A vs B">
            <OverlayCIEScatter
              measurementsA={repA?.wb_measurements || []}
              measurementsB={repB?.wb_measurements || []}
              targetXy={repA?.target?.white_point || [0.3127, 0.3290]}
            />
          </Card>

          {/* Measurement tables side by side */}
          <div className="two-col">
            <Card title={`Post-Cal: ${sessA?.tv || ''} (${sessA?.mode || ''})`}>
              <MeasurementTable measurements={repA?.post_cal?.measurements || []} />
            </Card>
            <Card title={`Post-Cal: ${sessB?.tv || ''} (${sessB?.mode || ''})`}>
              <MeasurementTable measurements={repB?.post_cal?.measurements || []} />
            </Card>
          </div>
        </>
      )}
    </div>
  );
}
