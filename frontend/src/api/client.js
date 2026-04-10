async function request(method, path, body) {
  const opts = { method, headers: { 'Content-Type': 'application/json' } };
  if (body !== undefined) opts.body = JSON.stringify(body);
  const r = await fetch(path, opts);
  if (!r.ok) {
    const err = await r.json().catch(() => ({ detail: r.statusText }));
    throw new Error(err.detail || r.statusText);
  }
  return r.json();
}

async function upload(path, formData) {
  const r = await fetch(path, { method: 'POST', body: formData });
  if (!r.ok) {
    const err = await r.json().catch(() => ({ detail: r.statusText }));
    throw new Error(err.detail || r.statusText);
  }
  return r.json();
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

  // LLM
  configureLlm:  (sid, body)  => api.post(`/api/session/${sid}/llm/configure`, body),
  getLlmStatus:  (sid)        => api.get(`/api/session/${sid}/llm/status`),

  // Report
  getReport:     (sid)        => api.get(`/api/session/${sid}/report`),
  downloadReport: (sid)       => `/api/session/${sid}/report/download`,
};
