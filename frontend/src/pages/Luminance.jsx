import { useState } from 'react';
import { Card, StatCard } from '../components/Card';
import { Button } from '../components/Button';
import { ActionPlan } from '../components/ActionPlan';
import { ZroInstructions } from '../components/ZroInstructions';
import { fmtDateTime, fmtNits } from '../utils/fmt';
import { QualityGate } from '../components/QualityGate';
import { AdbErrorBoundary } from '../components/AdbErrorBoundary';

const PICTURE_CONTROLS = [
  { key: 'Brightness', label: 'Brightness', hint: 'black level' },
  { key: 'Contrast',   label: 'Contrast',   hint: 'white level' },
];

function AdbPictureControls({ adbStatus, onAdbSetPicture, onAdbGetPicture, onDeploy, onRefresh }) {
  const [values, setValues]   = useState({ Brightness: 50, Contrast: 50 });
  const [pending, setPending] = useState({});
  const [errors, setErrors]   = useState({});

  const connected = adbStatus?.connected && adbStatus?.cms_tool_deployed;
  const dot = (ok) => (
    <span style={{ width: 8, height: 8, borderRadius: '50%', background: ok ? 'var(--green)' : 'var(--red)', display: 'inline-block', marginRight: 5 }} />
  );

  async function handleGet(control) {
    setPending(p => ({ ...p, [control]: 'get' }));
    setErrors(e => ({ ...e, [control]: null }));
    try {
      const res = await onAdbGetPicture(control);
      if (res?.value != null) setValues(v => ({ ...v, [control]: res.value }));
    } catch (err) {
      setErrors(e => ({ ...e, [control]: err.message }));
    } finally {
      setPending(p => ({ ...p, [control]: null }));
    }
  }

  async function handleSet(control) {
    setPending(p => ({ ...p, [control]: 'set' }));
    setErrors(e => ({ ...e, [control]: null }));
    try {
      await onAdbSetPicture(control, values[control]);
    } catch (err) {
      setErrors(e => ({ ...e, [control]: err.message }));
    } finally {
      setPending(p => ({ ...p, [control]: null }));
    }
  }

  return (
    <Card title="ADB Picture Controls">
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12, alignItems: 'center', marginBottom: 12 }}>
        <span>{dot(adbStatus?.connected)} <span className="text-sm">ADB connected</span></span>
        <span>{dot(adbStatus?.cms_tool_deployed)} <span className="text-sm">cms_tool.dex deployed</span></span>
        <Button small onClick={onRefresh} style={{ marginLeft: 'auto' }}>Refresh</Button>
        {adbStatus?.connected && !adbStatus?.cms_tool_deployed && (
          <Button small onClick={onDeploy}>Deploy cms_tool.dex</Button>
        )}
      </div>
      {!adbStatus?.connected && (
        <div className="muted text-sm" style={{ marginBottom: 8 }}>
          Connect the TV via ADB: <code>adb connect &lt;tv-ip&gt;</code>
        </div>
      )}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {PICTURE_CONTROLS.map(({ key, label, hint }) => (
          <div key={key} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span className="text-sm" style={{ width: 90, flexShrink: 0 }}>
              {label} <span className="muted">({hint})</span>
            </span>
            <input
              type="number"
              min={0}
              max={100}
              value={values[key]}
              onChange={e => setValues(v => ({ ...v, [key]: Number(e.target.value) }))}
              disabled={!connected}
              style={{ width: 60, padding: '4px 6px', background: 'var(--panel2)', border: '1px solid var(--line)', borderRadius: 4, color: 'var(--ink)', fontSize: '0.85rem' }}
            />
            <Button small disabled={!connected || pending[key] === 'get'} onClick={() => handleGet(key)}>
              {pending[key] === 'get' ? '…' : 'Get'}
            </Button>
            <Button small primary disabled={!connected || pending[key] === 'set'} onClick={() => handleSet(key)}>
              {pending[key] === 'set' ? '…' : 'Apply'}
            </Button>
            {errors[key] && <span className="text-sm" style={{ color: 'var(--red)' }}>{errors[key]}</span>}
          </div>
        ))}
      </div>
    </Card>
  );
}

export function Luminance({ session, adbStatus, onAdbSetPicture, onAdbGetPicture, onAdbDeploy, onRefreshAdb, onNext, onPrev }) {
  const ld = session.luminance_data || {};
  const plan     = ld.control_plan || [];
  const readings = ld.readings || [];
  const target   = ld.target_nits || 120;
  const current  = ld.current_nits || 0;
  const diff     = current > 0 ? (current - target) : null;

  const diffColor = diff === null ? '' : Math.abs(diff) < 5 ? 'green' : Math.abs(diff) < 20 ? 'yellow' : 'red';
  const currentColor = current > 0 ? (Math.abs(current - target) < 5 ? 'green' : 'yellow') : '';

  return (
    <>
      <ZroInstructions instructions={session.zro_instructions} />
      <QualityGate gate={(session.quality_gates || {}).luminance} />

      <div className="four-col" style={{ marginBottom: 16 }}>
        <StatCard value={target} label="Target nits" color="accent" />
        <StatCard value={fmtNits(current)} label="Current" color={currentColor} />
        <StatCard
          value={diff !== null ? (diff > 0 ? '+' : '') + diff.toFixed(1) : '—'}
          label="Δ from target"
          color={diffColor}
        />
        <StatCard value={readings.length} label="Readings" color="accent" />
      </div>

      <ActionPlan plan={plan} title="Backlight Control Plan" />

      {onAdbSetPicture && (
        <AdbErrorBoundary>
          <AdbPictureControls
            adbStatus={adbStatus}
            onAdbSetPicture={onAdbSetPicture}
            onAdbGetPicture={onAdbGetPicture}
            onDeploy={onAdbDeploy}
            onRefresh={onRefreshAdb}
          />
        </AdbErrorBoundary>
      )}

      {readings.length > 0 && (
        <Card title="Luminance History">
          <div className="import-log">
            {readings.map((r, i) => (
              <div className="import-log-entry" key={i}>
                <span>{r.Y?.toFixed(1)} nits</span>
                <span className="muted text-sm">{fmtDateTime(r.ts)}</span>
              </div>
            ))}
          </div>
        </Card>
      )}

      <div className="btn-group">
        <Button onClick={onPrev}>← Back</Button>
        <Button primary onClick={onNext} disabled={!((session.quality_gates || {}).luminance?.passed)}>
          {(session.quality_gates || {}).luminance?.passed
            ? 'Continue to White Balance →'
            : current > 0
              ? `Adjust to ${target} nits (within 10%)`
              : 'Waiting for luminance reading'}
        </Button>
      </div>
    </>
  );
}
