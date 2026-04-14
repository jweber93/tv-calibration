import { useState, useEffect, useRef, useCallback } from 'react';
import { api } from '../api/client';

const SSE_RETRY_DELAY_MS = 1000;
const SSE_RETRY_MAX_DELAY_MS = 30000;
const LLM_SSE_RETRY_DELAY_MS = 2000;
const WATCH_POLL_INTERVAL_MS = 5000;
const BRIDGE_POLL_INTERVAL_MS = 8000;
const DOGEGEN_POLL_INTERVAL_MS = 8000;
const DOGEGEN_READY_RETRY_MS = 500;
const DOGEGEN_STARTUP_TIMEOUT_MS = 5000;
const DOGEGEN_POLL_INTERVAL_MS_MIN = 250;

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

export function useSession() {
  const [session, setSession] = useState(null);
  const [profiles, setProfiles] = useState([]);
  const [watchStatus, setWatchStatus] = useState({ watching: false, path: null, error: null, last_import: null });
  const [bridgeUrl, setBridgeUrl] = useState('');
  const [watchDefaultPath, setWatchDefaultPath] = useState('');
  const [bridgeStatus, setBridgeStatus] = useState(null);
  const [dogegenStatus, setDogegenStatus] = useState(null);
  const [adbStatus, setAdbStatus] = useState(null);
  const [loading, setLoading] = useState(true);

  // LLM insight state
  const [llmInsight, setLlmInsight] = useState(null);   // { text, phase, timestamp }
  const [llmStreaming, setLlmStreaming] = useState(false);
  const [llmError, setLlmError] = useState(null);

  const sseRef = useRef(null);
  const sseRetryRef = useRef(null);
  const watchPollRef = useRef(null);
  const llmEsRef = useRef(null);
  const llmEsRetryRef = useRef(null);

  function stopLlmSSE() {
    clearTimeout(llmEsRetryRef.current);
    llmEsRef.current?.close();
    llmEsRef.current = null;
  }

  function startLlmSSE(sid) {
    stopLlmSSE();
    let retryDelay = LLM_SSE_RETRY_DELAY_MS;

    function connect() {
      console.log('[LLM] connecting to stream', sid);
      const es = new EventSource(`/api/session/${sid}/llm/stream`);
      es.addEventListener('llm_start', e => {
        console.log('[LLM] started', JSON.parse(e.data));
        setLlmStreaming(true);
        setLlmError(null);
      });
      es.addEventListener('llm_insight', e => {
        try {
          const data = JSON.parse(e.data);
          console.log('[LLM] insight received', data.phase, data.text?.slice(0, 80));
          setLlmInsight(data);
        } catch (err) {
          console.warn('[LLM] malformed insight payload', e.data, err);
        }
        setLlmStreaming(false);
      });
      es.addEventListener('patch_strategy', e => {
        console.log('[LLM] patch_strategy received', JSON.parse(e.data));
        setLlmStreaming(false);
      });
      es.addEventListener('llm_error', e => {
        const msg = (() => { try { return JSON.parse(e.data); } catch { return String(e.data); } })();
        console.error('[LLM] error received', msg);
        setLlmError(msg);
        setLlmStreaming(false);
      });
      es.onerror = err => {
        console.warn('[LLM] SSE connection error — retrying in', retryDelay, 'ms', err);
        es.close();
        llmEsRetryRef.current = setTimeout(() => {
          retryDelay = Math.min(retryDelay * 2, SSE_RETRY_MAX_DELAY_MS);
          connect();
        }, retryDelay);
      };
      llmEsRef.current = es;
    }

    connect();
  }

  function startSSE(sid) {
    clearTimeout(sseRetryRef.current);
    sseRef.current?.close();

    let retryDelay = SSE_RETRY_DELAY_MS;

    function connect() {
      const es = new EventSource(`/events/${sid}`);
      es.addEventListener('session', e => {
        try { setSession(JSON.parse(e.data)); } catch { /* malformed SSE payload */ }
      });
      es.addEventListener('watch_status', e => {
        try { setWatchStatus(JSON.parse(e.data)); } catch { /* malformed SSE payload */ }
      });
      es.onerror = () => {
        es.close();
        sseRetryRef.current = setTimeout(() => {
          retryDelay = Math.min(retryDelay * 2, SSE_RETRY_MAX_DELAY_MS);
          connect();
        }, retryDelay);
      };
      sseRef.current = es;
    }

    connect();
  }

  // Initial load
  useEffect(() => {
    Promise.all([api.getSession(), api.getProfiles(), api.getPrefs()])
      .then(([sess, profs, prefs]) => {
        setSession(sess);
        setProfiles(profs);
        if (sess?.id) startSSE(sess.id);
        const url = prefs?.bridge_url || 'http://localhost:7070';
        setBridgeUrl(url);
        api.saveBridgeUrl(url).catch(() => { /* best-effort sync */ });
        setWatchDefaultPath(prefs?.watch_folder || '');
      })
      .catch(() => { /* load failure is non-fatal */ })
      .finally(() => setLoading(false));

    return () => {
      clearTimeout(sseRetryRef.current);
      sseRef.current?.close();
      clearInterval(watchPollRef.current);
      stopLlmSSE();
    };
  }, []);
  const refreshWatchStatus = useCallback(async () => {
    try { setWatchStatus(await api.getWatchStatus()); } catch { /* poll failure is non-fatal */ }
  }, []);

  const refreshBridgeStatus = useCallback(async (url = bridgeUrl) => {
    if (!url) return;
    try { setBridgeStatus(await api.getBridgeStatus(url)); } catch { setBridgeStatus(null); }
  }, [bridgeUrl]);

  const refreshAdbStatus = useCallback(async () => {
    try { setAdbStatus(await api.getAdbStatus()); } catch { setAdbStatus(null); }
  }, []);

  const refreshDogegenStatus = useCallback(async () => {
    try { setDogegenStatus(await api.getDogegenStatus()); } catch { setDogegenStatus(null); }
  }, []);

  // Polling for watch status when SSE not available
  useEffect(() => {
    clearInterval(watchPollRef.current);
    watchPollRef.current = setInterval(refreshWatchStatus, WATCH_POLL_INTERVAL_MS);
    refreshWatchStatus();
    return () => clearInterval(watchPollRef.current);
  }, [refreshWatchStatus]);

  // Bridge polling
  useEffect(() => {
    if (!bridgeUrl || !session?.id) return;
    refreshBridgeStatus(bridgeUrl);
      const t = setInterval(() => refreshBridgeStatus(bridgeUrl), BRIDGE_POLL_INTERVAL_MS);
    return () => clearInterval(t);
  }, [bridgeUrl, refreshBridgeStatus, session?.id]);

  // LLM stream — connect when session has LLM configured, disconnect otherwise
  useEffect(() => {
    const sid = session?.id;
    const llmCfg = session?.llm_config || {};
    if (sid && llmCfg.endpoint && llmCfg.model) {
      startLlmSSE(sid);
    } else {
      stopLlmSSE();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session?.id, session?.llm_config?.endpoint, session?.llm_config?.model]);

  useEffect(() => {
    if (!session?.id) return;
    refreshDogegenStatus();
    const t = setInterval(refreshDogegenStatus, DOGEGEN_POLL_INTERVAL_MS);
    return () => clearInterval(t);
  }, [refreshDogegenStatus, session?.id]);

  useEffect(() => {
    if (!dogegenStatus?.running || dogegenStatus?.ready) return;
    const t = setTimeout(() => refreshDogegenStatus(), DOGEGEN_READY_RETRY_MS);
    return () => clearTimeout(t);
  }, [dogegenStatus?.running, dogegenStatus?.ready, refreshDogegenStatus]);

  const reload = useCallback(async () => {
    try { setSession(await api.getSession()); } catch { /* reload failure is non-fatal */ }
  }, []);

  async function deleteSession() {
    if (!session?.id) return;
    await api.deleteSession(session.id);
    sseRef.current?.close();
    setSession(null);
  }

  async function createSession(tv, mode, sdrPeakNits) {
    const sess = await api.createSession(tv, mode, sdrPeakNits);
    setSession(sess);
    startSSE(sess.id);
    return sess;
  }

  async function nextStep() {
    if (!session?.id) return;
    setSession(await api.nextStep(session.id));
  }

  async function prevStep() {
    if (!session?.id) return;
    setSession(await api.prevStep(session.id));
  }

  async function jumpToStep(stepIndex) {
    if (!session?.id) return;
    setSession(await api.jumpToStep(session.id, stepIndex));
  }

  async function confirmMode(mode, sdrPeakNits) {
    if (!session?.id) return;
    setSession(await api.confirmMode(session.id, mode, sdrPeakNits));
  }

  async function confirmPrepared() {
    if (!session?.id) return;
    setSession(await api.confirmPrepared(session.id));
  }

  async function setGammaWorkflow(workflow) {
    if (!session?.id) return;
    setSession(await api.setGammaWorkflow(session.id, workflow));
  }

  async function setSignalRange(range) {
    if (!session?.id) return;
    setSession(await api.setSignalRange(session.id, range));
    api.savePrefs({ signal_range: range }).catch(() => {});
  }

  async function setGrayscaleRamp(rampSteps) {
    if (!session?.id) return;
    setSession(await api.setGrayscaleRamp(session.id, rampSteps));
  }

  async function setCodeScale(codeScale) {
    if (!session?.id) return;
    setSession(await api.setCodeScale(session.id, codeScale));
    api.savePrefs({ code_scale: codeScale }).catch(() => {});
  }

  async function setPatternGenerator(generator) {
    if (!session?.id) return;
    setSession(await api.setPatternGenerator(session.id, generator));
    api.savePrefs({ pattern_generator: generator }).catch(() => {});
  }

  async function setLightspaceTier(tier, rampSteps) {
    if (!session?.id) return;
    setSession(await api.setLightspaceTier(session.id, tier, rampSteps));
  }

  async function uploadCsv(file) {
    if (!session?.id) return null;
    const result = await api.uploadCsv(session.id, file);
    await reload();
    return result;
  }

  async function startWatch(path) {
    if (!session?.id) return;
    setWatchDefaultPath(path);
    api.savePrefs({ watch_folder: path }).catch(() => {});
    const status = await api.startWatch(session.id, path);
    setWatchStatus(status);
  }

  async function stopWatch() {
    const status = await api.stopWatch();
    setWatchStatus(status);
  }

  async function saveBridgeUrl(url) {
    trySetLocalStorage('bridgeUrl', url);
    setBridgeUrl(url);
    await api.saveBridgeUrl(url);
    await refreshBridgeStatus(url);
  }

  async function ensureDogegenReady() {
    if (session?.pattern_generator !== 'dogegen') return null;
    if (!session?.id) throw new Error('Create a session before launching Dogegen.');

    let status = dogegenStatus;
    if (!status?.running) {
      status = await api.startDogegen(session.id);
      setDogegenStatus(status);
    }

    if (status?.ready) return status;

    const deadline = Date.now() + DOGEGEN_STARTUP_TIMEOUT_MS;
    while (Date.now() < deadline) {
      await new Promise(resolve => setTimeout(resolve, DOGEGEN_POLL_INTERVAL_MS_MIN));
      status = await api.getDogegenStatus();
      setDogegenStatus(status);
      if (status?.ready) return status;
    }

    throw new Error('Dogegen is still starting up. Wait a moment and try Measure again.');
  }

  async function triggerMeasure() {
    await ensureDogegenReady();
    return api.triggerMeasure(bridgeUrl);
  }

  async function saveDogegenConfig(config) {
    const status = await api.saveDogegenConfig(config);
    setDogegenStatus(status);
    return status;
  }

  async function startDogegen() {
    if (!session?.id) return null;
    const status = await api.startDogegen(session.id);
    setDogegenStatus(status);
    return status;
  }

  async function stopDogegen() {
    const status = await api.stopDogegen();
    setDogegenStatus(status);
    return status;
  }

  async function adbDeploy() {
    const result = await api.adbDeploy();
    await refreshAdbStatus();
    return result;
  }

  async function adbApply(channel, control, value) {
    await api.adbApply(channel, control, value);
    await reload();
  }

  async function adbReset() {
    await api.adbReset();
    await reload();
  }

  async function adbSetPicture(control, value) {
    await api.adbSetPicture(control, value);
  }

  async function adbGetPicture(control) {
    return await api.adbGetPicture(control);
  }

  async function configureLlm(payload) {
    if (!session?.id) return;
    return api.configureLlm(session.id, payload);
  }

  async function getLlmStatus() {
    if (!session?.id) return null;
    return api.getLlmStatus(session.id);
  }

  async function saveTvSettings(payload) {
    if (!session?.id) return;
    return api.saveTvSettings(session.id, payload);
  }

  function dismissLlmInsight() {
    setLlmInsight(null);
    setLlmError(null);
  }

  return {
    session, profiles, watchStatus, watchDefaultPath, bridgeUrl, bridgeStatus, dogegenStatus, adbStatus, loading,
    createSession, deleteSession, nextStep, prevStep, jumpToStep, confirmMode, confirmPrepared, setGammaWorkflow, setSignalRange, setGrayscaleRamp, setCodeScale, setPatternGenerator, setLightspaceTier,
    uploadCsv, startWatch, stopWatch,
    saveBridgeUrl, triggerMeasure, refreshBridgeStatus,
    saveDogegenConfig, startDogegen, stopDogegen, refreshDogegenStatus,
    adbDeploy, adbApply, adbReset, adbSetPicture, adbGetPicture, refreshAdbStatus,
    configureLlm, getLlmStatus, saveTvSettings,
    llmInsight, llmStreaming, llmError, dismissLlmInsight,
    reload,
  };
}
