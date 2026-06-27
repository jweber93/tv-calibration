import { useState, useEffect } from 'react';
import { Button } from './Button';

export function BridgeCard({ bridgeStatus, bridgeUrl, session, dogegenStatus, onSaveUrl, onMeasure }) {
  const [url, setUrl] = useState(bridgeUrl || '');
  const [showSettings, setShowSettings] = useState(!bridgeUrl);

  useEffect(() => { setUrl(bridgeUrl || ''); }, [bridgeUrl]);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState('');

  const ok = bridgeStatus?.ok;
  const dogegenSelected = session?.pattern_generator === 'dogegen';
  const dogegenReady = !!dogegenStatus?.ready;
  const dogegenBlocked = dogegenSelected && !!dogegenStatus?.running && !dogegenReady;

  async function handleMeasure() {
    setBusy(true);
    setMsg('');
    try {
      await onMeasure();
      setMsg('Measurement triggered');
    } catch (e) {
      setMsg(`Error: ${e.message}`);
    } finally {
      setBusy(false);
    }
  }

  async function handleSave() {
    await onSaveUrl(url);
    setShowSettings(false);
  }

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
        <span
          style={{
            width: 8, height: 8, borderRadius: '50%',
            background: ok ? 'var(--green)' : 'var(--ink2)',
            boxShadow: ok ? '0 0 6px var(--green)' : 'none',
            flexShrink: 0,
          }}
        />
        <span className="text-sm muted">
          {ok ? <span className="green">Bridge connected</span> : 'Bridge offline'}
        </span>
        <button
          className="btn btn-sm"
          style={{ marginLeft: 'auto' }}
          onClick={() => setShowSettings(s => !s)}
        >
          {showSettings ? 'Hide' : 'Settings'}
        </button>
      </div>

      {showSettings && (
        <div className="bridge-settings">
          <div className="text-sm muted">ZRO Bridge URL (e.g. http://localhost:7070)</div>
          <div className="bridge-url-row">
            <input
              type="text"
              value={url}
              onChange={e => setUrl(e.target.value)}
              placeholder="http://localhost:7070"
            />
            <Button small onClick={handleSave}>Save</Button>
          </div>
        </div>
      )}

      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 10 }}>
        <Button small disabled={!ok || busy || dogegenBlocked} onClick={handleMeasure}>
          {busy ? <span className="spinner" /> : 'Measure'}
        </Button>
        <span className="muted text-sm">
          {dogegenBlocked
            ? 'Waiting for Dogegen to finish warming up before measuring'
            : dogegenSelected && !dogegenStatus?.running
              ? 'Starts Dogegen if needed, then triggers one ZRO probe measurement'
              : 'Triggers one ZRO probe measurement'}
        </span>
      </div>
      {msg && <div className="text-sm muted mt-2">{msg}</div>}
    </div>
  );
}
