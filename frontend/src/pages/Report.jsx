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

  return (
    <>
      <div style={{ background: 'var(--surface2)', border: '1px solid var(--border)', borderRadius: 'var(--radius-lg)', padding: '28px 24px', marginBottom: 20, textAlign: 'center' }}>
        <div style={{ fontSize: '0.7rem', letterSpacing: '0.18em', textTransform: 'uppercase', color: 'var(--muted)', marginBottom: 6 }}>
          Calibration Complete
        </div>
        <div style={{ fontSize: '1.4rem', fontWeight: 700, marginBottom: 4 }}>
          {session.tv} — {session.mode}
        </div>
        <div style={{ fontSize: '0.8rem', color: 'var(--muted)' }}>
          Session {sid} · {session.date ? new Date(session.date).toLocaleDateString() : ''}
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
          <div className="four-col" style={{ marginBottom: 16 }}>
            <StatCard value={pre.avg_de != null ? pre.avg_de.toFixed(2) : '—'} label="Pre-Cal Avg ΔE" color="red" />
            <StatCard value={post.avg_de != null ? post.avg_de.toFixed(2) : '—'} label="Post-Cal Avg ΔE" color="green" />
            <StatCard value={fmtNits(report.peak_luminance)} label="Peak Luminance" color="accent" />
            <StatCard
              value={report.improvement_pct != null ? report.improvement_pct.toFixed(1) + '%' : '—'}
              label="Improvement"
              color={report.improvement_pct != null && report.improvement_pct > 0 ? 'green' : ''}
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
        <Button onClick={downloadJson}>Download JSON</Button>
      </div>
    </>
  );
}
