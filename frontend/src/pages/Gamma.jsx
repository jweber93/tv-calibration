import { Card } from '../components/Card';
import { Button } from '../components/Button';
import { ActionPlan } from '../components/ActionPlan';
import { MeasurementTable } from '../components/MeasurementTable';
import { ZroInstructions } from '../components/ZroInstructions';
import { GammaChart } from '../charts/GammaChart';
import { QualityGate } from '../components/QualityGate';
import { CollapsibleSection } from '../components/CollapsibleSection';

export function Gamma({ session, onSetGammaWorkflow, onNext, onPrev }) {
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

      <ActionPlan plan={plan} menuPath={gd.menu_path} />

      {gd.gamma_note && (
        <Card title="Note">
          <div className="text-sm muted">{gd.gamma_note}</div>
        </Card>
      )}

      {meas.length > 0 && (
        <Card title="Gamma Tracking">
          <GammaChart measurements={meas} />
          <div className="mt-3">
            <CollapsibleSection title="Measurement Data" storageKey="gamma-measurements" summary={`${meas.length} point${meas.length !== 1 ? 's' : ''}`}>
              <MeasurementTable measurements={meas} includeGamma />
            </CollapsibleSection>
          </div>
        </Card>
      )}

      {(gd.recommendations || []).length > 0 && (
        <Card title="Recommendations">
          <ul style={{ paddingLeft: 16 }} className="text-sm muted">
            {gd.recommendations.map((r, i) => <li key={i} style={{ marginBottom: 5 }}>{r}</li>)}
          </ul>
        </Card>
      )}

      <div className="btn-group">
        <Button onClick={onPrev}>← Back</Button>
        <Button primary onClick={onNext} disabled={!gd.ready_to_continue}>
          {gd.ready_to_continue ? 'Continue to Color Tuner →' : `Need ${(gd.workflow_label || 'selected').toLowerCase()} gamma pass`}
        </Button>
      </div>
    </>
  );
}
