/**
 * TvSettingsInput — collapsible JSON paste field for current TV hardware slider values.
 *
 * Allows the user to paste their current WB/CMS slider values so the LLM can
 * compute specific "Change from X to Y" deltas rather than reasoning in a vacuum.
 *
 * Props:
 *   onSave — async (payload) => void  — called with the parsed TVSettings object
 */
import { useState } from 'react';

const PLACEHOLDER = JSON.stringify(
  {
    two_point_wb: { R_gain: 0, G_gain: 0, B_gain: -3, R_offset: 0, G_offset: 0, B_offset: 2 },
    cms_sliders: {
      Red:     { Hue: 0, Saturation: 0, Brightness: 0 },
      Green:   { Hue: 0, Saturation: 0, Brightness: 0 },
      Blue:    { Hue: 0, Saturation: 0, Brightness: 0 },
    },
  },
  null,
  2,
);

export function TvSettingsInput({ onSave }) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState('');
  const [status, setStatus] = useState(null); // 'saved' | 'error' | null
  const [errorMsg, setErrorMsg] = useState('');

  async function handleSave() {
    setStatus(null);
    setErrorMsg('');
    let parsed;
    try {
      parsed = JSON.parse(text.trim() || '{}');
    } catch (e) {
      setStatus('error');
      setErrorMsg('Invalid JSON: ' + e.message);
      return;
    }
    try {
      await onSave(parsed);
      setStatus('saved');
      setTimeout(() => setStatus(null), 3000);
    } catch (e) {
      setStatus('error');
      setErrorMsg(e.message || 'Save failed');
    }
  }

  return (
    <div style={{ margin: '0 0 10px 0' }}>
      <button
        onClick={() => setOpen(o => !o)}
        style={{
          background: 'none',
          border: 'none',
          color: 'var(--muted)',
          cursor: 'pointer',
          fontSize: '0.75rem',
          padding: '2px 0',
          display: 'flex',
          alignItems: 'center',
          gap: 5,
          fontFamily: 'inherit',
        }}
      >
        <span style={{ fontSize: '0.65rem' }}>{open ? '▾' : '▸'}</span>
        Current TV Settings (for LLM context)
      </button>

      {open && (
        <div style={{
          marginTop: 6,
          background: 'var(--surface2)',
          border: '1px solid var(--border-subtle, rgba(255,255,255,0.08))',
          borderRadius: 'var(--radius-md, 6px)',
          padding: '10px 12px',
        }}>
          <div style={{ fontSize: '0.75rem', color: 'var(--muted)', marginBottom: 6, lineHeight: 1.5 }}>
            Paste your current WB / CMS slider values as JSON. The LLM will use these
            to compute specific "from → to" adjustment deltas.
          </div>
          <textarea
            value={text}
            onChange={e => setText(e.target.value)}
            placeholder={PLACEHOLDER}
            rows={10}
            spellCheck={false}
            style={{
              width: '100%',
              fontFamily: 'monospace',
              fontSize: '0.76rem',
              background: 'var(--surface3, rgba(0,0,0,0.2))',
              color: 'var(--text)',
              border: `1px solid ${status === 'error' ? 'var(--red)' : 'var(--border-subtle, rgba(255,255,255,0.1))'}`,
              borderRadius: 4,
              padding: '6px 8px',
              resize: 'vertical',
              boxSizing: 'border-box',
              outline: 'none',
            }}
          />
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 6 }}>
            <button
              onClick={handleSave}
              className="btn btn-primary"
              style={{ fontSize: '0.78rem', padding: '4px 14px' }}
            >
              Save Settings
            </button>
            {status === 'saved' && (
              <span style={{ fontSize: '0.75rem', color: 'var(--green)' }}>Saved</span>
            )}
            {status === 'error' && (
              <span style={{ fontSize: '0.75rem', color: 'var(--red)' }}>{errorMsg}</span>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
