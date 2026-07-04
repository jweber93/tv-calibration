import { useEffect, useState } from 'react';
import { Button } from './Button';

const REFRESH_MODE_OPTIONS = [
  { value: '', label: 'Auto (instrument default)' },
  { value: 'refresh', label: 'Refresh display (PWM backlight, CRT-like)' },
  { value: 'non_refresh', label: 'Non-refresh display (steady-state LCD/OLED)' },
];

const INTEGRATION_MODE_OPTIONS = [
  { value: '', label: 'Adaptive (spotread default)' },
  { value: 'non_adaptive', label: 'Non-adaptive (fixed integration time)' },
  { value: 'averaging', label: 'Averaging (where the instrument supports it)' },
];

export function ArgyllCard({ argyllPrefs, onScanInstruments, onSaveInstrument }) {
  const [port, setPort] = useState(argyllPrefs?.port || '');
  const [correctionPath, setCorrectionPath] = useState(argyllPrefs?.correction_path || '');
  const [refreshMode, setRefreshMode] = useState(argyllPrefs?.refresh_mode || '');
  const [refreshRateHz, setRefreshRateHz] = useState(argyllPrefs?.refresh_rate_hz ?? '');
  const [integrationMode, setIntegrationMode] = useState(argyllPrefs?.integration_mode || '');
  const [instruments, setInstruments] = useState([]);
  const [showSettings, setShowSettings] = useState(false);
  const [scanning, setScanning] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState('');

  useEffect(() => {
    setPort(argyllPrefs?.port || '');
    setCorrectionPath(argyllPrefs?.correction_path || '');
    setRefreshMode(argyllPrefs?.refresh_mode || '');
    setRefreshRateHz(argyllPrefs?.refresh_rate_hz ?? '');
    setIntegrationMode(argyllPrefs?.integration_mode || '');
  }, [argyllPrefs]);

  async function handleScan() {
    setScanning(true);
    setMsg('');
    try {
      const result = await onScanInstruments();
      setInstruments(result?.instruments || []);
      if (!result?.instruments?.length) {
        setMsg('No instruments detected — check the USB connection.');
      }
    } catch (e) {
      setMsg(`Error: ${e.message}`);
    } finally {
      setScanning(false);
    }
  }

  async function handleSave() {
    setBusy(true);
    setMsg('');
    try {
      await onSaveInstrument({
        port: port || null,
        instrument_name: instruments.find(i => String(i.index) === String(port))?.name || argyllPrefs?.instrument_name || null,
        correction_path: correctionPath || null,
        refresh_mode: refreshMode || null,
        refresh_rate_hz: refreshRateHz === '' ? null : Number(refreshRateHz),
        integration_mode: integrationMode || null,
      });
      setMsg('Instrument settings saved');
    } catch (e) {
      setMsg(`Error: ${e.message}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
        <span className="text-sm muted">
          {argyllPrefs?.instrument_name || argyllPrefs?.port
            ? `Meter: ${argyllPrefs?.instrument_name || `port ${argyllPrefs.port}`}`
            : 'No meter selected'}
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
          <div className="text-sm muted" style={{ marginBottom: 6 }}>Meter / port</div>
          <div className="bridge-url-row">
            <select value={port} onChange={e => setPort(e.target.value)}>
              <option value="">Auto-detect (spotread default)</option>
              {instruments.map(i => (
                <option key={i.index} value={i.index}>{i.name}</option>
              ))}
            </select>
            <Button small onClick={handleScan} disabled={scanning}>
              {scanning ? <span className="spinner" /> : 'Scan'}
            </Button>
          </div>

          <div className="text-sm muted" style={{ marginTop: 10 }}>
            CCMX/CCSS correction file (colorimeters on wide-gamut panels)
          </div>
          <div className="bridge-url-row">
            <input
              type="text"
              value={correctionPath}
              onChange={e => setCorrectionPath(e.target.value)}
              placeholder="C:\path\to\correction.ccmx"
            />
          </div>

          <div className="text-sm muted" style={{ marginTop: 10 }}>
            Display refresh mode (flicker-prone / PWM-backlit panels)
          </div>
          <div className="bridge-url-row">
            <select value={refreshMode} onChange={e => setRefreshMode(e.target.value)}>
              {REFRESH_MODE_OPTIONS.map(o => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
          </div>

          <div className="text-sm muted" style={{ marginTop: 10 }}>
            Refresh rate override (Hz) — leave blank to auto-detect
          </div>
          <div className="bridge-url-row">
            <input
              type="number"
              min={0}
              step="any"
              value={refreshRateHz}
              onChange={e => setRefreshRateHz(e.target.value)}
              placeholder="e.g. 120"
              style={{ width: 120 }}
            />
          </div>

          <div className="text-sm muted" style={{ marginTop: 10 }}>
            Integration time / averaging override
          </div>
          <div className="bridge-url-row" style={{ marginTop: 4 }}>
            <select value={integrationMode} onChange={e => setIntegrationMode(e.target.value)}>
              {INTEGRATION_MODE_OPTIONS.map(o => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
            <Button small onClick={handleSave} disabled={busy}>Save</Button>
          </div>
        </div>
      )}

      {msg && <div className="text-sm muted mt-2">{msg}</div>}
    </div>
  );
}
