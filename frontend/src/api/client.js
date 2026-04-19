const DEFAULT_TIMEOUT_MS = 30000;

async function request(method, path, body, timeout = DEFAULT_TIMEOUT_MS) {
  const opts = { method, headers: { 'Content-Type': 'application/json' } };
  if (body !== undefined) opts.body = JSON.stringify(body);
  
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeout);
  opts.signal = controller.signal;
  
  try {
    const r = await fetch(path, opts);
    clearTimeout(timeoutId);
    if (!r.ok) {
      const err = await r.json().catch(() => ({ detail: r.statusText }));
      throw new Error(err.detail || r.statusText);
    }
    return r.json();
  } catch (err) {
    clearTimeout(timeoutId);
    if (err.name === 'AbortError') {
      throw new Error(`Request timed out after ${timeout}ms`);
    }
    throw err;
  }
}

async function upload(path, formData, timeout = DEFAULT_TIMEOUT_MS) {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeout);
  
  try {
    const r = await fetch(path, { method: 'POST', body: formData, signal: controller.signal });
    clearTimeout(timeoutId);
    if (!r.ok) {
      const err = await r.json().catch(() => ({ detail: r.statusText }));
      throw new Error(err.detail || r.statusText);
    }
    return r.json();
  } catch (err) {
    clearTimeout(timeoutId);
    if (err.name === 'AbortError') {
      throw new Error(`Upload timed out after ${timeout}ms`);
    }
    throw err;
  }
}

export const api = {
  get:    (path)        => request('GET',    path),
  post:   (path, body)  => request('POST',   path, body),
  delete: (path)        => request('DELETE', path),
  upload: (path, form)  => upload(path, form),

  // Session
  getSession:    ()           => api.get('/api/session'),
  deleteSession: (sid)        => api.delete(`/api/session/${sid}`),
  createSession: (tv, mode, sdrPeakNits) =>
    api.post('/api/session', { tv, mode, sdr_peak_nits: sdrPeakNits }),
  nextStep:      (sid)        => api.post(`/api/session/${sid}/next`),
  prevStep:      (sid)        => api.post(`/api/session/${sid}/prev`),
  jumpToStep:    (sid, step_index) => api.post(`/api/session/${sid}/jump`, { step_index }),
  confirmMode:   (sid, mode, sdrPeakNits) =>
    api.post(`/api/session/${sid}/mode`, { mode, sdr_peak_nits: sdrPeakNits }),
  confirmPrepared: (sid)      => api.post(`/api/session/${sid}/prepared`),
  setGammaWorkflow: (sid, workflow) =>
    api.post(`/api/session/${sid}/gamma/workflow`, { workflow }),
  setSignalRange: (sid, signal_range) =>
    api.post(`/api/session/${sid}/signal-range`, { signal_range }),
  setGrayscaleRamp: (sid, ramp_steps) =>
    api.post(`/api/session/${sid}/grayscale-ramp`, { ramp_steps }),
  setCodeScale: (sid, code_scale) =>
    api.post(`/api/session/${sid}/code-scale`, { code_scale }),
  setPatternGenerator: (sid, pattern_generator) =>
    api.post(`/api/session/${sid}/pattern-generator`, { pattern_generator }),
  setLightspaceTier: (sid, tier, rampSteps) =>
    api.post(`/api/session/${sid}/lightspace-tier`, { tier, ramp_steps: rampSteps }),
  getProfiles:   ()           => api.get('/api/profiles'),

  // Import
  uploadCsv:     (sid, file)  => {
    const form = new FormData();
    form.append('file', file);
    return api.upload(`/api/session/${sid}/import/zro`, form);
  },

  // Watch
  startWatch:    (sid, path)  => api.post(`/api/session/${sid}/watch`, { path }),
  stopWatch:     ()           => api.post('/api/watch/stop'),
  getWatchStatus: ()          => api.get('/api/watch/status'),

  // Bridge
  getBridgeStatus: (url)      => api.get(`/api/bridge/status?url=${encodeURIComponent(url)}`),
  saveBridgeUrl:   (url)      => api.post('/api/bridge/url', { url }),
  triggerMeasure:  (url)      => api.post('/api/bridge/measure', { url }),

  // Dogegen
  getDogegenStatus: ()        => api.get('/api/dogegen/status'),
  saveDogegenConfig: (body)   => api.post('/api/dogegen/config', body),
  startDogegen: (sid)         => api.post(`/api/session/${sid}/dogegen/start`),
  stopDogegen: ()             => api.post('/api/dogegen/stop'),

  // ADB — CMS (per-channel colour tuner)
  getAdbStatus:  ()           => api.get('/api/adb/status'),
  adbDeploy:     ()           => api.post('/api/adb/cms/push'),
  adbApply:      (channel, control, value) =>
    api.post('/api/adb/cms/set', { channel, control, value }),
  adbReset:      ()           => api.post('/api/adb/cms/reset'),

  // ADB — main picture controls (Brightness, Contrast, Saturation, PictureMode)
  adbSetPicture: (control, value, device) =>
    api.post('/api/adb/picture/set', { control, value, device }),
  adbGetPicture: (control, device) =>
    api.post('/api/adb/picture/get', { control, device }),

  // Prefs
  getPrefs:      ()           => api.get('/api/prefs'),
  savePrefs:     (body)       => api.post('/api/prefs', body),

  // LLM
  configureLlm:  (sid, body)  => api.post(`/api/session/${sid}/llm/configure`, body),
  getLlmStatus:  (sid)        => api.get(`/api/session/${sid}/llm/status`),
  saveTvSettings: (sid, body) => api.post(`/api/session/${sid}/tv-settings`, body),

  // Suggested patches
  getSuggestedPatches: (sid, budget) => {
    const url = new URL(`/api/session/${sid}/suggested-patches`, window.location.origin);
    if (budget) url.searchParams.set('budget', budget);
    return api.get(url.toString());
  },
  runSuggestedPatches: (sid, patches) =>
    api.post(`/api/session/${sid}/suggested-patches/run`, { patches }),

  // Report
  getReport:     (sid)        => api.get(`/api/session/${sid}/report`),
  downloadPdf:   async (sid, tv) => {
    const r = await fetch(`/api/session/${sid}/report/pdf`);
    if (!r.ok) {
      const err = await r.json().catch(() => ({ detail: r.statusText }));
      throw new Error(err.detail || r.statusText);
    }
    const blob = await r.blob();
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement('a');
    const slug = (tv || 'calibration').replace(/[^a-z0-9]/gi, '_');
    a.href = url; a.download = `${slug}_${sid.slice(0, 8)}.pdf`; a.click();
    URL.revokeObjectURL(url);
  },

  // Comparison (#168)
  getReportHistory:  (tvKey, limit) => {
    const url = new URL(`/api/report/history/${tvKey}`, window.location.origin);
    if (limit) url.searchParams.set('limit', limit);
    return api.get(url.toString());
  },
  getCompare:        (a, b, format) => {
    const url = new URL(`/api/report/compare`, window.location.origin);
    url.searchParams.set('a', a);
    url.searchParams.set('b', b);
    if (format) url.searchParams.set('format', format);
    return api.get(url.toString());
  },
  getCompareDeltaSummary: (a, b) => api.post(`/api/report/compare/delta_summary?a=${a}&b=${b}`),
  downloadComparePdf:    async (a, b) => {
    const r = await fetch(`/api/report/compare?a=${a}&b=${b}&format=pdf`);
    if (!r.ok) {
      const err = await r.json().catch(() => ({ detail: r.statusText }));
      throw new Error(err.detail || r.statusText);
    }
    const blob = await r.blob();
    const url  = URL.createObjectURL(blob);
    const aEl  = document.createElement('a');
    aEl.href = url; aEl.download = `comparison_${a.slice(0,8)}_vs_${b.slice(0,8)}.pdf`; aEl.click();
    URL.revokeObjectURL(url);
  },
};
