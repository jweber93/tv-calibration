/**
 * LlmInsightCard — displays the latest AI assistant insight received via SSE.
 *
 * Props:
 *   insight    — { text: string, phase: string, timestamp: string } | null
 *   streaming  — bool, true while the LLM is generating
 *   error      — string | null
 *   onDismiss  — () => void
 */
export function LlmInsightCard({ insight, streaming, error, onDismiss }) {
  if (!streaming && !insight && !error) return null;

  return (
    <div style={{
      margin: '0 0 16px 0',
      background: 'var(--surface2)',
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
          {error ? 'AI Assistant — Error' : 'AI Assistant'}
          {insight?.phase && !error && (
            <span style={{ marginLeft: 8, fontWeight: 400, color: 'var(--muted)', textTransform: 'none', letterSpacing: 0 }}>
              · {insight.phase}
            </span>
          )}
        </span>
        {streaming && (
          <span style={{ fontSize: '0.75rem', color: 'var(--muted)', display: 'flex', alignItems: 'center', gap: 5 }}>
            <span className="spinner" style={{ width: 10, height: 10, borderWidth: 1.5 }} />
            Thinking…
          </span>
        )}
        {!streaming && (insight || error) && (
          <button
            onClick={onDismiss}
            title="Dismiss"
            style={{ background: 'none', border: 'none', color: 'var(--muted)', cursor: 'pointer', fontSize: '1rem', lineHeight: 1, padding: '0 2px' }}
          >
            ×
          </button>
        )}
      </div>

      {/* Body */}
      {streaming && !insight && !error && (
        <div style={{ padding: '12px 14px', fontSize: '0.82rem', color: 'var(--muted)' }}>
          Analysing measurements…
        </div>
      )}
      {error && (
        <div style={{ padding: '10px 14px', fontSize: '0.82rem', color: 'var(--red)', fontFamily: 'monospace', whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
          {error}
        </div>
      )}
      {insight?.text && (
        <div style={{ padding: '12px 14px', fontSize: '0.84rem', lineHeight: 1.65, whiteSpace: 'pre-wrap', color: 'var(--text)' }}>
          {insight.text}
        </div>
      )}
    </div>
  );
}
