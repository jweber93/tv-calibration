import { useEffect, useState } from 'react';
import { Button } from './Button';
import * as api from '../api/client';

export function LlmConnectionCard({ sid, onConfigureLlm }) {
  const [endpoint, setEndpoint] = useState('');
  const [model, setModel] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [status, setStatus] = useState(null); // null | 'testing' | { configured, reachable, model, error }
  const [savedModel, setSavedModel] = useState(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!sid) return;
    api.getLlmStatus(sid).then(data => {
      if (data?.configured) {
        setEndpoint(data.endpoint || '');
        setModel(data.model || '');
        setStatus(data);
      }
    }).catch(() => {});
  }, [sid]);

  async function handleSave() {
    if (!sid || loading) return;
    setLoading(true);
    setSavedModel(null);
    const body = { endpoint: endpoint.trim(), model: model.trim() };
    if (apiKey.trim()) body.api_key = apiKey.trim();
    try {
      const result = onConfigureLlm
        ? await onConfigureLlm(body)
        : await api.configureLlm(sid, body);
      setSavedModel(result?.model || null);
      setApiKey('');
    } finally {
      setLoading(false);
    }
  }

  async function handleTest() {
    if (!sid || loading) return;
    setLoading(true);
    setStatus('testing');
    try {
      const data = await api.getLlmStatus(sid);
      setStatus(data);
    } catch (err) {
      setStatus({ configured: false, reachable: false, error: err.message });
    } finally {
      setLoading(false);
    }
  }

  const statusBadge = (() => {
    if (status === 'testing') {
      return (
        <span style={{ fontSize: '0.8rem', fontWeight: 600, padding: '5px 10px', borderRadius: 'var(--radius)', color: 'var(--ink2)', background: 'var(--panel2)', border: '1px solid var(--line)' }}>
          Testing…
        </span>
      );
    }
    if (!status || !status.configured) {
      return (
        <span style={{ fontSize: '0.8rem', fontWeight: 600, padding: '5px 10px', borderRadius: 'var(--radius)', color: 'var(--ink2)', background: 'var(--panel2)', border: '1px solid var(--line)' }}>
          Not configured
        </span>
      );
    }
    if (status.reachable) {
      return (
        <span style={{ fontSize: '0.8rem', fontWeight: 600, padding: '5px 10px', borderRadius: 'var(--radius)', color: 'var(--green)', background: 'var(--green-dim)', border: '1px solid #22c98759' }}>
          Reachable ({status.model})
        </span>
      );
    }
    return (
      <span style={{ fontSize: '0.8rem', fontWeight: 600, padding: '5px 10px', borderRadius: 'var(--radius)', color: 'var(--red)', background: 'var(--red-dim)', border: '1px solid #e74c3c4d' }}>
        {status.error || 'Unreachable'}
      </span>
    );
  })();

  return (
    <div className="card">
      <div style={{ fontSize: '1rem', fontWeight: 700, color: 'var(--ink)', marginBottom: 12 }}>
        AI Assistant
      </div>
      <div className="text-sm muted" style={{ marginBottom: 16 }}>
        Connect an OpenAI-compatible LLM (e.g. Ollama, LiteLLM) to get real-time coaching
        after each ZRO import. If unconfigured the rest of the workflow is unaffected.
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
          <label style={{ fontSize: '0.78rem', fontWeight: 600, color: 'var(--ink2)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>
            Endpoint URL
          </label>
          <input
            type="url"
            value={endpoint}
            onChange={e => setEndpoint(e.target.value)}
            placeholder="http://localhost:11434/v1"
            autoComplete="off"
            spellCheck={false}
            style={{ background: 'var(--panel2)', border: '1px solid var(--line2)', borderRadius: 'var(--radius)', color: 'var(--ink)', fontSize: '0.84rem', padding: '7px 10px', width: '100%', maxWidth: 480, outline: 'none', fontFamily: 'inherit' }}
          />
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
          <label style={{ fontSize: '0.78rem', fontWeight: 600, color: 'var(--ink2)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>
            Model name
          </label>
          <input
            type="text"
            value={model}
            onChange={e => setModel(e.target.value)}
            placeholder="llama3"
            autoComplete="off"
            spellCheck={false}
            style={{ background: 'var(--panel2)', border: '1px solid var(--line2)', borderRadius: 'var(--radius)', color: 'var(--ink)', fontSize: '0.84rem', padding: '7px 10px', width: '100%', maxWidth: 480, outline: 'none', fontFamily: 'inherit' }}
          />
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
          <label style={{ fontSize: '0.78rem', fontWeight: 600, color: 'var(--ink2)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>
            API key{' '}
            <span style={{ fontSize: '0.72rem', fontWeight: 400, textTransform: 'none', letterSpacing: 0, color: 'var(--line2)' }}>
              (optional — never shown again)
            </span>
          </label>
          <input
            type="password"
            value={apiKey}
            onChange={e => setApiKey(e.target.value)}
            placeholder="sk-…"
            autoComplete="new-password"
            style={{ background: 'var(--panel2)', border: '1px solid var(--line2)', borderRadius: 'var(--radius)', color: 'var(--ink)', fontSize: '0.84rem', padding: '7px 10px', width: '100%', maxWidth: 480, outline: 'none', fontFamily: 'inherit' }}
          />
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginTop: 2 }}>
          <div className="btn-group">
            <Button small onClick={handleSave} disabled={loading}>Save</Button>
            <Button small onClick={handleTest} disabled={loading}>Test connection</Button>
            {statusBadge}
          </div>
          {savedModel && (
            <div style={{ fontSize: '0.78rem', color: 'var(--green)' }}>
              Saved — configured model: {savedModel}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
