import { useRef, useState } from 'react';

export function UploadZone({ onUpload }) {
  const inputRef = useRef(null);
  const [dragging, setDragging] = useState(false);
  const [status, setStatus] = useState('');
  const [busy, setBusy] = useState(false);

  async function handleFile(file) {
    if (!file) return;
    setBusy(true);
    setStatus('Uploading…');
    try {
      const result = await onUpload(file);
      setStatus(`Imported ${result?.total_rows ?? '?'} rows`);
    } catch (e) {
      setStatus(`Error: ${e.message}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <div
        className={`upload-zone ${dragging ? 'drag-over' : ''}`}
        onClick={() => inputRef.current?.click()}
        onDragOver={e => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={e => { e.preventDefault(); setDragging(false); handleFile(e.dataTransfer.files[0]); }}
      >
        <div style={{ fontSize: '1.4rem', marginBottom: 8 }}>📂</div>
        <strong>Click to upload</strong> or drag & drop a ZRO CSV export<br />
        <span className="text-sm muted">(tab-separated: Date / R / G / B / Y / x / y / msec)</span>
      </div>
      <input
        ref={inputRef}
        type="file"
        accept=".csv,.tsv,.txt"
        style={{ display: 'none' }}
        onChange={e => handleFile(e.target.files[0])}
      />
      {status && (
        <div className="text-sm muted mt-2">
          {busy && <span className="spinner" style={{ marginRight: 6 }} />}
          {status}
        </div>
      )}
    </div>
  );
}
