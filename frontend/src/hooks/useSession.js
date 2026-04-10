import { useState, useEffect, useRef, useCallback } from 'react';
import { api } from '../api/client';

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

  const sseRef = useRef(null);
  const sseRetryRef = useRef(null);
  const watchPollRef = useRef(null);

  // Initial load
  useEffect(() => {
    Promise.all([api.getSession(), api.getProfiles()])
      .then(([sess, profs]) => {
        setSession(sess);
        setProfiles(profs);
        if (sess?.id) startSSE(sess.id);
      })
      .catch(() => {})
      .finally(() => setLoading(false));

    // Load persisted bridge URL (default to localhost where the bridge normally runs)
    const saved = localStorage.getItem('bridgeUrl') || 'http://localhost:7070';
    setBridgeUrl(saved);
    api.saveBridgeUrl(saved).catch(() => {});

    // Load persisted watch path
    const savedWatch = localStorage.getItem('watchPath') || '';
    setWatchDefaultPath(savedWatch);

    return () => {
      clearTimeout(sseRetryRef.current);
      sseRef.current?.close();
      clearInterval(watchPollRef.current);
    };
  }, []);

  function startSSE(sid) {
    clearTimeout(sseRetryRef.current);
    sseRef.current?.close();

    let retryDelay = 1000;

    function connect() {
      const es = new EventSource(`/events/${sid}`);
      es.addEventListener('session', e => {
        try { setSession(JSON.parse(e.data)); } catch {}
      });
      es.addEventListener('watch_status', e => {
        try { setWatchStatus(JSON.parse(e.data)); } catch {}
      });
      es.onerror = () => {
        es.close();
        sseRetryRef.current = setTimeout(() => {
          retryDelay = Math.min(retryDelay * 2, 30000);
          connect();
        }, retryDelay);
      };
      sseRef.current = es;
    }

    connect();
  }

  const refreshWatchStatus = useCallback(async () => {
    try { setWatchStatus(await api.getWatchStatus()); } catch {}
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
    watchPollRef.current = setInterval(refreshWatchStatus, 5000);
    refreshWatchStatus();
    return () => clearInterval(watchPollRef.current);
  }, [refreshWatchStatus]);

  // Bridge polling
  useEffect(() => {
    if (!bridgeUrl) return;
    refreshBridgeStatus(bridgeUrl);
    const t = setInterval(() => refreshBridgeStatus(bridgeUrl), 8000);
    return () => clearInterval(t);
  }, [bridgeUrl, refreshBridgeStatus]);

  useEffect(() => {
    refreshDogegenStatus();
    const t = setInterval(refreshDogegenStatus, 8000);
    return () => clearInterval(t);
  }, [refreshDogegenStatus]);

  useEffect(() => {
    if (!dogegenStatus?.running || dogegenStatus?.ready) return;
    const t = setTimeout(() => refreshDogegenStatus(), 500);
    return () => clearTimeout(t);
  }, [dogegenStatus?.running, dogegenStatus?.ready, refreshDogegenStatus]);

  const reload = useCallback(async () => {
    try { setSession(await api.getSession()); } catch {}
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
  }

  async function setGrayscaleRamp(rampSteps) {
    if (!session?.id) return;
    setSession(await api.setGrayscaleRamp(session.id, rampSteps));
  }

  async function setCodeScale(codeScale) {
    if (!session?.id) return;
    setSession(await api.setCodeScale(session.id, codeScale));
  }

  async function setPatternGenerator(generator) {
    if (!session?.id) return;
    setSession(await api.setPatternGenerator(session.id, generator));
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
    localStorage.setItem('watchPath', path);
    setWatchDefaultPath(path);
    const status = await api.startWatch(session.id, path);
    setWatchStatus(status);
  }

  async function stopWatch() {
    const status = await api.stopWatch();
    setWatchStatus(status);
  }

  async function saveBridgeUrl(url) {
    localStorage.setItem('bridgeUrl', url);
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

    const deadline = Date.now() + 5000;
    while (Date.now() < deadline) {
      await new Promise(resolve => setTimeout(resolve, 250));
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

  return {
    session, profiles, watchStatus, watchDefaultPath, bridgeUrl, bridgeStatus, dogegenStatus, adbStatus, loading,
    createSession, deleteSession, nextStep, prevStep, jumpToStep, confirmMode, confirmPrepared, setGammaWorkflow, setSignalRange, setGrayscaleRamp, setCodeScale, setPatternGenerator, setLightspaceTier,
    uploadCsv, startWatch, stopWatch,
    saveBridgeUrl, triggerMeasure, refreshBridgeStatus,
    saveDogegenConfig, startDogegen, stopDogegen, refreshDogegenStatus,
    adbDeploy, adbApply, adbReset, adbSetPicture, adbGetPicture, refreshAdbStatus,
    configureLlm, getLlmStatus,
    reload,
  };
}
