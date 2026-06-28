import { useState, useEffect } from 'react';
import { Card } from './Card';

export function PredictedSettingsCard({ getPredictedSettings, phase }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    Promise.resolve(getPredictedSettings ? getPredictedSettings(phase) : null)
      .then((res) => {
        if (!cancelled) setData(res);
      })
      .catch(() => {
        if (!cancelled) setData(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [getPredictedSettings, phase]);

  if (loading) return null;

  if (!data || !data.predicted) {
    const reason = data?.reason || 'Prediction unavailable.';
    return (
      <Card title="Predicted Starting Point" style={{ marginBottom: 16 }}>
        <div style={{ fontSize: '0.82rem', color: 'var(--ink2)', fontStyle: 'italic' }}>
          {reason}
        </div>
      </Card>
    );
  }

  const { predicted } = data;
  const { settings, confidence, source } = predicted;

  if (source === 'cold_start' || !settings || settings.length === 0) {
    return (
      <Card title="Predicted Starting Point" style={{ marginBottom: 16 }}>
        <div style={{ fontSize: '0.82rem', color: 'var(--ink2)', fontStyle: 'italic' }}>
          No prior data for this TV — measuring from defaults.
        </div>
      </Card>
    );
  }

  const confidencePct = Math.round((confidence ?? 0) * 100);

  return (
    <Card title="Predicted Starting Point" style={{ marginBottom: 16 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
        <span style={{
          fontSize: '0.7rem',
          fontWeight: 700,
          letterSpacing: '0.08em',
          textTransform: 'uppercase',
          color: 'var(--ink2)',
        }}>
          From history
        </span>
        <span style={{
          marginLeft: 'auto',
          fontSize: '0.72rem',
          fontWeight: 600,
          color: 'var(--accent)',
          padding: '2px 8px',
          border: '1px solid var(--accent)',
          borderRadius: 10,
        }}>
          {confidencePct}% confidence
        </span>
      </div>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.82rem' }}>
        <tbody>
          {settings.map((s) => (
            <tr key={`${s.menu}:${s.setting}`} style={{ borderBottom: '1px solid var(--border-subtle, rgba(255,255,255,0.06))' }}>
              <td style={{ padding: '7px 0', color: 'var(--ink2)', whiteSpace: 'nowrap' }}>
                {s.menu} › {s.setting}
              </td>
              <td style={{ padding: '7px 0', textAlign: 'right', fontWeight: 600, color: 'var(--ink)' }}>
                {s.value}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {settings.length > 0 && settings[0].reason && (
        <div style={{ fontSize: '0.78rem', color: 'var(--ink2)', marginTop: 8, lineHeight: 1.5 }}>
          {settings[0].reason}
        </div>
      )}
    </Card>
  );
}
