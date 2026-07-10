export function fmtDe(v) {
  if (v == null) return '<span class="muted">—</span>';
  const cls = v <= 1 ? 'de-excellent' : v <= 2 ? 'de-good' : v <= 3 ? 'de-acceptable' : 'de-poor';
  return `<span class="de-pill ${cls}">ΔE ${v.toFixed(2)}</span>`;
}

export function fmtNits(v) {
  return v == null || v <= 0 ? '—' : `${v.toFixed(1)} nits`;
}

export function dirIcon(d) {
  return d === 'up' ? '↑' : d === 'down' ? '↓' : d === 'hold' ? '–' : '✓';
}

export function dirClass(d) {
  return d === 'up' ? 'up' : d === 'down' ? 'down' : d === 'hold' ? 'hold' : 'check';
}

export function fmtDateTime(ts) {
  if (!ts) return '—';
  const dt = new Date(ts);
  if (Number.isNaN(dt.getTime())) return ts;
  return dt.toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', second: '2-digit' });
}

export function fmtTime(ts) {
  if (!ts) return '—';
  const dt = new Date(ts);
  if (Number.isNaN(dt.getTime())) return ts;
  return dt.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit', second: '2-digit' });
}

export function measurementTimeSummary(startTs, endTs) {
  if (!startTs && !endTs) return '';
  if (startTs && endTs && startTs !== endTs) return `${fmtTime(startTs)} to ${fmtTime(endTs)}`;
  return fmtTime(endTs || startTs);
}

export const SDR_GAMMA_TARGET = 2.2;

/**
 * Check if EOTF denotes PQ / SMPTE ST.2084.
 * Mirrors calibrator/utils.py:is_pq_eotf — PQ does not follow a power law,
 * so gamma tracking must be compared against 1.0 instead of SDR_GAMMA_TARGET.
 */
export function isPqEotf(eotf) {
  const e = (eotf || '').toLowerCase();
  return e.includes('pq') || e.includes('2084');
}
