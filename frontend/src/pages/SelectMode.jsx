import { useState } from 'react';
import { Card } from '../components/Card';
import { Button } from '../components/Button';

const DEFAULT_MODES = [
  { key: 'SDR',          label: 'SDR',           desc: 'Rec.709 · BT.1886',              peak_nits: 120 },
  { key: 'HDR10',        label: 'HDR10',          desc: 'Rec.2020 · PQ (ST.2084)',         peak_nits: 1000 },
  { key: 'Dolby Vision', label: 'Dolby Vision',   desc: 'Rec.2020 · PQ · internal TM',    peak_nits: 1000 },
];

export function SelectMode({ session, onConfirmMode }) {
  const modes = session.modes || DEFAULT_MODES;
  const [selectedMode, setSelectedMode] = useState(session.mode || null);
  const [sdrPeakNits, setSdrPeakNits] = useState(120);

  const ambientGuide = session.sdr_ambient_guide || [];

  async function handleConfirm() {
    await onConfirmMode(selectedMode, sdrPeakNits);
  }

  return (
    <>
      <Card title="Select Calibration Mode">
        <div className="mode-grid">
          {modes.map(m => (
            <div
              key={m.key}
              className={`mode-card ${selectedMode === m.key ? 'selected' : ''}`}
              onClick={() => setSelectedMode(m.key)}
            >
              <div className="mode-card-label">{m.label}</div>
              <div className="mode-card-desc">{m.desc}</div>
              <div className="mode-nits">Target: {m.peak_nits} nits</div>
            </div>
          ))}
        </div>
      </Card>

      {selectedMode === 'SDR' && (
        <Card title="SDR Peak Luminance Target">
          <div className="text-sm muted" style={{ marginBottom: 10 }}>
            Choose based on your viewing environment:
          </div>
          {ambientGuide.length > 0 && (
            <select
              style={{ marginBottom: 10 }}
              onChange={e => setSdrPeakNits(Number(e.target.value))}
              value={sdrPeakNits}
            >
              {ambientGuide.map(a => (
                <option key={a.recommended} value={a.recommended}>
                  {a.label} (~{a.recommended} nits)
                </option>
              ))}
            </select>
          )}
          <div className="nits-row">
            <input
              type="range" min={80} max={250} step={5}
              value={sdrPeakNits}
              onChange={e => setSdrPeakNits(Number(e.target.value))}
            />
            <div className="nits-display">{sdrPeakNits} nits</div>
          </div>
        </Card>
      )}

      <Button primary onClick={handleConfirm} disabled={!selectedMode}>
        Confirm Mode &amp; Continue
      </Button>
    </>
  );
}
