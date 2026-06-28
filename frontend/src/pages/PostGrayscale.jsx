import { Card } from '../components/Card';
import { Button } from '../components/Button';
import { MeasurementTable } from '../components/MeasurementTable';
import { ZroInstructions } from '../components/ZroInstructions';
import { DeChart } from '../charts/DeChart';
import { QualityGate } from '../components/QualityGate';
import { CollapsibleSection } from '../components/CollapsibleSection';

export function PostGrayscale({ session, onNext, onPrev }) {
  const gd = session.grayscale_data || {};
  const done  = gd.done || 0;
  const total = gd.total || 11;
  const pct   = Math.round(done / total * 100);
  const meas  = gd.measurements || [];

  return (
    <>
      <ZroInstructions instructions={session.zro_instructions} />
      <QualityGate gate={(session.quality_gates || {}).post_grayscale} />

      <Card title="Post-Calibration Grayscale Progress">
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12 }}>
          <div style={{ flex: 1, background: 'var(--panel2)', height: 8, borderRadius: 4, overflow: 'hidden' }}>
            <div style={{
              height: '100%', width: `${pct}%`,
              background: pct === 100 ? 'var(--green)' : 'var(--accent)',
              borderRadius: 4, transition: 'width .3s',
            }} />
          </div>
          <div className="text-sm muted">{done}/{total}</div>
        </div>
        {meas.length > 0 && <DeChart measurements={meas} label="Post-Cal ΔE" />}
        {meas.length > 0 && (
          <div className="mt-3">
            <CollapsibleSection title="Measurement Data" storageKey="post-grayscale-measurements" summary={`${meas.length} reading${meas.length !== 1 ? 's' : ''}`}>
              <MeasurementTable measurements={meas} />
            </CollapsibleSection>
          </div>
        )}
      </Card>

      <div className="btn-group">
        <Button onClick={onPrev}>← Back</Button>
        <Button primary onClick={onNext} disabled={done < total}>
          {done < total ? `Need ${total - done} more readings` : 'Generate Report →'}
        </Button>
      </div>
    </>
  );
}
