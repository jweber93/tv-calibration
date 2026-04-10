import { fmtDateTime, measurementTimeSummary } from '../utils/fmt';

export function ImportLog({ imports }) {
  if (!imports?.length) return <div className="muted text-sm">No imports yet.</div>;
  const recent = [...imports].reverse().slice(0, 5);
  return (
    <div className="import-log">
      {recent.map((imp, i) => {
        const buckets = Object.entries(imp.buckets_populated || {})
          .map(([k, v]) => `${k.replace('_measurements', '')}: ${v}`)
          .join(', ');
        const measuredRange = measurementTimeSummary(imp.measurement_start, imp.measurement_end);
        return (
          <div className="import-log-entry" key={i}>
            <div>
              <div className="import-file">{imp.filename || 'auto-import'}</div>
              <div className="import-meta">{imp.total_rows} rows · {buckets || 'no data classified'}</div>
              <div className="import-meta">
                Imported {fmtDateTime(imp.timestamp)}
                {measuredRange ? ` · measured ${measuredRange}` : ''}
              </div>
              {(imp.abl_warnings || []).map((w, j) => (
                <div className="abl-warn" key={j}>⚠ {w}</div>
              ))}
            </div>
            <div className="import-meta" style={{ whiteSpace: 'nowrap' }}>
              {imp.auto_import ? '⚡ auto' : 'manual'}
            </div>
          </div>
        );
      })}
    </div>
  );
}
