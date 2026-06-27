import { dirClass, dirIcon } from '../utils/fmt';
import { Button } from './Button';

export function ActionPlan({ plan, menuPath, adbStatus, onApply }) {
  if (!plan?.length) return null;
  const ready = adbStatus?.connected && adbStatus?.cms_tool_deployed && !!onApply;
  const applicableSteps = ready ? plan.filter(s => s.direction !== 'hold' && s.amount) : [];
  const maxAmount = Math.max(...plan.map(s => s.amount || 0), 1);

  return (
    <div className="card mb-0">
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 16 }}>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: '1rem', fontWeight: 700, color: 'var(--ink)', lineHeight: 1.2 }}>
            What to Adjust
          </div>
          {menuPath && (
            <div style={{ fontSize: '0.73rem', color: 'var(--ink2)', fontFamily: 'var(--mono)', marginTop: 3 }}>
              {menuPath}
            </div>
          )}
        </div>
        {applicableSteps.length > 0 && (
          <Button
            small
            onClick={() => applicableSteps.forEach(s => onApply(s.control, s.amount, s.direction))}
          >
            Apply all
          </Button>
        )}
      </div>

      {plan.map((step, i) => {
        const isHold = step.direction === 'hold';
        const canApply = ready && step.amount && !isHold;
        const pct = step.amount ? Math.min((step.amount / maxAmount) * 100, 100) : 0;
        const barColor =
          step.direction === 'up' ? 'var(--green)' :
          step.direction === 'down' ? 'var(--red)' :
          'var(--yellow)';

        return (
          <div
            key={i}
            style={{
              display: 'flex',
              alignItems: 'flex-start',
              gap: 14,
              padding: '12px 0',
              borderBottom: i < plan.length - 1 ? '1px solid var(--line)' : 'none',
            }}
          >
            <div
              className={`action-icon ${dirClass(step.direction)}`}
              style={{ width: 38, height: 38, fontSize: '1.25rem', flexShrink: 0, marginTop: 1 }}
              aria-label={step.direction === 'up' ? 'increase' : step.direction === 'down' ? 'decrease' : step.direction === 'hold' ? 'hold' : 'ok'}
            >
              {dirIcon(step.direction)}
            </div>

            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                {step.control && (
                  <code style={{ fontFamily: 'var(--mono)', fontSize: '0.82rem', color: 'var(--accent)', fontWeight: 600 }}>
                    {step.control}
                  </code>
                )}
                {step.priority && (
                  <span className={`badge badge-${step.priority}`}>{step.priority}</span>
                )}
                {canApply && (
                  <div style={{ marginLeft: 'auto' }}>
                    <Button small onClick={() => onApply(step.control, step.amount, step.direction)}>
                      Apply
                    </Button>
                  </div>
                )}
              </div>

              <div style={{ fontSize: '0.84rem', fontWeight: 500, color: 'var(--ink)', marginBottom: 3 }}>
                {step.summary}
              </div>

              {step.reason && (
                <div className="action-reason" style={{ marginBottom: step.amount != null ? 8 : 0 }}>
                  {step.reason}
                </div>
              )}

              {step.amount != null && (
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <div style={{ flex: 1, height: 4, background: 'var(--panel3)', borderRadius: 2, overflow: 'hidden' }}>
                    <div style={{ width: `${pct}%`, height: '100%', background: barColor, borderRadius: 2 }} />
                  </div>
                  <span style={{ fontSize: '0.72rem', color: 'var(--ink2)', fontFamily: 'var(--mono)', minWidth: 20, textAlign: 'right' }}>
                    {step.amount}
                  </span>
                </div>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
