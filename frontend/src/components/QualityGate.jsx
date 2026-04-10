import { useEffect, useRef, useState } from 'react';

/**
 * QualityGate — inline quality gate status banner for a calibration step.
 *
 * Props:
 *   gate  — entry from session.quality_gates (passed, score, threshold, unit, label, detail)
 *   label — override display label (defaults to gate.label)
 */
export function QualityGate({ gate, label }) {
  const hasAnimated = useRef(false);
  const [animClass, setAnimClass] = useState('');

  useEffect(() => {
    if (gate?.passed && !hasAnimated.current) {
      hasAnimated.current = true;
      setAnimClass('qg-enter');
    }
    if (!gate?.passed) {
      hasAnimated.current = false;
      setAnimClass('');
    }
  }, [gate?.passed]);

  if (!gate) return null;

  const passed = gate.passed;
  const color  = passed ? 'var(--green)' : 'var(--red)';
  const icon   = passed ? '✓' : '✗';
  const text   = label || gate.label;

  return (
    <div
      className={animClass}
      style={{
        display: 'flex', alignItems: 'center', gap: 10,
        padding: passed ? '10px 14px' : '8px 12px',
        background: passed ? 'rgba(34,201,135,0.1)' : 'rgba(220,50,50,0.08)',
        border: `1px solid ${color}`,
        borderLeft: passed ? `3px solid ${color}` : `1px solid ${color}`,
        borderRadius: 6,
        marginBottom: 12,
      }}
    >
      <span style={{ color, fontWeight: 700, fontSize: passed ? '1.1rem' : '1rem', flexShrink: 0 }}>{icon}</span>
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
