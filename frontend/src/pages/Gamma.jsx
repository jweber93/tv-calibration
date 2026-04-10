import { Card } from '../components/Card';
import { Button } from '../components/Button';
import { ActionPlan } from '../components/ActionPlan';
import { MeasurementTable } from '../components/MeasurementTable';
import { UploadZone } from '../components/UploadZone';
import { WatchFolder } from '../components/WatchFolder';
import { BridgeCard } from '../components/BridgeCard';
import { ZroInstructions } from '../components/ZroInstructions';
import { GammaChart } from '../charts/GammaChart';
import { QualityGate } from '../components/QualityGate';

export function Gamma({ session, watchStatus, watchDefaultPath, bridgeStatus, bridgeUrl, onUpload, onStartWatch, onStopWatch, onSaveBridgeUrl, onMeasure, onSetGammaWorkflow, onNext, onPrev }) {
  const gd = session.gamma_data || {};
  const plan      = gd.control_plan || [];
  const meas      = gd.measurements || [];
  const workflows = gd.available_workflows || [];

  return (
    <>
      <ZroInstructions instructions={session.zro_instructions} />
      <QualityGate gate={(session.quality_gates || {}).gamma} />

      {workflows.length > 1 && (
        <Card title="Gamma Workflow">
          <div className="btn-group">
            {workflows.map(w => (
              <Button
                key={w.id}
                primary={gd.workflow === w.id}
                onClick={() => onSetGammaWorkflow(w.id)}
              >
                {w.label}
              </Button>
            ))}
          </div>
        </Card>
      )}

      <ActionPlan plan={plan} title={`Gamma Control Plan (${gd.menu_path || ''})`} />

      {gd.gamma_note && (
        <Card title="Note">
          <div className="text-sm muted">{gd.gamma_note}</div>
        </Card>
      )}

      {meas.length > 0 && (
        <Card title="Gamma Tracking">
          <GammaChart measurements={meas} />
          <div className="mt-3"><MeasurementTable measurements={meas} includeGamma /></div>
        </Card>
      )}

      {(gd.recommendations || []).length > 0 && (
        <Card title="Recommendations">
          <ul style={{ paddingLeft: 16 }} className="text-sm muted">
            {gd.recommendations.map((r, i) => <li key={i} style={{ marginBottom: 5 }}>{r}</li>)}
          </ul>
        </Card>
      )}

      <Card title="ZRO Bridge">
        <BridgeCard bridgeStatus={bridgeStatus} bridgeUrl={bridgeUrl} session={session} dogegenStatus={dogegenStatus} onSaveUrl={onSaveBridgeUrl} onMeasure={onMeasure} />
      </Card>

      <div className="two-col">
        <Card title="Auto-Watch Path">
          <WatchFolder watchStatus={watchStatus} defaultPath={watchStatus.path || watchDefaultPath} onStart={onStartWatch} onStop={onStopWatch} />
        </Card>
        <Card title="Import ZRO CSV">
          <UploadZone onUpload={onUpload} />
        </Card>
      </div>

      <div className="btn-group">
        <Button onClick={onPrev}>← Back</Button>
        <Button primary onClick={onNext} disabled={!gd.ready_to_continue}>
          {gd.ready_to_continue ? 'Continue to Color Tuner →' : `Need ${(gd.workflow_label || 'selected').toLowerCase()} gamma pass`}
        </Button>
      </div>
    </>
  );
}
