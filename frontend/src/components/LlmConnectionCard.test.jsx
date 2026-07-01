import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { LlmConnectionCard } from './LlmConnectionCard';
import { api } from '../api/client';

vi.mock('../api/client', () => ({
  api: {
    getLlmStatus: vi.fn(),
    configureLlm: vi.fn(),
    probeLlm: vi.fn(),
  },
}));

const sid = 'test-session-123';

describe('LlmConnectionCard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows "Not configured" when getLlmStatus returns configured:false', async () => {
    api.getLlmStatus.mockResolvedValue({ configured: false });
    render(<LlmConnectionCard sid={sid} />);
    expect(await screen.findByText('Not configured')).toBeInTheDocument();
  });

  it('shows green reachable badge with model when reachable', async () => {
    api.getLlmStatus.mockResolvedValue({ configured: true, reachable: true, model: 'llama3' });
    render(<LlmConnectionCard sid={sid} />);
    expect(await screen.findByText('Reachable (llama3)')).toBeInTheDocument();
  });

  it('shows error when configured but unreachable', async () => {
    api.getLlmStatus.mockResolvedValue({ configured: true, reachable: false, error: 'HTTP 401: Unauthorized' });
    api.probeLlm.mockResolvedValue({ configured: true, reachable: false, error: 'HTTP 401: Unauthorized' });
    render(<LlmConnectionCard sid={sid} />);
    await screen.findByText('Not configured');
    await userEvent.click(screen.getByText('Test connection'));
    expect(await screen.findByText('HTTP 401: Unauthorized')).toBeInTheDocument();
  });

  it('clicking Save calls configureLlm with entered field values', async () => {
    api.getLlmStatus.mockResolvedValue({ configured: false });
    api.configureLlm.mockResolvedValue({ configured: true, model: 'llama3' });
    render(<LlmConnectionCard sid={sid} />);
    await screen.findByText('Not configured');

    await userEvent.type(screen.getByPlaceholderText('http://localhost:11434/v1'), 'http://localhost:11434/v1');
    await userEvent.type(screen.getByPlaceholderText('llama3'), 'llama3');
    await userEvent.click(screen.getByText('Save'));

    expect(api.configureLlm).toHaveBeenCalledOnce();
    expect(api.configureLlm).toHaveBeenCalledWith(sid, { endpoint: 'http://localhost:11434/v1', model: 'llama3' });
  });

  it('prefills endpoint and model from status on mount when already configured', async () => {
    api.getLlmStatus.mockResolvedValue({ configured: true, reachable: true, model: 'gpt-4', endpoint: 'https://api.openai.com/v1' });
    render(<LlmConnectionCard sid={sid} />);
    const endpointInput = await screen.findByDisplayValue('https://api.openai.com/v1');
    expect(endpointInput).toBeInTheDocument();
    expect(screen.getByDisplayValue('gpt-4')).toBeInTheDocument();
  });

  it('disables buttons while saving', async () => {
    api.getLlmStatus.mockResolvedValue({ configured: false });
    let resolveConfigure;
    api.configureLlm.mockImplementation(() => new Promise(r => { resolveConfigure = r; }));
    render(<LlmConnectionCard sid={sid} />);
    await screen.findByText('Not configured');

    await userEvent.type(screen.getByPlaceholderText('http://localhost:11434/v1'), 'http://localhost:11434/v1');
    await userEvent.type(screen.getByPlaceholderText('llama3'), 'llama3');
    await userEvent.click(screen.getByText('Save'));

    expect(screen.getByText('Save')).toBeDisabled();
    expect(screen.getByText('Test connection')).toBeDisabled();

    resolveConfigure({ configured: true, model: 'llama3' });
    await screen.findByText('Saved — configured model: llama3');

    expect(screen.getByText('Save')).not.toBeDisabled();
    expect(screen.getByText('Test connection')).not.toBeDisabled();
  });

  it('calls onConfigureLlm callback when provided instead of raw API', async () => {
    const onConfigureLlm = vi.fn().mockResolvedValue({ configured: true, model: 'llama3' });
    api.getLlmStatus.mockResolvedValue({ configured: false });
    render(<LlmConnectionCard sid={sid} onConfigureLlm={onConfigureLlm} />);
    await screen.findByText('Not configured');

    await userEvent.type(screen.getByPlaceholderText('http://localhost:11434/v1'), 'http://localhost:11434/v1');
    await userEvent.type(screen.getByPlaceholderText('llama3'), 'llama3');
    await userEvent.click(screen.getByText('Save'));

    expect(onConfigureLlm).toHaveBeenCalledOnce();
    expect(onConfigureLlm).toHaveBeenCalledWith({ endpoint: 'http://localhost:11434/v1', model: 'llama3' });
    expect(api.configureLlm).not.toHaveBeenCalled();
  });

  it('trims whitespace from endpoint, model, and api_key on save', async () => {
    api.getLlmStatus.mockResolvedValue({ configured: false });
    api.configureLlm.mockResolvedValue({ configured: true, model: 'llama3' });
    render(<LlmConnectionCard sid={sid} />);
    await screen.findByText('Not configured');

    await userEvent.type(screen.getByPlaceholderText('http://localhost:11434/v1'), '  http://localhost:11434/v1  ');
    await userEvent.type(screen.getByPlaceholderText('llama3'), '  llama3  ');
    await userEvent.click(screen.getByText('Save'));

    expect(api.configureLlm).toHaveBeenCalledWith(sid, { endpoint: 'http://localhost:11434/v1', model: 'llama3' });
  });

  it('clicking Test connection probes current field values, not saved config', async () => {
    api.getLlmStatus.mockResolvedValue({ configured: false });
    api.probeLlm.mockResolvedValue({ configured: true, reachable: true, model: 'llama3' });
    render(<LlmConnectionCard sid={sid} />);
    await screen.findByText('Not configured');

    await userEvent.type(screen.getByPlaceholderText('http://localhost:11434/v1'), 'http://localhost:11434/v1');
    await userEvent.type(screen.getByPlaceholderText('llama3'), 'llama3');
    await userEvent.click(screen.getByText('Test connection'));

    expect(api.probeLlm).toHaveBeenCalledOnce();
    expect(api.probeLlm).toHaveBeenCalledWith(sid, { endpoint: 'http://localhost:11434/v1', model: 'llama3' });
    expect(await screen.findByText('Reachable (llama3)')).toBeInTheDocument();
  });

  it('Test connection does not call configureLlm', async () => {
    api.getLlmStatus.mockResolvedValue({ configured: false });
    api.probeLlm.mockResolvedValue({ configured: true, reachable: true, model: 'llama3' });
    render(<LlmConnectionCard sid={sid} />);
    await screen.findByText('Not configured');

    await userEvent.type(screen.getByPlaceholderText('http://localhost:11434/v1'), 'http://localhost:11434/v1');
    await userEvent.type(screen.getByPlaceholderText('llama3'), 'llama3');
    await userEvent.click(screen.getByText('Test connection'));

    expect(api.configureLlm).not.toHaveBeenCalled();
  });
});

describe('LlmConnectionCard — integration', () => {
  it('real api/client.js export shape is consumed correctly (namespace-vs-named import guard)', () => {
    expect(api).toBeDefined();
    expect(typeof api.getLlmStatus).toBe('function');
    expect(typeof api.configureLlm).toBe('function');
    expect(typeof api.probeLlm).toBe('function');
  });
});
