import { Card, StatCard } from '../components/Card';
import { Button } from '../components/Button';
import { ActionPlan } from '../components/ActionPlan';
import { MeasurementTable } from '../components/MeasurementTable';
import { UploadZone } from '../components/UploadZone';
import { WatchFolder } from '../components/WatchFolder';
import { BridgeCard } from '../components/BridgeCard';
import { ZroInstructions } from '../components/ZroInstructions';
import { CIEScatter } from '../charts/CIEScatter';
import { fmtDe, fmtDateTime } from '../utils/fmt';
import { QualityGate } from '../components/QualityGate';

function DeMeasCard({ label, measurement, hasFlag }) {
  return (
    <div className={`stat-card ${hasFlag ? 'green-top' : ''}`}>
      <div className="stat-value"
        dangerouslySetInnerHTML={{ __html: measurement ? fmtDe(measurement.delta_e) : '—' }}
      />
      <div className="stat-label">{label}</div>
      {measurement && (
        <div className="text-sm muted mt-2">
          x={measurement.x?.toFixed(4)} y={measurement.y?.toFixed(4)}
        </div>
      )}
      {measurement?.timestamp && (
        <div className="text-sm muted">Measured {fmtDateTime(measurement.timestamp)}</div>
      )}
    </div>
  );
}

export function WhiteBalance({ session, watchStatus, watchDefaultPath, bridgeStatus, bridgeUrl, dogegenStatus, onUpload, onStartWatch, onStopWatch, onSaveBridgeUrl, onMeasure, onNext, onPrev }) {
  const wd = session.wb_data || {};
  const plan   = wd.control_plan || [];
  const hints  = wd.hints || null;
  const target = wd.target_xy || [0.3127, 0.3290];
  const wbMeas = wd.measurements || [];

  return (
    <>
      <ZroInstructions instructions={session.zro_instructions} />
      <QualityGate gate={(session.quality_gates || {}).white_balance} />

      <div className="two-col" style={{ marginBottom: 16 }}>
        <DeMeasCard label="80% Gray (Gain)"   measurement={wd.gain_measurement}   hasFlag={wd.has_gain_measurement} />
        <DeMeasCard label="30% Gray (Offset)" measurement={wd.offset_measurement} hasFlag={wd.has_offset_measurement} />
      </div>

      <ActionPlan plan={plan} title={`WB Control Plan (${wd.menu_path || ''})`} />

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

      <Card title="ZRO Bridge" id="bridge-card">
        <BridgeCard bridgeStatus={bridgeStatus} bridgeUrl={bridgeUrl} session={session} dogegenStatus={dogegenStatus} onSaveUrl={onSaveBridgeUrl} onMeasure={onMeasure} />
      </Card>

      <div className="two-col">
        <Card title="Auto-Watch Path" id="watch-folder">
          <WatchFolder watchStatus={watchStatus} defaultPath={watchStatus.path || watchDefaultPath} onStart={onStartWatch} onStop={onStopWatch} />
        </Card>
        <Card title="Import ZRO CSV">
          <UploadZone onUpload={onUpload} />
        </Card>
      </div>

      <Card title="Menu Path">
        <div className="text-sm accent">{wd.menu_path || ''}</div>
        {(wd.notes || []).length > 0 && (
          <ul style={{ marginTop: 10, paddingLeft: 16 }} className="text-sm muted">
            {wd.notes.map((n, i) => <li key={i} style={{ marginBottom: 5 }}>{n}</li>)}
          </ul>
        )}
      </Card>

      {wbMeas.length > 0 && (
        <Card title="WB Measurements">
          <MeasurementTable measurements={wbMeas} />
        </Card>
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
