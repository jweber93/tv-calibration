import { useState, useEffect } from 'react';
import { fmtDateTime, measurementTimeSummary } from '../utils/fmt';

export function WatchFolder({ watchStatus, onStart, onStop, defaultPath }) {
  const [path, setPath] = useState(defaultPath || '');

  // Sync input to defaultPath once it resolves from localStorage (only when not watching)
  useEffect(() => {
    if (defaultPath && !watchStatus.watching) setPath(defaultPath);
  }, [defaultPath]);
  const ws = watchStatus;

  const dotCls = ws.watching ? 'dot-active' : ws.error ? 'dot-error' : 'dot-inactive';
  const diagnostics = ws.diagnostics || {};
  const lastImport = ws.last_import || null;
  const lastImportMeasured = measurementTimeSummary(lastImport?.measurement_start, lastImport?.measurement_end);

  const diagRows = [
    ['Watched file',    diagnostics.watched_file || '—'],
    ['File exists',     diagnostics.watched_file_exists == null ? '—' : (diagnostics.watched_file_exists ? 'Yes' : 'No')],
    ['File mtime',      fmtDateTime(diagnostics.watched_file_mtime)],
    ['File size',       diagnostics.watched_file_size_bytes == null ? '—' : `${diagnostics.watched_file_size_bytes} bytes`],
    ['Pending timers',  diagnostics.pending_files ?? '—'],
    ['Last file event', diagnostics.last_event ? `${diagnostics.last_event.event} at ${fmtDateTime(diagnostics.last_event.timestamp)}` : '—'],
    ['Last import attempt', diagnostics.last_attempt ? `${diagnostics.last_attempt.status} at ${fmtDateTime(diagnostics.last_attempt.timestamp)}` : '—'],
  ];

  return (
    <div>
      <div className="text-sm muted" style={{ marginBottom: 10 }}>
        Point this at either the export folder or a specific ZRO CSV file to import automatically.
      </div>
      <div className="watch-row">
        <div className={`watch-status-dot ${dotCls}`} />
        <input
          type="text"
          value={path}
          onChange={e => setPath(e.target.value)}
          placeholder="C:\Users\...\ColourSpaceMeasurementLog.csv"
        />
        {ws.watching
          ? <button className="btn btn-sm" onClick={onStop}>Stop</button>
          : <button className="btn btn-primary btn-sm" onClick={() => onStart(path)}>Watch</button>
        }
      </div>
      <div className="text-sm mt-2">
        {ws.watching
          ? <span className="green">Watching: {ws.path}</span>
          : ws.error
          ? <span className="red">{ws.error}</span>
          : <span className="muted">Not watching</span>
        }
      </div>
      {lastImport && (
        <div className="text-sm muted mt-2">
          Last import: <strong>{lastImport.file || 'auto-import'}</strong> at {fmtDateTime(lastImport.timestamp)}
          {lastImportMeasured && <><br />Measurement time: {lastImportMeasured}</>}
        </div>
      )}
      <div className="mt-3">
        <div className="text-sm muted" style={{ marginBottom: 6 }}>Watcher diagnostics</div>
        <table className="data-table">
          <tbody>
            {diagRows.map(([label, value]) => (
              <tr key={label}><td>{label}</td><td>{value}</td></tr>
            ))}
            {diagnostics.last_attempt?.detail && (
              <tr><td>Attempt detail</td><td>{diagnostics.last_attempt.detail}</td></tr>
            )}
            {ws.error && <tr><td>Current error</td><td>{ws.error}</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  );
}
