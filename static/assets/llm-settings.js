/**
 * LlmSettingsSection — native ES module (no build step required).
 *
 * Injects an "AI Assistant" settings card into the Setup (prepare) page
 * so users can configure the optional LLM endpoint for AI coaching.
 *
 * Behaviour:
 *  - Only visible when session.step === "prepare"
 *  - Uses MutationObserver to survive React re-renders
 *  - Saves endpoint/model on blur via POST /api/session/{sid}/llm/configure
 *  - API key is write-only: saved on blur then cleared from the input
 *  - "Test Connection" calls GET /api/session/{sid}/llm/status
 *  - Toggling off POSTs empty endpoint+model to clear the session config
 */

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function escapeHtml(str) {
  if (!str) return '';
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// ---------------------------------------------------------------------------
// LlmSettingsSection
// ---------------------------------------------------------------------------

class LlmSettingsSection {
  constructor() {
    /** @type {object|null} Latest session from the API */
    this.session = null;
    /** @type {EventSource|null} */
    this._sessionEs = null;

    // Form state — persisted across React re-renders
    this._endpoint = '';
    this._model = '';
    this._enabled = false;

    /** @type {null|'testing'|'ok'|'unreachable'|'error'} */
    this._testStatus = null;

    // Guard against re-entrant injection triggered by our own DOM mutations
    this._injecting = false;

    this._startObserver();
    this._initSession();
  }

  // -------------------------------------------------------------------------
  // Session initialisation + SSE stream
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
    const wasNull = !this.session;
    const prevId = this.session?.id;
    const prevStep = this.session?.step;
    this.session = session;

    // Populate form from session on first load, session change, or when
    // returning to the prepare step (so we show the last-saved values).
    if (wasNull || prevId !== session.id || (prevStep !== 'prepare' && session.step === 'prepare')) {
      const llmCfg = session.llm_config || {};
      this._endpoint = llmCfg.endpoint || '';
      this._model = llmCfg.model || '';
      this._enabled = !!(llmCfg.endpoint && llmCfg.model);
      this._testStatus = null;
    }

    this._ensureInjected();
  }

  // -------------------------------------------------------------------------
  // MutationObserver — re-inject after React re-renders
  // -------------------------------------------------------------------------

  _startObserver() {
    const observer = new MutationObserver(() => {
      if (this._injecting) return;
      this._ensureInjected();
    });
    observer.observe(document.body, { childList: true, subtree: true });
    this._observer = observer;
  }

  // -------------------------------------------------------------------------
  // DOM injection
  // -------------------------------------------------------------------------

  _ensureInjected() {
    if (!this.session) return;

    if (this.session.step !== 'prepare') {
      // Remove our card if we navigated away from the prepare step.
      const existing = document.getElementById('llm-settings-section');
      if (existing) existing.remove();
      return;
    }

    // Already in the DOM — nothing to do.
    if (document.getElementById('llm-settings-section')) return;

    // Find the .main container the React app renders into.
    const main = document.querySelector('.main');
    if (!main) return;

    this._injecting = true;
    try {
      const card = this._createCard();
      main.appendChild(card);
      this._wireEvents(card);
    } finally {
      this._injecting = false;
    }
  }

  _createCard() {
    const el = document.createElement('div');
    el.id = 'llm-settings-section';
    el.className = 'card';
    el.innerHTML = this._cardHtml();
    return el;
  }

  _cardHtml() {
    const ep = escapeHtml(this._endpoint);
    const model = escapeHtml(this._model);
    const checked = this._enabled ? 'checked' : '';

    let statusHtml = '';
    if (this._testStatus === 'testing') {
      statusHtml = `<span class="llm-status llm-status-testing">Testing\u2026</span>`;
    } else if (this._testStatus === 'ok') {
      statusHtml = `<span class="llm-status llm-status-ok">\u2713 Reachable</span>`;
    } else if (this._testStatus === 'unreachable') {
      statusHtml = `<span class="llm-status llm-status-err">\u2717 Unreachable</span>`;
    } else if (this._testStatus === 'error') {
      statusHtml = `<span class="llm-status llm-status-err">\u2717 Error</span>`;
    }

    return `
      <div class="card-title">
        <span>\u2736</span>
        AI Assistant
      </div>
      <div class="text-sm muted" style="margin-bottom:16px">
        Connect an OpenAI-compatible LLM (e.g. Ollama, LiteLLM) to get
        real-time coaching after each ZRO import. If unconfigured the rest of
        the workflow is unaffected.
      </div>
      <label class="llm-toggle-row">
        <input type="checkbox" id="llm-enable-toggle" ${checked}>
        <span class="llm-toggle-label">Enable AI Assistant</span>
      </label>
      <div id="llm-fields" style="display:${this._enabled ? 'flex' : 'none'};flex-direction:column;gap:12px">
        <div class="llm-field-row">
          <label class="llm-field-label" for="llm-endpoint">Endpoint URL</label>
          <input
            type="url"
            id="llm-endpoint"
            class="llm-input"
            value="${ep}"
            placeholder="http://localhost:11434/v1"
            autocomplete="off"
            spellcheck="false"
          >
        </div>
        <div class="llm-field-row">
          <label class="llm-field-label" for="llm-model">Model name</label>
          <input
            type="text"
            id="llm-model"
            class="llm-input"
            value="${model}"
            placeholder="llama3"
            autocomplete="off"
            spellcheck="false"
          >
        </div>
        <div class="llm-field-row">
          <label class="llm-field-label" for="llm-apikey">
            API key
            <span class="llm-field-hint">(optional \u2014 never shown)</span>
          </label>
          <input
            type="password"
            id="llm-apikey"
            class="llm-input"
            placeholder="sk-\u2026"
            autocomplete="new-password"
          >
        </div>
        <div class="btn-group" style="margin-top:2px">
          <button id="llm-test-btn" class="btn btn-sm">Test Connection</button>
          ${statusHtml}
        </div>
      </div>
    `;
  }

  _wireEvents(card) {
    const toggle = card.querySelector('#llm-enable-toggle');
    const fields = card.querySelector('#llm-fields');
    const epInput = card.querySelector('#llm-endpoint');
    const modelInput = card.querySelector('#llm-model');
    const apikeyInput = card.querySelector('#llm-apikey');
    const testBtn = card.querySelector('#llm-test-btn');

    // Enable/disable toggle
    toggle.addEventListener('change', async () => {
      this._enabled = toggle.checked;
      fields.style.display = this._enabled ? 'flex' : 'none';
      if (!this._enabled) {
        // Clear LLM config from session when disabled
        this._endpoint = '';
        this._model = '';
        this._testStatus = null;
        await this._configure({ endpoint: '', model: '' });
      }
    });

    // Save endpoint on blur
    epInput.addEventListener('blur', async () => {
      this._endpoint = epInput.value.trim();
      await this._configure({ endpoint: this._endpoint });
    });

    // Save model on blur
    modelInput.addEventListener('blur', async () => {
      this._model = modelInput.value.trim();
      await this._configure({ model: this._model });
    });

    // API key is write-only: save on blur then wipe the input
    apikeyInput.addEventListener('blur', async () => {
      const key = apikeyInput.value;
      if (key) {
        await this._configure({ api_key: key });
        apikeyInput.value = '';
      }
    });

    // Test Connection
    testBtn.addEventListener('click', async () => {
      this._testStatus = 'testing';
      this._rerender(card);
      try {
        const data = await this._testConnection();
        if (data.configured && data.reachable) {
          this._testStatus = 'ok';
        } else if (data.configured) {
          this._testStatus = 'unreachable';
        } else {
          this._testStatus = 'error';
        }
      } catch (_) {
        this._testStatus = 'error';
      }
      this._rerender(card);
    });
  }

  /** Re-render the card contents in-place, preserving typed-but-unsaved values. */
  _rerender(card) {
    const epInput = card.querySelector('#llm-endpoint');
    const modelInput = card.querySelector('#llm-model');
    if (epInput) this._endpoint = epInput.value;
    if (modelInput) this._model = modelInput.value;

    this._injecting = true;
    try {
      card.innerHTML = this._cardHtml();
      this._wireEvents(card);
    } finally {
      this._injecting = false;
    }
  }

  // -------------------------------------------------------------------------
  // API calls
  // -------------------------------------------------------------------------

  async _configure(payload) {
    if (!this.session?.id) return;
    try {
      await fetch(`/api/session/${this.session.id}/llm/configure`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
    } catch (_) { /* ignore — server may be unreachable */ }
  }

  async _testConnection() {
    const res = await fetch(`/api/session/${this.session.id}/llm/status`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  }
}

// ---------------------------------------------------------------------------
// Bootstrap
// ---------------------------------------------------------------------------

function init() {
  new LlmSettingsSection();
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}
