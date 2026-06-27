import { useState, useRef, useEffect } from 'react';
import { fmtDateTime, measurementTimeSummary } from '../utils/fmt';
import { Button } from './Button';

function StatusDot({ ok, configured }) {
  return (
    <span style={{
      width: 7, height: 7, borderRadius: '50%', flexShrink: 0, display: 'inline-block',
      background: ok ? 'var(--green)' : configured ? 'var(--ink3)' : 'var(--ink3)',
      boxShadow: ok ? '0 0 5px var(--green)' : 'none',
    }} />
  );
}

export function InstrumentPanel({
  session,
  watchStatus, watchDefaultPath,
  bridgeStatus, bridgeUrl,
  dogegenStatus, adbStatus,
  onMeasure, onSaveBridgeUrl,
  onStartWatch, onStopWatch,
  onUpload,
  onSaveDogegenConfig, onStartDogegen, onStopDogegen,
}) {
  const [measureBusy, setMeasureBusy] = useState(false);
  const [measureMsg, setMeasureMsg] = useState('');

  const [showBridgeUrl, setShowBridgeUrl] = useState(!bridgeUrl);
  const [bridgeUrlInput, setBridgeUrlInput] = useState(bridgeUrl || '');
  useEffect(() => { setBridgeUrlInput(bridgeUrl || ''); }, [bridgeUrl]);

  const [showWatchConfigure, setShowWatchConfigure] = useState(false);
  const [watchPath, setWatchPath] = useState(watchDefaultPath || '');
  useEffect(() => {
    if (watchDefaultPath && !watchStatus?.watching) setWatchPath(watchDefaultPath);
  }, [watchDefaultPath, watchStatus?.watching]);

  const inputRef = useRef(null);
  const [uploadBusy, setUploadBusy] = useState(false);
  const [uploadStatus, setUploadStatus] = useState('');
  const [dragging, setDragging] = useState(false);

  const [showDogegenSettings, setShowDogegenSettings] = useState(false);
  const [dogegenPath, setDogegenPath] = useState(dogegenStatus?.path || '');
  const [dogegenResolveHost, setDogegenResolveHost] = useState(dogegenStatus?.resolve_host || '');
  const [dogegenWindowPct, setDogegenWindowPct] = useState(dogegenStatus?.window_pct || 10);
  const [dogegenMaxcll, setDogegenMaxcll] = useState(dogegenStatus?.maxcll || 1000);
  const [dogegenBusy, setDogegenBusy] = useState(false);
  const [dogegenMsg, setDogegenMsg] = useState('');
  useEffect(() => {
    setDogegenPath(dogegenStatus?.path || '');
    setDogegenResolveHost(dogegenStatus?.resolve_host || '');
    setDogegenWindowPct(dogegenStatus?.window_pct || 10);
    setDogegenMaxcll(dogegenStatus?.maxcll || 1000);
  }, [dogegenStatus?.path, dogegenStatus?.resolve_host, dogegenStatus?.window_pct, dogegenStatus?.maxcll]);

  const bridgeOk = bridgeStatus?.ok;
  const dogegenSelected = session?.pattern_generator === 'dogegen';
  const dogegenRunning = dogegenStatus?.running;
  const dogegenReady = dogegenStatus?.ready;
  const dogegenBlocked = dogegenSelected && dogegenRunning && !dogegenReady;

  const ws = watchStatus || {};
  const watchDotCls = ws.watching ? 'dot-active' : ws.error ? 'dot-error' : 'dot-inactive';
  const lastImport = ws.last_import;
  const lastImportMeasured = measurementTimeSummary(lastImport?.measurement_start, lastImport?.measurement_end);

  async function handleMeasure() {
    setMeasureBusy(true);
    setMeasureMsg('');
    try {
      await onMeasure();
      setMeasureMsg('Triggered');
    } catch (e) {
      setMeasureMsg(`Error: ${e.message}`);
    } finally {
      setMeasureBusy(false);
    }
  }

  async function handleSaveBridgeUrl() {
    await onSaveBridgeUrl(bridgeUrlInput);
    setShowBridgeUrl(false);
  }

  async function handleFile(file) {
    if (!file) return;
    setUploadBusy(true);
    setUploadStatus('Uploading…');
    try {
      const result = await onUpload(file);
      setUploadStatus(`Imported ${result?.total_rows ?? '?'} rows`);
    } catch (e) {
      setUploadStatus(`Error: ${e.message}`);
    } finally {
      setUploadBusy(false);
    }
  }

  async function handleDogegenStart() {
    setDogegenBusy(true);
    setDogegenMsg('');
    try {
      await onStartDogegen();
    } catch (e) {
      setDogegenMsg(`Error: ${e.message}`);
    } finally {
      setDogegenBusy(false);
    }
  }

  async function handleDogegenStop() {
    setDogegenBusy(true);
    setDogegenMsg('');
    try {
      await onStopDogegen();
    } catch (e) {
      setDogegenMsg(`Error: ${e.message}`);
    } finally {
      setDogegenBusy(false);
    }
  }

  async function handleDogegenSave() {
    setDogegenBusy(true);
    setDogegenMsg('');
    try {
      await onSaveDogegenConfig({
        path: dogegenPath,
        resolve_host: dogegenResolveHost,
        window_pct: Number(dogegenWindowPct),
        maxcll: Number(dogegenMaxcll),
      });
      setShowDogegenSettings(false);
    } catch (e) {
      setDogegenMsg(`Error: ${e.message}`);
    } finally {
      setDogegenBusy(false);
    }
  }

  return (
    <div className="instrument-panel">

      {/* Measure */}
      <div className="ip-section">
        <button
          className="btn btn-primary"
          style={{ width: '100%', justifyContent: 'center', fontSize: '0.88rem', padding: '10px 0' }}
          disabled={!bridgeOk || measureBusy || dogegenBlocked}
          onClick={handleMeasure}
        >
          {measureBusy ? <span className="spinner" /> : 'Measure'}
        </button>
        {measureMsg && <div className="text-sm muted mt-2">{measureMsg}</div>}
        {dogegenBlocked && (
          <div className="text-sm muted mt-2">Waiting for Dogegen to warm up</div>
        )}
        {!bridgeOk && !dogegenBlocked && (
          <div className="text-sm muted mt-2">Bridge offline — configure URL below</div>
        )}
      </div>

      {/* Signal Chain */}
      <div className="ip-section">
        <div className="ip-label">Signal Chain</div>

        {/* ZRO Bridge */}
        <div className="ip-chain-row">
          <StatusDot ok={bridgeOk} configured={bridgeStatus?.configured} />
          <span className="text-sm" style={{ flex: 1 }}>
            ZRO Bridge&nbsp;
            {bridgeOk
              ? <span className="green">connected</span>
              : <span className="muted">offline</span>
            }
          </span>
          <button className="btn btn-sm" onClick={() => setShowBridgeUrl(s => !s)}>
            {showBridgeUrl ? 'Hide' : 'URL'}
          </button>
        </div>
        {showBridgeUrl && (
          <div className="ip-settings-body">
            <div className="text-sm muted" style={{ marginBottom: 6 }}>ZRO Bridge URL</div>
            <div className="bridge-url-row">
              <input
                type="text"
                value={bridgeUrlInput}
                onChange={e => setBridgeUrlInput(e.target.value)}
                placeholder="http://localhost:7070"
              />
              <Button small onClick={handleSaveBridgeUrl}>Save</Button>
            </div>
          </div>
        )}

        {/* Dogegen */}
        {dogegenSelected && (
          <div style={{ marginTop: 10 }}>
            <div className="ip-chain-row">
              <StatusDot ok={dogegenRunning} configured={dogegenStatus?.configured} />
              <span className="text-sm" style={{ flex: 1 }}>
                Dogegen&nbsp;
                {dogegenRunning
                  ? <span className="green">running</span>
                  : dogegenStatus?.configured
                    ? <span className="muted">ready</span>
                    : <span className="muted">not configured</span>
                }
              </span>
              <button className="btn btn-sm" onClick={() => setShowDogegenSettings(s => !s)}>
                {showDogegenSettings ? 'Hide' : 'Settings'}
              </button>
            </div>
            <div className="ip-chain-row" style={{ marginTop: 6, paddingLeft: 14, gap: 6 }}>
              <Button small primary={!dogegenRunning} disabled={dogegenBusy} onClick={handleDogegenStart}>
                {dogegenRunning ? 'Running' : 'Start'}
              </Button>
              <Button small disabled={dogegenBusy || !dogegenRunning} onClick={handleDogegenStop}>Stop</Button>
            </div>
            {showDogegenSettings && (
              <div className="ip-settings-body">
                <div className="text-sm muted" style={{ marginBottom: 4 }}>Executable path</div>
                <div className="bridge-url-row" style={{ marginBottom: 8 }}>
                  <input
                    type="text"
                    value={dogegenPath}
                    onChange={e => setDogegenPath(e.target.value)}
                    placeholder="C:\...\Dogegen.exe"
                  />
                </div>
                <div className="text-sm muted" style={{ marginBottom: 4 }}>Resolve host</div>
                <div className="bridge-url-row" style={{ marginBottom: 8 }}>
                  <input
                    type="text"
                    value={dogegenResolveHost}
                    onChange={e => setDogegenResolveHost(e.target.value)}
                    placeholder="127.0.0.1"
                  />
                </div>
                <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 8 }}>
                  <input
                    type="number"
                    min={1} max={100}
                    value={dogegenWindowPct}
                    onChange={e => setDogegenWindowPct(Number(e.target.value))}
                    style={{ width: 60, padding: '4px 6px', background: 'var(--bg)', border: '1px solid var(--line2)', borderRadius: 'var(--radius)', color: 'var(--ink)', fontSize: '0.79rem' }}
                  />
                  <span className="text-sm muted">Window %</span>
                  <input
                    type="number"
                    min={1}
                    value={dogegenMaxcll}
                    onChange={e => setDogegenMaxcll(Number(e.target.value))}
                    style={{ width: 80, padding: '4px 6px', background: 'var(--bg)', border: '1px solid var(--line2)', borderRadius: 'var(--radius)', color: 'var(--ink)', fontSize: '0.79rem' }}
                  />
                  <span className="text-sm muted">MaxCLL</span>
                </div>
                <Button small onClick={handleDogegenSave} disabled={dogegenBusy}>Save</Button>
              </div>
            )}
            {dogegenMsg && <div className="text-sm muted mt-2">{dogegenMsg}</div>}
          </div>
        )}

        {/* ADB */}
        {adbStatus && (
          <div className="ip-chain-row" style={{ marginTop: 10 }}>
            <StatusDot ok={adbStatus.connected} configured />
            <span className="text-sm" style={{ flex: 1 }}>
              ADB&nbsp;
              {adbStatus.connected
                ? <span className="green">connected</span>
                : <span className="muted">disconnected</span>
              }
            </span>
          </div>
        )}
      </div>

      {/* Last Import */}
      {lastImport && (
        <div className="ip-section">
          <div className="ip-label">Last Import</div>
          <div style={{ fontSize: '0.78rem', fontFamily: 'var(--mono)', color: 'var(--ink)', wordBreak: 'break-all' }}>
            {lastImport.file || 'auto-import'}
          </div>
          <div className="text-sm muted" style={{ marginTop: 3 }}>{fmtDateTime(lastImport.timestamp)}</div>
          {lastImportMeasured && (
            <div className="text-sm muted">Measurement: {lastImportMeasured}</div>
          )}
        </div>
      )}

      {/* Watch */}
      <div className="ip-section">
        <div className="ip-label">Auto-Watch</div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
          <span className={`watch-status-dot ${watchDotCls}`} />
          <span className="text-sm" style={{ flex: 1 }}>
            {ws.watching
              ? <span className="green">Watching</span>
              : ws.error
                ? <span className="red" style={{ fontSize: '0.78rem' }}>{ws.error}</span>
                : <span className="muted">Not watching</span>
            }
          </span>
          {ws.watching && (
            <button className="btn btn-sm" onClick={onStopWatch}>Stop</button>
          )}
        </div>
        {ws.watching && ws.path && (
          <div className="muted" style={{ fontSize: '0.72rem', fontFamily: 'var(--mono)', marginTop: 4, wordBreak: 'break-all' }}>
            {ws.path}
          </div>
        )}
        <button
          style={{ background: 'none', border: 'none', padding: '6px 0 0', cursor: 'pointer', color: 'var(--ink2)', fontFamily: 'inherit', fontSize: '0.78rem', display: 'flex', alignItems: 'center', gap: 4 }}
          onClick={() => setShowWatchConfigure(o => !o)}
        >
          {showWatchConfigure ? '▾' : '▸'} Configure
        </button>
        {showWatchConfigure && (
          <div className="ip-settings-body" style={{ marginTop: 6 }}>
            <div className="text-sm muted" style={{ marginBottom: 6 }}>
              Path or folder to watch for ZRO CSV exports
            </div>
            <div className="watch-row">
              <input
                type="text"
                value={watchPath}
                onChange={e => setWatchPath(e.target.value)}
                placeholder="C:\Users\...\ColourSpaceMeasurementLog.csv"
              />
              {ws.watching
                ? <button className="btn btn-sm" onClick={onStopWatch}>Stop</button>
                : <button className="btn btn-primary btn-sm" onClick={() => onStartWatch(watchPath)}>Watch</button>
              }
            </div>
          </div>
        )}
      </div>

      {/* Upload */}
      <div className="ip-section">
        <div
          className={`ip-upload ${dragging ? 'drag-over' : ''}`}
          onClick={() => inputRef.current?.click()}
          onDragOver={e => { e.preventDefault(); setDragging(true); }}
          onDragLeave={() => setDragging(false)}
          onDrop={e => { e.preventDefault(); setDragging(false); handleFile(e.dataTransfer.files[0]); }}
        >
          <span>📂</span>
          <span className="text-sm">Drop CSV or browse</span>
        </div>
        <input
          ref={inputRef}
          type="file"
          accept=".csv,.tsv,.txt"
          style={{ display: 'none' }}
          onChange={e => handleFile(e.target.files[0])}
        />
        {uploadStatus && (
          <div className="text-sm muted mt-2">
            {uploadBusy && <span className="spinner" style={{ marginRight: 6 }} />}
            {uploadStatus}
          </div>
        )}
      </div>

    </div>
  );
}
