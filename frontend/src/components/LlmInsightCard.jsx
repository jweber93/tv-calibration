/**
 * LlmInsightCard — displays the latest AI assistant insight received via SSE.
 *
 * Props:
 *   insight    — { text: string, plan: AdjustmentPlan|null, phase: string, timestamp: string } | null
 *   streaming  — bool, true while the LLM is generating
 *   error      — string | null
 *   onDismiss  — () => void
 *
 * AdjustmentPlan shape (from calcore/llm.py):
 *   { adjustments: [{menu, setting, from, to, scope, reason}], next_step, confidence }
 */

const NEXT_STEP_LABEL = {
  rerun_grayscale: 'Re-run Grayscale',
  proceed_cms:     'Proceed to CMS',
  rerun_wb:        'Re-run White Balance',
  verify:          'Verify / Done',
};

const SCOPE_BADGE = {
  global: { label: 'Global', color: 'var(--accent)' },
  local:  { label: 'Local',  color: 'var(--yellow, #f0a500)' },
};

function AdjustmentRow({ adj }) {
  const scope = SCOPE_BADGE[adj.scope] || { label: adj.scope || '—', color: 'var(--ink2)' };
  const fromStr = adj.from != null ? String(adj.from) : '?';
  const toStr   = adj.to   != null ? String(adj.to)   : '?';
  return (
    <div style={{ padding: '8px 0', borderBottom: '1px solid var(--border-subtle, rgba(255,255,255,0.06))' }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, flexWrap: 'wrap' }}>
        <span style={{ fontWeight: 600, fontSize: '0.83rem', color: 'var(--ink)' }}>
          {adj.setting}
        </span>
        <span style={{ fontSize: '0.75rem', color: 'var(--ink2)' }}>
          {adj.menu}
        </span>
        <span style={{
          marginLeft: 'auto',
          fontSize: '0.72rem',
          fontWeight: 700,
          color: scope.color,
          textTransform: 'uppercase',
          letterSpacing: '0.06em',
        }}>
          {scope.label}
        </span>
      </div>
      <div style={{ fontSize: '0.82rem', color: 'var(--accent)', marginTop: 2 }}>
        {fromStr} → {toStr}
      </div>
      {adj.reason && (
        <div style={{ fontSize: '0.79rem', color: 'var(--ink2)', marginTop: 3, lineHeight: 1.5 }}>
          {adj.reason}
        </div>
      )}
    </div>
  );
}

function AdjustmentPlanView({ plan }) {
  const nextLabel = NEXT_STEP_LABEL[plan.next_step] || plan.next_step;
  const confidencePct = Math.round((plan.confidence ?? 0) * 100);
  return (
    <div style={{ padding: '10px 14px' }}>
      {plan.adjustments.length === 0 ? (
        <div style={{ fontSize: '0.82rem', color: 'var(--ink2)', fontStyle: 'italic' }}>
          No adjustments needed — data insufficient or sweep complete.
        </div>
      ) : (
        <>
          <div style={{ fontSize: '0.72rem', fontWeight: 700, letterSpacing: '0.08em', textTransform: 'uppercase', color: 'var(--ink2)', marginBottom: 6 }}>
            Required Adjustments
          </div>
          {plan.adjustments.map((adj, i) => (
            <AdjustmentRow key={i} adj={adj} />
          ))}
        </>
      )}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginTop: 10 }}>
        <div style={{
          flex: 1,
          background: 'var(--panel3, rgba(255,255,255,0.05))',
          borderRadius: 6,
          padding: '5px 10px',
          fontSize: '0.8rem',
        }}>
          <span style={{ color: 'var(--ink2)', marginRight: 6 }}>Next Step:</span>
          <span style={{ fontWeight: 600, color: 'var(--ink)' }}>{nextLabel}</span>
        </div>
        <div style={{ fontSize: '0.75rem', color: 'var(--ink2)', whiteSpace: 'nowrap' }}>
          Confidence {confidencePct}%
        </div>
      </div>
    </div>
  );
}

export function LlmInsightCard({ insight, streaming, error, onDismiss }) {
  if (!streaming && !insight && !error) return null;

  const hasPlan = insight?.plan && Array.isArray(insight.plan.adjustments);

  return (
    <div style={{
      margin: '0 0 16px 0',
      background: 'var(--panel2)',
      border: `1px solid ${error ? 'var(--red)' : 'var(--accent)'}`,
      borderRadius: 'var(--radius-lg)',
      overflow: 'hidden',
    }}>
      {/* Header bar */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        padding: '8px 12px',
        background: error ? 'rgba(231,76,60,0.08)' : 'var(--accent-dim)',
        borderBottom: `1px solid ${error ? 'rgba(231,76,60,0.2)' : 'rgba(0,180,216,0.2)'}`,
      }}>
        <span style={{ fontSize: '0.72rem', fontWeight: 700, letterSpacing: '0.1em', textTransform: 'uppercase', color: error ? 'var(--red)' : 'var(--accent)', flexGrow: 1 }}>
          {error ? 'AI Assistant — Error' : hasPlan ? 'Adjustment Plan' : 'AI Assistant'}
          {insight?.phase && !error && (
            <span style={{ marginLeft: 8, fontWeight: 400, color: 'var(--ink2)', textTransform: 'none', letterSpacing: 0 }}>
              · {insight.phase}
            </span>
          )}
        </span>
        {streaming && (
          <span style={{ fontSize: '0.75rem', color: 'var(--ink2)', display: 'flex', alignItems: 'center', gap: 5 }}>
            <span className="spinner" style={{ width: 10, height: 10, borderWidth: 1.5 }} />
            Thinking…
          </span>
        )}
        {!streaming && (insight || error) && (
          <button
            onClick={onDismiss}
            title="Dismiss"
            style={{ background: 'none', border: 'none', color: 'var(--ink2)', cursor: 'pointer', fontSize: '1rem', lineHeight: 1, padding: '0 2px' }}
          >
            ×
          </button>
        )}
      </div>

      {/* Body */}
      {streaming && !insight && !error && (
        <div style={{ padding: '12px 14px', fontSize: '0.82rem', color: 'var(--ink2)' }}>
          Analysing measurements…
        </div>
      )}
      {error && (
        <div style={{ padding: '10px 14px', fontSize: '0.82rem', color: 'var(--red)', fontFamily: 'monospace', whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
          {error}
        </div>
      )}
      {hasPlan && (
        <AdjustmentPlanView plan={insight.plan} />
      )}
      {!hasPlan && insight?.text && (
        <div style={{ padding: '12px 14px', fontSize: '0.84rem', lineHeight: 1.65, whiteSpace: 'pre-wrap', color: 'var(--ink)' }}>
          {insight.text}
        </div>
      )}
    </div>
  );
}
