import { useState, useEffect } from 'react';

function trySetLocalStorage(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch (err) {
    if (err.name === 'QuotaExceededError' || err.code === 22) {
      console.warn(`LocalStorage quota exceeded for key: ${key}`);
    } else {
      console.warn(`LocalStorage write failed for key: ${key}`, err);
    }
  }
}

export function CollapsibleSection({ title, storageKey, defaultOpen = false, summary, children }) {
  const [open, setOpen] = useState(() => {
    if (storageKey) {
      const saved = localStorage.getItem(`section:${storageKey}`);
      if (saved !== null) return saved === 'true';
    }
    return defaultOpen;
  });

  useEffect(() => {
    if (storageKey) trySetLocalStorage(`section:${storageKey}`, open);
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
