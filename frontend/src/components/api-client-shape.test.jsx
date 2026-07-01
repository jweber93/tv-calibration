import { describe, it, expect } from 'vitest';

describe('api/client.js — real export shape', () => {
  it('exposes api as a named export with all LLM methods callable', async () => {
    const mod = await import('../api/client');
    expect(mod.api).toBeDefined();
    expect(typeof mod.api.getLlmStatus).toBe('function');
    expect(typeof mod.api.configureLlm).toBe('function');
    expect(typeof mod.api.probeLlm).toBe('function');
    expect(typeof mod.api.getLlmHistorySummary).toBe('function');
  });

  it('api object has no unexpected top-level exports (regression guard)', async () => {
    const mod = await import('../api/client');
    const keys = Object.keys(mod);
    expect(keys).toEqual(['api']);
  });
});
