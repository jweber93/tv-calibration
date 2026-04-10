import { useState, useEffect } from 'react';

export function CollapsibleSection({ title, storageKey, defaultOpen = false, summary, children }) {
  const [open, setOpen] = useState(() => {
    if (storageKey) {
      const saved = localStorage.getItem(`section:${storageKey}`);
      if (saved !== null) return saved === 'true';
    }
    return defaultOpen;
  });

  useEffect(() => {
    if (storageKey) localStorage.setItem(`section:${storageKey}`, open);
  }, [open, storageKey]);

  return (
    <div className="collapsible-section">
      <button
        className={`collapsible-header${open ? ' open' : ''}`}
        onClick={() => setOpen(o => !o)}
        aria-expanded={open}
      >
        <span className="collapsible-chevron">{open ? '▾' : '▸'}</span>
        <span className="collapsible-title">{title}</span>
        {!open && summary && <span className="collapsible-summary">{summary}</span>}
      </button>
      {open && <div className="collapsible-body">{children}</div>}
    </div>
  );
}
