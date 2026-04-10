import { useState } from 'react';
import { Card } from '../components/Card';
import { Button } from '../components/Button';

export function Setup({ profiles, onCreateSession }) {
  const [selectedTv, setSelectedTv] = useState(profiles[0]?.key || '');

  async function handleStart() {
    await onCreateSession(selectedTv);
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
      <Card title="Start Session">
        <div className="text-sm muted" style={{ marginBottom: 14 }}>
          Creates a new calibration session. ColourSpace ZRO / Zero measurements are imported via file watch or manual CSV upload.
        </div>
        <Button primary onClick={handleStart} disabled={!selectedTv}>
          Start New Session
        </Button>
      </Card>
    </>
  );
}
