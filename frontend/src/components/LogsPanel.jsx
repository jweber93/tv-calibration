import { useState, useEffect, useRef } from 'react';

const LEVEL_COLOR = {
  DEBUG:    'var(--muted)',
  INFO:     'var(--text)',
  WARNING:  '#e6a817',
  ERROR:    'var(--red)',
  CRITICAL: 'var(--red)',
};

function levelOf(line) {
  if (/\bCRITICAL\b/.test(line)) return 'CRITICAL';
  if (/\bERROR\b/.test(line))    return 'ERROR';
  if (/\bWARNING\b/.test(line))  return 'WARNING';
  if (/\bDEBUG\b/.test(line))    return 'DEBUG';
  return 'INFO';
}

export function LogsPanel() {
  const [open, setOpen] = useState(false);
  const [lines, setLines] = useState([]);
  const [connected, setConnected] = useState(false);
  const esRef = useRef(null);
  const bottomRef = useRef(null);
  const MAX_LINES = 300;

  useEffect(() => {
    const es = new EventSource('/api/logs/stream');
    esRef.current = es;
    es.onopen = () => setConnected(true);
    es.onmessage = e => {
      try {
        const line = JSON.parse(e.data);
        setLines(prev => {
          const next = [...prev, line];
          return next.length > MAX_LINES ? next.slice(next.length - MAX_LINES) : next;
        });
      } catch { /* ignore */ }
    };
    es.onerror = () => setConnected(false);
    return () => es.close();
  }, []);

  // Auto-scroll when panel is open
  useEffect(() => {
    if (open) bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [lines, open]);

  const errorCount = lines.filter(l => /\b(ERROR|CRITICAL)\b/.test(l)).length;

  return (
    <div style={{
      position: 'fixed', bottom: 0, left: 0, right: 0, zIndex: 1000,
      fontFamily: 'monospace', fontSize: '0.75rem',
      background: 'var(--surface)', borderTop: '1px solid var(--border)',
      boxShadow: open ? '0 -4px 24px rgba(0,0,0,0.4)' : 'none',
    }}>
      {/* Toggle bar */}
      <div
        onClick={() => setOpen(o => !o)}
        style={{
          display: 'flex', alignItems: 'center', gap: 10, padding: '4px 14px',
          cursor: 'pointer', userSelect: 'none',
          background: 'var(--surface2)',
        }}
      >
        <span style={{ color: connected ? 'var(--green)' : 'var(--muted)', fontSize: '0.65rem' }}>●</span>
        <span style={{ color: 'var(--muted)', fontWeight: 600, letterSpacing: '0.05em' }}>SERVER LOGS</span>
        {errorCount > 0 && (
          <span style={{ background: 'var(--red)', color: '#fff', borderRadius: 3, padding: '0 5px', fontSize: '0.65rem' }}>
            {errorCount} error{errorCount !== 1 ? 's' : ''}
          </span>
        )}
        <span style={{ marginLeft: 'auto', color: 'var(--muted)', fontSize: '0.65rem' }}>
          {open ? '▼' : '▲'}
        </span>
      </div>

      {/* Log lines */}
      {open && (
        <div style={{ height: 220, overflowY: 'auto', padding: '6px 14px' }}>
          {lines.length === 0 ? (
            <div style={{ color: 'var(--muted)', padding: '20px 0', textAlign: 'center' }}>No log lines yet.</div>
          ) : (
            lines.map((line, i) => (
              <div key={i} style={{ color: LEVEL_COLOR[levelOf(line)], whiteSpace: 'pre-wrap', wordBreak: 'break-all', lineHeight: 1.5 }}>
                {line}
              </div>
            ))
          )}
          <div ref={bottomRef} />
        </div>
      )}
    </div>
  );
}
