import { useEffect, useState } from 'react';
import { Button } from './Button';

export function DogegenCard({ dogegenStatus, session, onSaveConfig, onStart, onStop }) {
  const [path, setPath] = useState(dogegenStatus?.path || '');
  const [resolveHost, setResolveHost] = useState(dogegenStatus?.resolve_host || '');
  const [windowPct, setWindowPct] = useState(dogegenStatus?.window_pct || 10);
  const [maxcll, setMaxcll] = useState(dogegenStatus?.maxcll || 1000);
  const [showSettings, setShowSettings] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState('');

  useEffect(() => {
    setPath(dogegenStatus?.path || '');
    setResolveHost(dogegenStatus?.resolve_host || '');
    setWindowPct(dogegenStatus?.window_pct || 10);
    setMaxcll(dogegenStatus?.maxcll || 1000);
  }, [dogegenStatus]);

  const running = dogegenStatus?.running;
  const configured = dogegenStatus?.configured;

  async function handleSave() {
    setBusy(true);
    setMsg('');
    try {
      await onSaveConfig({
        path,
        resolve_host: resolveHost,
        window_pct: Number(windowPct),
        maxcll: Number(maxcll),
      });
      setMsg('Dogegen settings saved');
      setShowSettings(false);
    } catch (e) {
      setMsg(`Error: ${e.message}`);
    } finally {
      setBusy(false);
    }
  }

  async function handleStart() {
    setBusy(true);
    setMsg('');
    try {
      await onStart();
      setMsg(`Dogegen launched for ${session?.mode || 'the current session'}`);
    } catch (e) {
      setMsg(`Error: ${e.message}`);
    } finally {
      setBusy(false);
    }
  }

  async function handleStop() {
    setBusy(true);
    setMsg('');
    try {
      await onStop();
      setMsg('Dogegen stopped');
    } catch (e) {
      setMsg(`Error: ${e.message}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
        <span
          style={{
            width: 8, height: 8, borderRadius: '50%',
            background: running ? 'var(--green)' : 'var(--ink2)',
            boxShadow: running ? '0 0 6px var(--green)' : 'none',
            flexShrink: 0,
          }}
        />
        <span className="text-sm muted">
          {running
            ? <span className="green">Dogegen running</span>
            : configured
              ? 'Dogegen ready to launch'
              : 'Dogegen path not configured'}
        </span>
        <button
          className="btn btn-sm"
          style={{ marginLeft: 'auto' }}
          onClick={() => setShowSettings(s => !s)}
        >
          {showSettings ? 'Hide' : 'Settings'}
        </button>
      </div>

      <div className="text-sm muted" style={{ marginBottom: 10 }}>
        For HDR10 sessions the app launches Dogegen with `resolve_hdr` and `MaxCLL {maxcll}`. For SDR sessions it uses `resolve_sdr`. Both connect Dogegen to ColourSpace as the active TPG.
      </div>

      {showSettings && (
        <div className="bridge-settings">
          <div className="text-sm muted">Dogegen executable path</div>
          <div className="bridge-url-row">
            <input
              type="text"
              value={path}
              onChange={e => setPath(e.target.value)}
              placeholder="C:\Program Files\Dogegen\Dogegen.exe"
            />
          </div>
          <div className="text-sm muted" style={{ marginTop: 10 }}>Resolve host (optional)</div>
          <div className="bridge-url-row">
            <input
              type="text"
              value={resolveHost}
              onChange={e => setResolveHost(e.target.value)}
              placeholder="127.0.0.1"
            />
          </div>
          <div className="bridge-url-row" style={{ marginTop: 10 }}>
            <input
              type="number"
              min={1}
              max={100}
              value={windowPct}
              onChange={e => setWindowPct(Number(e.target.value))}
              style={{ width: 120 }}
            />
            <span className="text-sm muted">Window %</span>
            <input
              type="number"
              min={1}
              value={maxcll}
              onChange={e => setMaxcll(Number(e.target.value))}
              style={{ width: 140, marginLeft: 8 }}
            />
            <span className="text-sm muted">MaxCLL</span>
            <Button small onClick={handleSave} disabled={busy}>Save</Button>
          </div>
        </div>
      )}

      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 10, flexWrap: 'wrap' }}>
        <Button small primary disabled={busy} onClick={handleStart}>
          {busy ? <span className="spinner" /> : (running ? 'Dogegen Running' : 'Start Dogegen')}
        </Button>
        <Button small disabled={busy || !running} onClick={handleStop}>Stop</Button>
        {dogegenStatus?.path && <span className="muted text-sm">{dogegenStatus.path}</span>}
      </div>

      {(msg || dogegenStatus?.last_error) && (
        <div className="text-sm muted mt-2">{msg || `Error: ${dogegenStatus.last_error}`}</div>
      )}
    </div>
  );
}
