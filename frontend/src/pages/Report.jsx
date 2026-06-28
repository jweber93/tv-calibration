import { useEffect, useState } from 'react';
import { Card, StatCard } from '../components/Card';
import { Button } from '../components/Button';
import { MeasurementTable } from '../components/MeasurementTable';
import { DeChart } from '../charts/DeChart';
import { GammaChart } from '../charts/GammaChart';
import { api } from '../api/client';
import { fmtNits } from '../utils/fmt';

export function Report({ session, onPrev }) {
  const [report, setReport] = useState(null);
  const [error, setError] = useState(null);
  const sid = session.id;

  useEffect(() => {
    api.getReport(sid)
      .then(setReport)
      .catch(e => setError(e.message));
  }, [sid]);

  function downloadJson() {
    if (!report) return;
    const blob = new Blob([JSON.stringify(report, null, 2)], { type: 'application/json' });
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement('a');
    a.href = url; a.download = `calibration_${sid}.json`; a.click();
    URL.revokeObjectURL(url);
  }

  const pre   = report?.pre_cal  || {};
  const post  = report?.post_cal || {};
  const preMeas  = pre.measurements  || [];
  const postMeas = post.measurements || [];

  const improvementPct = report?.improvement_pct;
  const improvementPositive = improvementPct != null && improvementPct > 0;

  return (
    <>
      {/* Result Hero */}
      <div style={{ background: 'var(--panel2)', border: '1px solid var(--line)', borderRadius: 'var(--radius-lg)', padding: '36px 24px 28px', marginBottom: 16, textAlign: 'center' }}>
        <div style={{ fontSize: '0.65rem', letterSpacing: '0.2em', textTransform: 'uppercase', color: 'var(--ink3)', marginBottom: 10 }}>
          Calibration Complete — {session.tv} · {session.mode}
        </div>

        {/* Big improvement number */}
        <div style={{ fontSize: '3.5rem', fontWeight: 700, lineHeight: 1, color: improvementPositive ? 'var(--green)' : 'var(--ink)', marginBottom: 4 }}>
          {improvementPct != null ? `${improvementPct > 0 ? '+' : ''}${improvementPct.toFixed(1)}%` : '—'}
        </div>
        <div style={{ fontSize: '0.75rem', color: 'var(--ink2)', marginBottom: 24 }}>
          accuracy improvement
        </div>

        {/* Before → After ΔE bar */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 0, maxWidth: 440, margin: '0 auto' }}>
          <div style={{ flex: 1, textAlign: 'right' }}>
            <div style={{ fontSize: '1.5rem', fontWeight: 700, color: 'var(--red)' }}>
              {pre.avg_de != null ? pre.avg_de.toFixed(2) : '—'}
            </div>
            <div style={{ fontSize: '0.65rem', letterSpacing: '0.12em', textTransform: 'uppercase', color: 'var(--ink3)', marginTop: 2 }}>
              Pre-Cal ΔE
            </div>
          </div>
          <div style={{ padding: '0 20px', color: 'var(--ink3)', fontSize: '1.2rem', userSelect: 'none' }}>→</div>
          <div style={{ flex: 1, textAlign: 'left' }}>
            <div style={{ fontSize: '1.5rem', fontWeight: 700, color: 'var(--green)' }}>
              {post.avg_de != null ? post.avg_de.toFixed(2) : '—'}
            </div>
            <div style={{ fontSize: '0.65rem', letterSpacing: '0.12em', textTransform: 'uppercase', color: 'var(--ink3)', marginTop: 2 }}>
              Post-Cal ΔE
            </div>
          </div>
        </div>
      </div>

      {error && <div className="red text-sm" style={{ padding: 16 }}>Error loading report: {error}</div>}

      {!report && !error && (
        <div className="muted text-sm" style={{ padding: 16 }}>
          <span className="spinner" style={{ marginRight: 8 }} />Loading report…
        </div>
      )}

      {report && (
        <>
          {/* Compact stat grid */}
          <div className="four-col" style={{ marginBottom: 16 }}>
            <StatCard
              value={post.avg_de != null ? post.avg_de.toFixed(2) : '—'}
              label="Post-Cal Avg ΔE"
              color="green"
              tooltip="Average Delta E after calibration. ΔE < 1 is excellent."
            />
            <StatCard
              value={fmtNits(report.peak_luminance)}
              label="Peak Luminance"
              color="accent"
              tooltip="Maximum brightness measured in cd/m² (nits)."
            />
            <StatCard
              value={report.gamma?.avg_gamma != null ? report.gamma.avg_gamma.toFixed(2) : '—'}
              label="Tracking Gamma"
              tooltip="Average measured gamma exponent. Target is typically 2.2 or BT.1886."
            />
            <StatCard
              value={post.max_de != null ? post.max_de.toFixed(2) : '—'}
              label="Post-Cal Max ΔE"
              tooltip="Worst-case Delta E after calibration. ΔE < 3 is generally acceptable."
            />
          </div>

          <div className="two-col">
            <Card title="Pre-Cal Grayscale">
              <DeChart measurements={preMeas} label="Pre-Cal ΔE" />
              <div className="mt-3"><MeasurementTable measurements={preMeas} /></div>
            </Card>
            <Card title="Post-Cal Grayscale">
              <DeChart measurements={postMeas} label="Post-Cal ΔE" />
              <div className="mt-3"><MeasurementTable measurements={postMeas} /></div>
            </Card>
          </div>

          <Card title="Gamma Verification">
            {(report.gamma_measurements || []).length > 0 ? (
              <>
                <GammaChart measurements={report.gamma_measurements} />
                <div className="mt-3"><MeasurementTable measurements={report.gamma_measurements} includeGamma /></div>
              </>
            ) : (
              <div className="muted text-sm">No gamma measurements recorded.</div>
            )}
          </Card>

          <div className="two-col">
            <Card title="White Balance">
              <MeasurementTable measurements={report.wb_measurements || []} />
            </Card>
            <Card title="Color Tuner">
              <MeasurementTable measurements={report.cms_measurements || []} />
            </Card>
          </div>
        </>
      )}

      <div className="btn-group">
        <Button onClick={onPrev}>← Back</Button>
        <Button onClick={() => window.open(`/api/session/${sid}/report/html`, '_blank')}>
          Open Full HTML Report
        </Button>
        <Button onClick={() => api.downloadPdf(sid, session.tv).catch(e => alert(`PDF export failed: ${e.message}`))}>
          Download PDF
        </Button>
        <Button onClick={downloadJson}>Download JSON</Button>
      </div>
    </>
  );
}
