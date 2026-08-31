import { act, renderHook } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { useSession } from './useSession';
import { api } from '../api/client';

vi.mock('../api/client', () => ({
  api: {
    getSession: vi.fn(),
    getProfiles: vi.fn(),
    getPrefs: vi.fn(),
    saveBridgeUrl: vi.fn(),
    getWatchStatus: vi.fn(),
    getBridgeStatus: vi.fn(),
    getAdbStatus: vi.fn(),
    getDogegenStatus: vi.fn(),
  },
}));

class MockEventSource {
  constructor(url) {
    this.url = url;
    this.listeners = {};
    this.closeCallCount = 0;
    MockEventSource.instances.push(this);
  }
  addEventListener(type, cb) {
    (this.listeners[type] ||= []).push(cb);
  }
  close() { this.closeCallCount++; }
  emit(type, data) {
    (this.listeners[type] || []).forEach(cb => cb({ data: JSON.stringify(data) }));
  }
  emitRaw(type, raw) {
    // Bypass the JSON.stringify wrapper to dispatch a literal (possibly invalid) payload.
    (this.listeners[type] || []).forEach(cb => cb({ data: raw }));
  }
}
MockEventSource.instances = [];

const SID = 'test-session-llm';
const LLM_STREAM_URL = `/api/session/${SID}/llm/stream`;

describe('useSession LLM SSE streaming flag', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    MockEventSource.instances = [];
    globalThis.EventSource = MockEventSource;
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    vi.spyOn(console, 'log').mockImplementation(() => {});

    // Session with LLM configured so startLlmSSE() runs on load.
    api.getSession.mockResolvedValue({
      id: SID,
      llm_config: { endpoint: 'http://llm.example:8080/v1', model: 'test-model' },
    });
    api.getProfiles.mockResolvedValue([]);
    api.getPrefs.mockResolvedValue({});
    api.saveBridgeUrl.mockResolvedValue({});
    api.getWatchStatus.mockResolvedValue({ watching: false, path: null, error: null, last_import: null });
    api.getBridgeStatus.mockResolvedValue({});
    api.getAdbStatus.mockResolvedValue({});
    api.getDogegenStatus.mockResolvedValue({ running: false });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  async function waitForLlmStream() {
    await vi.waitFor(() => {
      const es = MockEventSource.instances.find(i => i.url === LLM_STREAM_URL);
      expect(es).toBeTruthy();
    });
    return MockEventSource.instances.find(i => i.url === LLM_STREAM_URL);
  }

  it('clears llmStreaming on an llm_insight event with empty text (issue #677)', async () => {
    const { result, unmount } = renderHook(() => useSession());
    try {
      const es = await waitForLlmStream();

      act(() => {
        es.emit('llm_start', { phase: 'gamma' });
      });
      expect(result.current.llmStreaming).toBe(true);

      // Backend broadcasts text: "" for an empty LLM completion (content-filtered,
      // truncated, or a provider that returns empty content).
      act(() => {
        es.emit('llm_insight', { phase: 'gamma', text: '' });
      });

      expect(result.current.llmStreaming).toBe(false);
      // The empty text is rejected as malformed, so no insight is stored.
      expect(result.current.llmInsight).toBeNull();
    } finally {
      unmount();
    }
  });

  it('still clears llmStreaming for a valid llm_insight payload', async () => {
    const { result, unmount } = renderHook(() => useSession());
    try {
      const es = await waitForLlmStream();

      act(() => {
        es.emit('llm_start', { phase: 'gamma' });
      });
      expect(result.current.llmStreaming).toBe(true);

      act(() => {
        es.emit('llm_insight', { phase: 'gamma', text: 'Gamma looks off around 50% signal.' });
      });

      expect(result.current.llmStreaming).toBe(false);
      expect(result.current.llmInsight).toMatchObject({
        phase: 'gamma',
        text: 'Gamma looks off around 50% signal.',
      });
    } finally {
      unmount();
    }
  });

  it('clears llmStreaming on an llm_error event', async () => {
    const { result, unmount } = renderHook(() => useSession());
    try {
      const es = await waitForLlmStream();

      act(() => {
        es.emit('llm_start', { phase: 'gamma' });
      });
      expect(result.current.llmStreaming).toBe(true);

      act(() => {
        es.emit('llm_error', { error: 'LLM request failed' });
      });

      expect(result.current.llmStreaming).toBe(false);
    } finally {
      unmount();
    }
  });

  it('clears llmStreaming and warns when the llm_insight payload is not valid JSON', async () => {
    const { result, unmount } = renderHook(() => useSession());
    try {
      const es = await waitForLlmStream();

      act(() => {
        es.emit('llm_start', { phase: 'gamma' });
      });
      expect(result.current.llmStreaming).toBe(true);

      // Dispatch a raw, non-JSON payload directly to the listener so the
      // try/catch in useSession runs and the finally block must clear the flag.
      act(() => {
        es.emitRaw('llm_insight', 'not-json{');
      });

      expect(result.current.llmStreaming).toBe(false);
      expect(result.current.llmInsight).toBeNull();
      expect(console.warn).toHaveBeenCalledWith(
        '[LLM] malformed insight payload',
        { eventData: 'not-json{' },
        expect.any(Error)
      );
    } finally {
      unmount();
    }
  });

  it('closes the LLM EventSource on unmount', async () => {
    const { unmount } = renderHook(() => useSession());
    const es = await waitForLlmStream();
    expect(es.closeCallCount).toBe(0);
    unmount();
    expect(es.closeCallCount).toBeGreaterThan(0);
  });
});
