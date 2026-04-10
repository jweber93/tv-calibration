/**
 * AiInsightPanel — native ES module (no build step required).
 *
 * Mounts an "AI Calibration Coach" panel that:
 *  - is hidden when no LLM is configured on the session
 *  - shows a loading spinner while an LLM call is in-flight
 *  - renders the markdown-formatted llm_insight text on arrival
 *  - shows a dismissable error state on llm_error
 *  - collapses/expands via a toggle button
 *  - fully dismisses until the calibration step changes
 *
 * SSE subscriptions:
 *  /events/{sid}                   → session updates (step changes, new imports)
 *  /api/session/{sid}/llm/stream   → llm_insight / llm_error events
 */

/** Steps where the panel should appear. */
const AI_STEPS = new Set(['white_balance', 'gamma', 'color_tuner', 'post_grayscale']);

/** Timeout (ms) before we stop showing the spinner with no response. */
const SPINNER_TIMEOUT_MS = 40_000;

// ---------------------------------------------------------------------------
// Minimal markdown → safe HTML converter (handles the LLM output format)
// ---------------------------------------------------------------------------

function markdownToHtml(text) {
  if (!text) return '';

  // Escape HTML entities first.
  let html = text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');

  // Process line by line so we can group blockquotes.
  const lines = html.split('\n');
  const out = [];
  let inBlockquote = false;

  for (const rawLine of lines) {
    // Blockquote: lines starting with &gt; (escaped >)
    if (rawLine.startsWith('&gt; ') || rawLine === '&gt;') {
      const inner = rawLine.startsWith('&gt; ') ? rawLine.slice(5) : '';
      if (!inBlockquote) {
        out.push('<blockquote>');
        inBlockquote = true;
      }
      out.push(`<p>${inlineMarkdown(inner)}</p>`);
    } else {
      if (inBlockquote) {
        out.push('</blockquote>');
        inBlockquote = false;
      }
      if (rawLine.trim() === '') {
        out.push('<br>');
      } else {
        out.push(`<p>${inlineMarkdown(rawLine)}</p>`);
      }
    }
  }
  if (inBlockquote) out.push('</blockquote>');

  return out.join('\n');
}

/** Apply inline markdown: **bold**, *italic*, `code`. */
function inlineMarkdown(text) {
  return text
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.+?)\*/g, '<em>$1</em>')
    .replace(/`(.+?)`/g, '<code>$1</code>');
}

// ---------------------------------------------------------------------------
// Panel implementation
// ---------------------------------------------------------------------------

class AiInsightPanel {
  constructor() {
    /** @type {object|null} Latest session object from the API */
    this.session = null;
    /** @type {EventSource|null} */
    this._sessionEs = null;
    /** @type {EventSource|null} */
    this._llmEs = null;
    /** @type {{text:string, phase:string, timestamp:string}|null} */
    this._insight = null;
    /** @type {string|null} */
    this._error = null;
    /** Whether the LLM stream is open and awaiting a response. */
    this._loading = false;
    /** Timer handle for spinner timeout. */
    this._spinnerTimer = null;
    /** Whether the user collapsed the panel body. */
    this._collapsed = false;
    /** Whether the user fully dismissed the panel (resets on step change). */
    this._dismissed = false;
    /** The session step when the panel was last dismissed. */
    this._dismissedOnStep = null;
    /** Import count at the time the LLM stream was last opened. */
    this._importCount = 0;

    this._el = this._createElement();
    document.body.appendChild(this._el);

    // Start by loading the current session, then subscribe to SSE.
    this._initSession();
  }

  // -------------------------------------------------------------------------
  // Initialisation
  // -------------------------------------------------------------------------

  async _initSession() {
    try {
      const res = await fetch('/api/session');
      if (res.ok) {
        const session = await res.json();
        if (session?.id) {
          this._onSession(session);
          this._openSessionStream(session.id);
          return;
        }
      }
    } catch (_) { /* ignore */ }

    // No session yet — retry after a short delay.
    setTimeout(() => this._initSession(), 3000);
  }

  _openSessionStream(sid) {
    if (this._sessionEs) {
      this._sessionEs.close();
      this._sessionEs = null;
    }
    const es = new EventSource(`/events/${sid}`);
    es.addEventListener('session', (e) => {
      try { this._onSession(JSON.parse(e.data)); } catch (_) { /* ignore */ }
    });
    es.onerror = () => {
      // Connection dropped — retry init after a delay.
      es.close();
      this._sessionEs = null;
      setTimeout(() => this._initSession(), 5000);
    };
    this._sessionEs = es;
  }

  // -------------------------------------------------------------------------
  // Session state handling
  // -------------------------------------------------------------------------

  _onSession(session) {
    const prevStep = this.session?.step;
    const prevId = this.session?.id;
    this.session = session;

    const step = session.step;
    const llmCfg = session.llm_config || {};
    const llmConfigured = !!(llmCfg.endpoint && llmCfg.model);
    const onTargetStep = AI_STEPS.has(step);

    // Reset dismiss when the step changes.
    if (step !== this._dismissedOnStep) {
      this._dismissed = false;
    }

    if (onTargetStep && llmConfigured && !this._dismissed) {
      // Check whether a new import arrived (should re-trigger LLM stream).
      const importCount = (session.zro_imports || []).length;
      const newImport = importCount > this._importCount;

      // Open (or re-open) the LLM stream when:
      //  - session ID changed (new session), or
      //  - this is the first time we're on this step, or
      //  - a new import was detected (new LLM call in-flight).
      if (!this._llmEs || prevId !== session.id || newImport) {
        this._importCount = importCount;
        if (newImport) {
          // Clear previous insight so the spinner shows for the new call.
          this._insight = null;
          this._error = null;
        }
        this._openLlmStream(session.id);
      }
      this._render(true);
    } else {
      this._closeLlmStream();
      this._render(false);
    }
  }

  // -------------------------------------------------------------------------
  // LLM SSE stream
  // -------------------------------------------------------------------------

  _openLlmStream(sid) {
    this._closeLlmStream();
    this._loading = true;
    this._clearSpinnerTimer();
    this._spinnerTimer = setTimeout(() => {
      // If we haven't received anything, stop the spinner gracefully.
      if (this._loading) {
        this._loading = false;
        this._render(true);
      }
    }, SPINNER_TIMEOUT_MS);

    const es = new EventSource(`/api/session/${sid}/llm/stream`);

    es.addEventListener('llm_insight', (e) => {
      try {
        const data = JSON.parse(e.data);
        // data is {phase, text, timestamp}
        this._insight = typeof data === 'object' ? data : { text: data, phase: '', timestamp: '' };
      } catch (_) {
        this._insight = { text: String(e.data), phase: '', timestamp: '' };
      }
      this._loading = false;
      this._clearSpinnerTimer();
      this._render(true);
    });

    es.addEventListener('llm_error', (e) => {
      try {
        this._error = JSON.parse(e.data);
      } catch (_) {
        this._error = String(e.data);
      }
      this._loading = false;
      this._clearSpinnerTimer();
      this._render(true);
    });

    es.onerror = () => {
      // Connection error — stop spinner but keep panel visible.
      this._loading = false;
      this._clearSpinnerTimer();
      this._render(true);
    };

    this._llmEs = es;
  }

  _closeLlmStream() {
    if (this._llmEs) {
      this._llmEs.close();
      this._llmEs = null;
    }
    this._clearSpinnerTimer();
    this._loading = false;
  }

  _clearSpinnerTimer() {
    if (this._spinnerTimer !== null) {
      clearTimeout(this._spinnerTimer);
      this._spinnerTimer = null;
    }
  }

  // -------------------------------------------------------------------------
  // DOM creation and rendering
  // -------------------------------------------------------------------------

  _createElement() {
    const el = document.createElement('div');
    el.id = 'ai-insight-panel';
    el.style.display = 'none';
    return el;
  }

  _render(visible) {
    if (!visible) {
      this._el.style.display = 'none';
      return;
    }
    this._el.style.display = 'block';

    const isCollapsed = this._collapsed;
    const hasInsight = !!this._insight;
    const hasError = !!this._error;
    const isLoading = this._loading;

    // ---- Body content ----
    let bodyHtml = '';
    if (isLoading && !hasInsight) {
      bodyHtml = `
        <div class="ai-panel-spinner-wrap">
          <span class="ai-panel-spinner"></span>
          <span class="ai-panel-spinner-label">Analysing measurements…</span>
        </div>`;
    } else if (hasError) {
      bodyHtml = `
        <div class="ai-panel-error">
          <strong>Analysis error</strong>
          <p>${escapeHtml(String(this._error))}</p>
          <button class="ai-panel-retry-btn">Retry</button>
        </div>`;
    } else if (hasInsight) {
      const { text = '', phase = '', timestamp = '' } = this._insight;
      const phaseLabel = phase ? `<span class="ai-panel-phase">${escapeHtml(phase.replace(/_/g, ' '))}</span>` : '';
      const timeLabel = timestamp
        ? `<span class="ai-panel-time">${escapeHtml(new Date(timestamp).toLocaleTimeString())}</span>`
        : '';
      const meta = (phaseLabel || timeLabel)
        ? `<div class="ai-panel-meta">${phaseLabel}${timeLabel}</div>` : '';
      bodyHtml = `
        ${meta}
        <div class="ai-panel-markdown">${markdownToHtml(text)}</div>`;
    } else {
      bodyHtml = `
        <div class="ai-panel-spinner-wrap">
          <span class="ai-panel-spinner-label">Waiting for LLM analysis…</span>
        </div>`;
    }

    this._el.innerHTML = `
      <div class="ai-panel-header">
        <div class="ai-panel-title">
          <span class="ai-panel-icon">✦</span>
          AI Calibration Coach
        </div>
        <div class="ai-panel-actions">
          <button class="ai-panel-toggle" aria-label="${isCollapsed ? 'Expand' : 'Collapse'}" title="${isCollapsed ? 'Expand' : 'Collapse'}">
            ${isCollapsed ? '▲' : '▼'}
          </button>
          <button class="ai-panel-dismiss" aria-label="Dismiss" title="Dismiss panel">✕</button>
        </div>
      </div>
      ${isCollapsed ? '' : `<div class="ai-panel-body">${bodyHtml}</div>`}
    `;

    // Wire up buttons.
    this._el.querySelector('.ai-panel-toggle').addEventListener('click', () => {
      this._collapsed = !this._collapsed;
      this._render(true);
    });

    this._el.querySelector('.ai-panel-dismiss').addEventListener('click', () => {
      this._dismissed = true;
      this._dismissedOnStep = this.session?.step || null;
      this._closeLlmStream();
      this._render(false);
    });

    const retryBtn = this._el.querySelector('.ai-panel-retry-btn');
    if (retryBtn) {
      retryBtn.addEventListener('click', () => {
        this._error = null;
        this._insight = null;
        if (this.session?.id) {
          this._openLlmStream(this.session.id);
        }
        this._render(true);
      });
    }
  }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function escapeHtml(str) {
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// ---------------------------------------------------------------------------
// Bootstrap
// ---------------------------------------------------------------------------

function init() {
  new AiInsightPanel();
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}
