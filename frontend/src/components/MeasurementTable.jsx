import { useState } from 'react';
import { fmtDe, fmtDateTime } from '../utils/fmt';

const COLUMNS = [
  { header: 'Label',   key: 'label' },
  { header: 'Measured', key: 'timestamp' },
  { header: 'Nits',    key: 'Y' },
  { header: 'x',       key: 'x' },
  { header: 'y',       key: 'y' },
  { header: 'CCT',     key: 'cct' },
  { header: 'ΔE',      key: 'delta_e' },
];
const GAMMA_COL = { header: 'γ', key: 'effective_gamma' };

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
          {columns.map(({ header, key }) => (
            <th
              key={key}
              onClick={() => handleSort(key)}
              style={{ cursor: 'pointer', userSelect: 'none', whiteSpace: 'nowrap' }}
              title={`Sort by ${header}`}
            >
              {header}
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
            <td>{m.Y ? m.Y.toFixed(1) : '—'}</td>
            <td>{m.x ? m.x.toFixed(4) : '—'}</td>
            <td>{m.y ? m.y.toFixed(4) : '—'}</td>
            <td>{m.cct ? Math.round(m.cct) + 'K' : '—'}</td>
            <td dangerouslySetInnerHTML={{ __html: fmtDe(m.delta_e) }} />
            {includeGamma && <td>{m.effective_gamma != null ? m.effective_gamma.toFixed(3) : '—'}</td>}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
