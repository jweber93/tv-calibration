import { Card } from '../components/Card';
import { Button } from '../components/Button';
import { MeasurementTable } from '../components/MeasurementTable';
import { ZroInstructions } from '../components/ZroInstructions';
import { fmtDe, dirIcon } from '../utils/fmt';
import { QualityGate } from '../components/QualityGate';
import { AdbErrorBoundary } from '../components/AdbErrorBoundary';

function AdbStatusPanel({ adbStatus, onDeploy, onRefresh }) {
  if (!adbStatus) {
    return <div className="muted text-sm">ADB status unavailable — is <code>adb</code> on PATH?</div>;
  }
  const dot = (ok) => (
    <span style={{ width: 8, height: 8, borderRadius: '50%', background: ok ? 'var(--green)' : 'var(--red)', display: 'inline-block', marginRight: 5 }} />
  );
  const deviceList = adbStatus.devices?.length
    ? adbStatus.devices.map((d, i) => <code key={i} style={{ fontSize: '0.75rem' }}>{d}</code>)
    : <span className="muted text-sm">none</span>;

  return (
    <div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12, alignItems: 'center' }}>
        <span>{dot(adbStatus.connected)} <span className="text-sm">ADB connected</span></span>
        <span>{dot(adbStatus.cms_tool_deployed)} <span className="text-sm">cms_tool.dex deployed</span></span>
        <span className="muted text-sm" style={{ marginLeft: 4 }}>Device: {deviceList}</span>
        <Button small onClick={onRefresh} style={{ marginLeft: 'auto' }}>Refresh</Button>
        {adbStatus.connected && !adbStatus.cms_tool_deployed && (
          <Button small onClick={onDeploy}>Deploy cms_tool.dex</Button>
        )}
      </div>
      {!adbStatus.connected && (
        <div className="muted text-sm mt-2">
          Connect the TV via ADB: <code>adb connect &lt;tv-ip&gt;</code>
        </div>
      )}
    </div>
  );
}

function ActionPlanWithAdb({ plan, title, adbStatus, onAdbApply }) {
  if (!plan?.length) return null;
  const ready = adbStatus?.connected && adbStatus?.cms_tool_deployed;

  return (
    <Card title={title}>
      {plan.map((step, i) => {
        const isHold = step.direction === 'hold';
        return (
          <div key={i} style={{ display: 'flex', alignItems: 'flex-start', gap: 10, padding: '8px 0', borderBottom: '1px solid var(--line)' }}>
            <span style={{ fontSize: '1rem', width: 20, textAlign: 'center', flexShrink: 0 }}>
              {dirIcon(step.direction)}
            </span>
            <div style={{ flex: 1 }}>
              <div style={{ fontWeight: 600, fontSize: '0.85rem' }}>
                {step.summary}
                {!isHold && ready && step.amount && (
                  <Button small onClick={() => onAdbApply(step.control, step.amount, step.direction)} style={{ marginLeft: 8 }}>
                    Apply
                  </Button>
                )}
              </div>
              <div className="muted text-sm">{step.reason || ''}</div>
            </div>
          </div>
        );
      })}
    </Card>
  );
}

export function ColorTuner({ session, adbStatus, onAdbApply, onAdbReset, onAdbDeploy, onRefreshAdb, onNext, onPrev }) {
  const cd = session.cms_data || {};
  const plan = cd.control_plan || [];
  const meas = cd.measurements || [];
  const lastMeasLabel  = meas.length ? meas[meas.length - 1].label : '';
  const activeColour   = lastMeasLabel.replace(/ 100%$/, '');

  const ready = adbStatus?.connected && adbStatus?.cms_tool_deployed;

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
        <ActionPlanWithAdb
          plan={plan}
          title={`${lastMeasLabel || 'Latest'} — Control Plan (${cd.menu_path || ''})`}
          adbStatus={adbStatus}
          onAdbApply={(control, amount, direction) => {
            const delta = direction === 'up' ? amount : -amount;
            onAdbApply(activeColour, control, delta);
          }}
        />
      )}

      {meas.length > 0 && (
        <Card title="CMS Measurements">
          <MeasurementTable measurements={meas} />
        </Card>
      )}

      <AdbErrorBoundary>
        <Card title="Programmatic Control (ADB)">
          <AdbStatusPanel adbStatus={adbStatus} onDeploy={onAdbDeploy} onRefresh={onRefreshAdb} />
          <div style={{ marginTop: 10 }}>
            <Button small disabled={!ready} onClick={onAdbReset}>
              Reset All CMS to 0
            </Button>
            <span className="muted text-sm" style={{ marginLeft: 8 }}>Calls setColorTunerReset() on the TV</span>
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
