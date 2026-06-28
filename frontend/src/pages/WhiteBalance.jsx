import { Card, StatCard, Readout } from '../components/Card';
import { Button } from '../components/Button';
import { ActionPlan } from '../components/ActionPlan';
import { MeasurementTable } from '../components/MeasurementTable';
import { ZroInstructions } from '../components/ZroInstructions';
import { CIEScatter } from '../charts/CIEScatter';
import { fmtDe, fmtDateTime } from '../utils/fmt';
import { QualityGate } from '../components/QualityGate';
import { CollapsibleSection } from '../components/CollapsibleSection';
import { PredictedSettingsCard } from '../components/PredictedSettingsCard';

function wbSub(measurement) {
  if (!measurement) return null;
  return (
    <>
      {(measurement.x != null || measurement.y != null) && (
        <div>x={measurement.x?.toFixed(4)} y={measurement.y?.toFixed(4)}</div>
      )}
      {measurement.timestamp && <div>Measured {fmtDateTime(measurement.timestamp)}</div>}
    </>
  );
}

export function WhiteBalance({ session, onNext, onPrev, getPredictedSettings }) {
  const wd = session.wb_data || {};
  const plan   = wd.control_plan || [];
  const hints  = wd.hints || null;
  const target = wd.target_xy || [0.3127, 0.3290];
  const wbMeas = wd.measurements || [];

  return (
    <>
      <ZroInstructions instructions={session.zro_instructions} />
      <QualityGate gate={(session.quality_gates || {}).white_balance} />
      <PredictedSettingsCard getPredictedSettings={getPredictedSettings} phase="white_balance" />

      <div className="two-col" style={{ marginBottom: 16 }}>
        <Readout
          label="80% Gray (Gain)"
          value={<span dangerouslySetInnerHTML={{ __html: wd.gain_measurement ? fmtDe(wd.gain_measurement.delta_e) : '—' }} />}
          tone={wd.has_gain_measurement ? 'green' : null}
          sub={wbSub(wd.gain_measurement)}
        />
        <Readout
          label="30% Gray (Offset)"
          value={<span dangerouslySetInnerHTML={{ __html: wd.offset_measurement ? fmtDe(wd.offset_measurement.delta_e) : '—' }} />}
          tone={wd.has_offset_measurement ? 'green' : null}
          sub={wbSub(wd.offset_measurement)}
        />
      </div>

      <ActionPlan plan={plan} menuPath={wd.menu_path} />

      {hints && (
        <Card title="White Point Analysis">
          <div className="two-col">
            <div>
              <div className="text-sm muted" style={{ marginBottom: 6 }}>CIE xy shift from D65:</div>
              <div style={{ marginBottom: 4 }}>
                x: <strong className={hints.x.status === 'ok' ? 'green' : 'yellow'}>
                  {hints.x.value > 0 ? '+' : ''}{hints.x.value}
                </strong> — {hints.x.action}
              </div>
              <div>
                y: <strong className={hints.y.status === 'ok' ? 'green' : 'yellow'}>
                  {hints.y.value > 0 ? '+' : ''}{hints.y.value}
                </strong> — {hints.y.action}
              </div>
            </div>
            <div>
              <div className="text-sm muted" style={{ marginBottom: 6 }}>CIE xy plot:</div>
              <CIEScatter measurements={wbMeas} targetXy={target} />
            </div>
          </div>
        </Card>
      )}

      <Card title="Menu Path">
        <div className="text-sm accent">{wd.menu_path || ''}</div>
        {(wd.notes || []).length > 0 && (
          <ul style={{ marginTop: 10, paddingLeft: 16 }} className="text-sm muted">
            {wd.notes.map((n, i) => <li key={i} style={{ marginBottom: 5 }}>{n}</li>)}
          </ul>
        )}
      </Card>

      {wbMeas.length > 0 && (
        <CollapsibleSection title="WB Measurements" storageKey="wb-measurements" summary={`${wbMeas.length} reading${wbMeas.length !== 1 ? 's' : ''}`}>
          <MeasurementTable measurements={wbMeas} />
        </CollapsibleSection>
      )}

      <div className="btn-group">
        <Button onClick={onPrev}>← Back</Button>
        <Button primary onClick={onNext} disabled={!wd.ready_to_continue}>
          {wd.ready_to_continue ? 'Continue to Gamma →' : 'Need both 80% and 30% readings'}
        </Button>
      </div>
    </>
  );
}
