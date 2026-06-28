import { Card } from '../components/Card';
import { Button } from '../components/Button';
import { ActionPlan } from '../components/ActionPlan';
import { MeasurementTable } from '../components/MeasurementTable';
import { ZroInstructions } from '../components/ZroInstructions';
import { fmtDe } from '../utils/fmt';
import { QualityGate } from '../components/QualityGate';
import { AdbErrorBoundary } from '../components/AdbErrorBoundary';
import { CollapsibleSection } from '../components/CollapsibleSection';

export function ColorTuner({ session, adbStatus, onAdbApply, onAdbReset, onAdbDeploy, onNext, onPrev }) {
  const cd = session.cms_data || {};
  const plan = cd.control_plan || [];
  const meas = cd.measurements || [];
  const lastMeasLabel  = meas.length ? meas[meas.length - 1].label : '';
  const activeColour   = lastMeasLabel.replace(/ 100%$/, '');

  return (
    <>
      <ZroInstructions instructions={session.zro_instructions} />
      <QualityGate gate={(session.quality_gates || {}).color_tuner} />

      <Card title="Color Status">
        <div className="three-col">
          {(cd.colors || []).map((c, i) => {
            const [r, g, b] = c.rgb;
            const m = meas.find(x => x.label === c.name || x.label === c.name + ' 100%');
            return (
              <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: 8, background: 'var(--panel2)', borderRadius: 'var(--radius)', border: '1px solid var(--line)' }}>
                <div style={{ width: 18, height: 18, borderRadius: 3, background: `rgb(${r},${g},${b})`, flexShrink: 0 }} />
                <div style={{ flex: 1, fontSize: '0.8rem' }}>{c.name}</div>
                {m
                  ? <span dangerouslySetInnerHTML={{ __html: fmtDe(m.delta_e) }} />
                  : <span className="muted text-sm">—</span>
                }
              </div>
            );
          })}
        </div>
      </Card>

      {cd.latest_hint && (
        <ActionPlan
          plan={plan}
          menuPath={cd.menu_path}
          adbStatus={adbStatus}
          onApply={(control, amount, direction) => {
            const delta = direction === 'up' ? amount : -amount;
            onAdbApply(activeColour, control, delta);
          }}
        />
      )}

      {meas.length > 0 && (
        <CollapsibleSection title="CMS Measurements" storageKey="cms-measurements" summary={`${meas.length} reading${meas.length !== 1 ? 's' : ''}`}>
          <MeasurementTable measurements={meas} />
        </CollapsibleSection>
      )}

      <AdbErrorBoundary>
        <Card title="Programmatic Control (ADB)">
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center' }}>
            {adbStatus?.connected && !adbStatus?.cms_tool_deployed && (
              <Button small onClick={onAdbDeploy}>Deploy cms_tool.dex</Button>
            )}
            <Button small disabled={!(adbStatus?.connected && adbStatus?.cms_tool_deployed)} onClick={onAdbReset}>
              Reset All CMS to 0
            </Button>
            <span className="muted text-sm">Calls setColorTunerReset() on the TV</span>
          </div>
        </Card>
      </AdbErrorBoundary>

      <div className="btn-group">
        <Button onClick={onPrev}>← Back</Button>
        <Button primary onClick={onNext}>Continue to Post-Cal Grayscale →</Button>
      </div>
    </>
  );
}
