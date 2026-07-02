import { useEffect, useRef, useState } from 'react';
import { Card } from './Card';
import { Button } from './Button';
import { fmtDe } from '../utils/fmt';
import { api } from '../api/client';

const SSE_RETRY_DELAY_MS = 1500;
const SSE_MAX_RETRIES = 5;

function initialColourState(colours) {
  const state = {};
  for (const c of colours) {
    state[c] = { status: 'pending', deltaE: null, waiting: false, lastMessage: null, iterations: 0 };
  }
  return state;
}

/**
 * AutocalCard — Item 1e/1f: guided measure -> correct -> apply -> re-measure loop.
 *
 * Manual apply shows an instruction ("Raise Red Saturation to 4") and pauses
 * until the user clicks "I made the change" (autocal/confirm), which is also
 * what happens when ADB auto-apply (1f) fails on a step and the loop falls
 * back to a manual instruction for just that one correction.
 */
export function AutocalCard({ sid, colours }) {
  const [applyMode, setApplyMode] = useState('manual');
  const [maxIterations, setMaxIterations] = useState(8);
  const [running, setRunning] = useState(false);
  const [colourState, setColourState] = useState(() => initialColourState(colours));
  const [waiting, setWaiting] = useState(null); // { colour, iteration } | null
  const [error, setError] = useState(null);
  const [done, setDone] = useState(false);

  const esRef = useRef(null);
  const esGenRef = useRef(0);
  const esRetryRef = useRef(null);

  useEffect(() => {
    api.getPrefs().then(prefs => {
      if (prefs?.autocal?.apply_mode) setApplyMode(prefs.autocal.apply_mode);
      if (prefs?.autocal?.max_iterations) setMaxIterations(prefs.autocal.max_iterations);
    }).catch(() => { /* fall back to defaults */ });
  }, []);

  useEffect(() => {
    if (!running) setColourState(initialColourState(colours));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [colours]);

  function stopStream() {
    clearTimeout(esRetryRef.current);
    esGenRef.current++;
    esRef.current?.close();
    esRef.current = null;
  }

  useEffect(() => stopStream, []);

  function startStream() {
    stopStream();
    const generation = esGenRef.current;
    let retryCount = 0;

    function connect() {
      const es = new EventSource(`/api/session/${sid}/autocal/stream`);

      es.addEventListener('autocal_start', () => {
        setError(null);
        setDone(false);
      });

      es.addEventListener('autocal_iteration', e => {
        try {
          const d = JSON.parse(e.data);
          setColourState(prev => ({
            ...prev,
            [d.colour]: {
              ...(prev[d.colour] || {}),
              status: 'running',
              deltaE: d.delta_e ?? prev[d.colour]?.deltaE ?? null,
              lastMessage: d.apply_message,
              iterations: d.iteration,
              waiting: false,
            },
          }));
        } catch { /* malformed SSE payload */ }
      });

      es.addEventListener('autocal_waiting', e => {
        try {
          const d = JSON.parse(e.data);
          setWaiting(d);
          setColourState(prev => ({
            ...prev,
            [d.colour]: { ...(prev[d.colour] || {}), status: 'waiting', waiting: true },
          }));
        } catch { /* malformed SSE payload */ }
      });

      es.addEventListener('autocal_colour_done', e => {
        try {
          const d = JSON.parse(e.data);
          setColourState(prev => ({
            ...prev,
            [d.colour]: {
              ...(prev[d.colour] || {}),
              status: d.converged ? 'converged' : 'out_of_range',
              deltaE: d.delta_e,
              iterations: d.iterations,
              reason: d.reason,
              waiting: false,
            },
          }));
        } catch { /* malformed SSE payload */ }
        setWaiting(null);
      });

      es.addEventListener('autocal_done', () => {
        setRunning(false);
        setDone(true);
        setWaiting(null);
        stopStream();
      });

      es.addEventListener('autocal_error', e => {
        const msg = (() => { try { return JSON.parse(e.data); } catch { return String(e.data); } })();
        setError(msg);
        setRunning(false);
        setWaiting(null);
        stopStream();
      });

      es.onerror = () => {
        es.close();
        if (esGenRef.current !== generation) return;
        retryCount++;
        if (retryCount >= SSE_MAX_RETRIES) {
          setError('Autocal progress stream disconnected — reload to reconnect.');
          return;
        }
        esRetryRef.current = setTimeout(connect, SSE_RETRY_DELAY_MS);
      };

      esRef.current = es;
    }

    connect();
  }

  async function handleStart() {
    if (!sid || running) return;
    setError(null);
    setDone(false);
    setColourState(initialColourState(colours));
    setRunning(true);
    try {
      await api.autocalRun(sid, { colours, apply_mode: applyMode, max_iterations: maxIterations });
      startStream();
    } catch (err) {
      setRunning(false);
      setError(err.message);
    }
  }

  async function handleStop() {
    if (!sid) return;
    try { await api.autocalStop(sid); } catch { /* best-effort */ }
  }

  async function handleConfirm() {
    if (!sid) return;
    try {
      await api.autocalConfirm(sid);
      setWaiting(null);
    } catch (err) {
      setError(err.message);
    }
  }

  function changeApplyMode(mode) {
    setApplyMode(mode);
    api.savePrefs({ autocal_apply_mode: mode }).catch(() => {});
  }

  const statusBadge = (status) => {
    const map = {
      pending:      { text: 'Not started', color: 'var(--ink2)', bg: 'var(--panel2)' },
      running:      { text: 'Measuring…',  color: 'var(--ink2)', bg: 'var(--panel2)' },
      waiting:      { text: 'Waiting on you', color: 'var(--yellow)', bg: 'var(--yellow-dim)' },
      converged:    { text: 'Converged',   color: 'var(--green)', bg: 'var(--green-dim)' },
      out_of_range: { text: 'Keep going',  color: 'var(--red)', bg: 'var(--red-dim)' },
    };
    const s = map[status] || map.pending;
    return (
      <span style={{ fontSize: '0.72rem', fontWeight: 600, padding: '3px 8px', borderRadius: 'var(--radius)', color: s.color, background: s.bg, border: `1px solid ${s.color}33` }}>
        {s.text}
      </span>
    );
  };

  return (
    <Card title="Autocal — guided measure / correct / apply loop">
      <div className="text-sm muted" style={{ marginBottom: 14 }}>
        Runs the damped CMS correction controller against each selected primary: measure, compute
        the fix, apply it, re-measure — looping until each primary clears the ΔE gate or the
        iteration cap is hit. In manual mode you make the change on the TV yourself; in ADB mode
        the app applies it, falling back to a manual instruction if the TV doesn't respond.
      </div>

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 14, alignItems: 'center', marginBottom: 14 }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
          <label style={{ fontSize: '0.72rem', fontWeight: 600, color: 'var(--ink2)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>
            Apply mode
          </label>
          <div className="btn-group">
            <Button small primary={applyMode === 'manual'} disabled={running} onClick={() => changeApplyMode('manual')}>
              Manual
            </Button>
            <Button small primary={applyMode === 'adb'} disabled={running} onClick={() => changeApplyMode('adb')}>
              ADB (auto)
            </Button>
          </div>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
          <label style={{ fontSize: '0.72rem', fontWeight: 600, color: 'var(--ink2)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>
            Iteration cap
          </label>
          <input
            type="number"
            min={1}
            max={50}
            value={maxIterations}
            disabled={running}
            onChange={e => setMaxIterations(Number(e.target.value) || 1)}
            style={{ width: 64, background: 'var(--panel2)', border: '1px solid var(--line2)', borderRadius: 'var(--radius)', color: 'var(--ink)', fontSize: '0.84rem', padding: '6px 8px', outline: 'none', fontFamily: 'inherit' }}
          />
        </div>

        <div style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
          {!running
            ? <Button primary onClick={handleStart} disabled={!colours?.length}>Start Autocal</Button>
            : <Button danger onClick={handleStop}>Stop</Button>
          }
        </div>
      </div>

      {error && (
        <div style={{ fontSize: '0.8rem', color: 'var(--red)', marginBottom: 12 }}>{String(error)}</div>
      )}
      {done && !error && (
        <div style={{ fontSize: '0.8rem', color: 'var(--green)', marginBottom: 12 }}>Autocal run finished.</div>
      )}

      {waiting && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '10px 14px', marginBottom: 14, background: 'var(--yellow-dim)', border: '1px solid var(--yellow)', borderRadius: 'var(--radius)' }}>
          <div style={{ flex: 1, fontSize: '0.84rem' }}>
            <strong>{waiting.colour}:</strong> {colourState[waiting.colour]?.lastMessage || 'Apply the shown change on the TV'}, then confirm.
          </div>
          <Button small primary onClick={handleConfirm}>I made the change — Remeasure</Button>
        </div>
      )}

      <div className="three-col">
        {colours.map(colour => {
          const cs = colourState[colour] || { status: 'pending' };
          return (
            <div key={colour} style={{ display: 'flex', flexDirection: 'column', gap: 6, padding: 10, background: 'var(--panel2)', borderRadius: 'var(--radius)', border: '1px solid var(--line)' }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
                <span style={{ fontSize: '0.84rem', fontWeight: 600 }}>{colour}</span>
                {statusBadge(cs.status)}
              </div>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
                {cs.deltaE != null
                  ? <span dangerouslySetInnerHTML={{ __html: fmtDe(cs.deltaE) }} />
                  : <span className="muted text-sm">—</span>}
                <span className="muted text-sm">{cs.iterations ? `${cs.iterations} iter` : ''}</span>
              </div>
              {cs.status === 'running' && cs.lastMessage && (
                <div className="text-sm muted" style={{ fontSize: '0.76rem' }}>{cs.lastMessage}</div>
              )}
            </div>
          );
        })}
      </div>
    </Card>
  );
}
