import { useState } from 'react';
import { Card } from '../components/Card';
import { Button } from '../components/Button';

const DEFAULT_MODES = [
  { key: 'SDR',          label: 'SDR',          desc: 'Rec.709 · BT.1886',           peak_nits: 120  },
  { key: 'HDR10',        label: 'HDR10',         desc: 'Rec.2020 · PQ (ST.2084)',      peak_nits: 1000 },
  { key: 'Dolby Vision', label: 'Dolby Vision',  desc: 'Rec.2020 · PQ · internal TM', peak_nits: 1000 },
];

export function Setup({ profiles, onCreateSession, onConfirmMode }) {
  const [selectedTv,   setSelectedTv]   = useState(profiles[0]?.key || '');
  const [selectedMode, setSelectedMode] = useState(null);
  const [sdrPeakNits,  setSdrPeakNits]  = useState(120);
  const [busy,         setBusy]         = useState(false);

  const modes = DEFAULT_MODES;

  async function handleBegin() {
    if (!selectedTv || !selectedMode) return;
    setBusy(true);
    try {
      await onCreateSession(selectedTv);
      await onConfirmMode(selectedMode, sdrPeakNits);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <Card title="Select TV Model">
        <div className="tv-grid">
          {profiles.map(p => (
            <div
              key={p.key}
              className={`tv-card ${selectedTv === p.key ? 'selected' : ''}`}
              onClick={() => setSelectedTv(p.key)}
            >
              <strong>{p.name}</strong>
            </div>
          ))}
        </div>
      </Card>

      <Card title="Signal Mode">
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
        <Card title="SDR Peak Luminance">
          <div className="text-sm muted" style={{ marginBottom: 10 }}>
            Set peak brightness based on your viewing environment:
          </div>
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

      <Button
        primary
        onClick={handleBegin}
        disabled={!selectedTv || !selectedMode || busy}
      >
        {busy ? 'Starting…' : 'Begin Calibration'}
      </Button>
    </>
  );
}
