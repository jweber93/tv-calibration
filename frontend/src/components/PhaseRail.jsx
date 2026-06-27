export function PhaseRail({ session, onJump }) {
  if (!session?.steps) return null;
  const gates = session.quality_gates || {};
  return (
    <nav className="phase-rail">
      {session.steps.map((step, i) => {
        const done   = i < session.step_index;
        const active = i === session.step_index;
        const gate   = gates[step.id];
        const cls    = done ? 'done' : active ? 'active' : 'upcoming';
        const chip   = done ? '✓' : i + 1;
        const sub    = done && gate?.detail ? gate.detail : active ? 'adjusting…' : '';
        const showDot = active || (done && gate);
        const dotCls  = active ? 'active' : gate?.passed ? 'pass' : 'fail';
        return (
          <div
            key={step.id || i}
            className={`phase-row ${cls}`}
            onClick={done ? () => onJump(i) : undefined}
            title={done ? `Go back to ${step.label}` : undefined}
          >
            <span className="phase-chip">{chip}</span>
            <div className="phase-body">
              <div className="phase-label">{step.label}</div>
              {sub && <div className="phase-sub">{sub}</div>}
            </div>
            {showDot && <span className={`phase-dot ${dotCls}`} />}
          </div>
        );
      })}
    </nav>
  );
}
