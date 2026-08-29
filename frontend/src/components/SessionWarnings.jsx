import { useState } from 'react';
import { Button } from './Button';

/**
 * SessionWarnings — dismissible banner for session-creation warnings (#673).
 *
 * Shows the `warnings` list from session_view: configured session defaults
 * that failed to apply (e.g. an inconsistent code_scale + signal_range combo).
 * Without this the failure was only a server-side log line and the session
 * silently ran with different effective settings than the user configured.
 *
 * Dismiss is per-session: App remounts this component with a new key when the
 * session id changes, so a fresh session always shows its own warnings.
 *
 * Props:
 *   warnings — string[] (session.warnings); renders nothing when empty
 */
export function SessionWarnings({ warnings }) {
  const [dismissed, setDismissed] = useState(false);
  if (!warnings || warnings.length === 0 || dismissed) return null;

  return (
    <div className="zro-card warning" data-testid="session-warnings">
      <div className="zro-card-title">Session defaults not applied</div>
      <div className="zro-card-summary" style={{ marginBottom: 8 }}>
        These configured defaults could not be applied, so this session is running with
        different settings than your preferences describe. Review them before measuring.
      </div>
      <ul style={{ margin: '0 0 10px', paddingLeft: '1.1em', fontSize: '0.82rem', color: 'var(--ink2)' }}>
        {warnings.map((w, i) => (
          <li key={i}>{w}</li>
        ))}
      </ul>
      <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
        <Button onClick={() => setDismissed(true)}>Dismiss</Button>
      </div>
    </div>
  );
}
