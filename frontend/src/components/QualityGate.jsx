/**
 * QualityGate — inline quality gate status banner for a calibration step.
 *
 * Props:
 *   gate  — entry from session.quality_gates (passed, score, threshold, unit, label, detail)
 *   label — override display label (defaults to gate.label)
 */
export function QualityGate({ gate, label }) {
  if (!gate) return null;

  const passed = gate.passed;
  const color  = passed ? 'var(--green)' : 'var(--red)';
  const icon   = passed ? '✓' : '✗';
  const text   = label || gate.label;

  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 10,
      padding: '8px 12px',
      background: passed ? 'rgba(0,200,100,0.08)' : 'rgba(220,50,50,0.08)',
      border: `1px solid ${color}`,
      borderRadius: 6,
      marginBottom: 12,
    }}>
      <span style={{ color, fontWeight: 700, fontSize: '1rem', flexShrink: 0 }}>{icon}</span>
      <span className="text-sm">
        <strong>{text}</strong>
        {' — '}
        {gate.detail}
        {!passed && gate.score != null && (
          <span className="muted"> (need {gate.unit === '%' ? `≤${gate.threshold}${gate.unit}` : `< ${gate.threshold} ${gate.unit}`})</span>
        )}
      </span>
    </div>
  );
}
