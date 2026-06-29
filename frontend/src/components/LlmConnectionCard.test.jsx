import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { LlmConnectionCard } from './LlmConnectionCard';
import * as client from '../api/client';

vi.mock('../api/client', () => ({
  getLlmStatus: vi.fn(),
  configureLlm: vi.fn(),
}));

const sid = 'test-session-123';

describe('LlmConnectionCard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows "Not configured" when getLlmStatus returns configured:false', async () => {
    client.getLlmStatus.mockResolvedValue({ configured: false });
    render(<LlmConnectionCard sid={sid} />);
    expect(await screen.findByText('Not configured')).toBeInTheDocument();
  });

  it('shows green reachable badge with model when reachable', async () => {
    client.getLlmStatus.mockResolvedValue({ configured: true, reachable: true, model: 'llama3' });
    render(<LlmConnectionCard sid={sid} />);
    expect(await screen.findByText('Reachable (llama3)')).toBeInTheDocument();
  });

  it('shows error when configured but unreachable', async () => {
    client.getLlmStatus.mockResolvedValue({ configured: true, reachable: false, error: 'HTTP 401: Unauthorized' });
    render(<LlmConnectionCard sid={sid} />);
    await screen.findByText('Not configured');
    await userEvent.click(screen.getByText('Test connection'));
    expect(await screen.findByText('HTTP 401: Unauthorized')).toBeInTheDocument();
  });

  it('clicking Save calls configureLlm with entered field values', async () => {
    client.getLlmStatus.mockResolvedValue({ configured: false });
    client.configureLlm.mockResolvedValue({ configured: true, model: 'llama3' });
    render(<LlmConnectionCard sid={sid} />);
    await screen.findByText('Not configured');

    await userEvent.type(screen.getByPlaceholderText('http://localhost:11434/v1'), 'http://localhost:11434/v1');
    await userEvent.type(screen.getByPlaceholderText('llama3'), 'llama3');
    await userEvent.click(screen.getByText('Save'));

    expect(client.configureLlm).toHaveBeenCalledOnce();
    expect(client.configureLlm).toHaveBeenCalledWith(sid, { endpoint: 'http://localhost:11434/v1', model: 'llama3' });
  });

  it('prefills endpoint and model from status on mount when already configured', async () => {
    client.getLlmStatus.mockResolvedValue({ configured: true, reachable: true, model: 'gpt-4', endpoint: 'https://api.openai.com/v1' });
    render(<LlmConnectionCard sid={sid} />);
    const endpointInput = await screen.findByDisplayValue('https://api.openai.com/v1');
    expect(endpointInput).toBeInTheDocument();
    expect(screen.getByDisplayValue('gpt-4')).toBeInTheDocument();
  });

  it('disables buttons while saving', async () => {
    client.getLlmStatus.mockResolvedValue({ configured: false });
    let resolveConfigure;
    client.configureLlm.mockImplementation(() => new Promise(r => { resolveConfigure = r; }));
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

  it('trims whitespace from endpoint, model, and api_key on save', async () => {
    client.getLlmStatus.mockResolvedValue({ configured: false });
    client.configureLlm.mockResolvedValue({ configured: true, model: 'llama3' });
    render(<LlmConnectionCard sid={sid} />);
    await screen.findByText('Not configured');

    await userEvent.type(screen.getByPlaceholderText('http://localhost:11434/v1'), '  http://localhost:11434/v1  ');
    await userEvent.type(screen.getByPlaceholderText('llama3'), '  llama3  ');
    await userEvent.click(screen.getByText('Save'));

    expect(client.configureLlm).toHaveBeenCalledWith(sid, { endpoint: 'http://localhost:11434/v1', model: 'llama3' });
  });
});
