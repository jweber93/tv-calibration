import { fmtDe, fmtDateTime } from '../utils/fmt';

export function MeasurementTable({ measurements, includeGamma }) {
  if (!measurements?.length) {
    return (
      <div className="muted text-sm" style={{ padding: '12px 0' }}>
        No measurements yet — import a ZRO CSV to populate this table.
      </div>
    );
  }

  const headers = ['Label', 'Measured', 'Nits', 'x', 'y', 'CCT', 'ΔE'];
  if (includeGamma) headers.push('γ');
  const rows = [...measurements].sort((a, b) => {
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
        <tr>{headers.map(h => <th key={h}>{h}</th>)}</tr>
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
