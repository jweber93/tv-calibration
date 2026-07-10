import { useState } from 'react';
import { fmtDe, fmtDateTime, SDR_GAMMA_TARGET } from '../utils/fmt';
import { Tooltip } from './Tooltip';

const COLUMNS = [
  { header: 'Label',   key: 'label' },
  { header: 'Measured', key: 'timestamp' },
  { header: 'Nits',    key: 'Y' },
  { header: 'x',       key: 'x' },
  { header: 'y',       key: 'y' },
  { header: 'CCT',     key: 'cct',     tooltip: 'Correlated Color Temperature in Kelvin. D65 (the standard white point) is 6504 K.' },
  { header: 'ΔE',      key: 'delta_e', tooltip: 'Delta E — perceptual color difference. ΔE < 2 is invisible to most viewers; < 1 is excellent.' },
];
const GAMMA_COL = { header: 'γ', key: 'effective_gamma', tooltip: `Effective gamma — the measured power-law exponent at each stimulus step. SDR target: ${SDR_GAMMA_TARGET} (PQ/HDR tracks 1.0).` };

function getValue(m, key) {
  if (key === 'label')     return m.label || '';
  if (key === 'timestamp') return m.timestamp || '';
  return m[key] ?? null;
}

export function MeasurementTable({ measurements, includeGamma }) {
  const [sortCol, setSortCol] = useState(null);
  const [sortDir, setSortDir] = useState('asc');

  if (!measurements?.length) {
    return (
      <div className="muted text-sm" style={{ padding: '12px 0' }}>
        No measurements yet — import a ZRO CSV to populate this table.
      </div>
    );
  }

  const columns = includeGamma ? [...COLUMNS, GAMMA_COL] : COLUMNS;

  function handleSort(key) {
    if (sortCol === key) {
      setSortDir(d => d === 'asc' ? 'desc' : 'asc');
    } else {
      setSortCol(key);
      setSortDir('asc');
    }
  }

  const rows = [...measurements].sort((a, b) => {
    if (sortCol) {
      const av = getValue(a, sortCol);
      const bv = getValue(b, sortCol);
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      const cmp = typeof av === 'string' ? av.localeCompare(bv) : av - bv;
      return sortDir === 'asc' ? cmp : -cmp;
    }
    // default: sort by stimulus %
    const ap = a.stimulus_pct;
    const bp = b.stimulus_pct;
    if (ap == null && bp == null) return String(a.timestamp || '').localeCompare(String(b.timestamp || ''));
    if (ap == null) return 1;
    if (bp == null) return -1;
    return ap - bp;
  });

  return (
    <table className="data-table">
      <thead>
        <tr>
          {columns.map(({ header, key, tooltip }) => (
            <th
              key={key}
              onClick={() => handleSort(key)}
              style={{ cursor: 'pointer', userSelect: 'none', whiteSpace: 'nowrap' }}
              title={`Sort by ${header}`}
            >
              {header}
              {tooltip && <Tooltip text={tooltip} />}
              {sortCol === key ? (sortDir === 'asc' ? ' ▲' : ' ▼') : ''}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((m, i) => (
          <tr key={i}>
            <td>{m.label || ''}</td>
            <td>{fmtDateTime(m.timestamp)}</td>
            <td>{m.Y != null ? m.Y.toFixed(1) : '—'}</td>
            <td>{m.x != null ? m.x.toFixed(4) : '—'}</td>
            <td>{m.y != null ? m.y.toFixed(4) : '—'}</td>
            <td>{m.cct != null ? Math.round(m.cct) + 'K' : '—'}</td>
            <td dangerouslySetInnerHTML={{ __html: fmtDe(m.delta_e) }} />
            {includeGamma && <td>{m.effective_gamma != null ? m.effective_gamma.toFixed(3) : '—'}</td>}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
