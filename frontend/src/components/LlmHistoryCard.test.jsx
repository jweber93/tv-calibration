import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { LlmHistoryCard } from './LlmHistoryCard';
import * as client from '../api/client';

vi.mock('../api/client', () => ({
  getLlmHistorySummary: vi.fn(),
}));

const sid = 'test-session-123';

describe('LlmHistoryCard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders nothing while loading', () => {
    client.getLlmHistorySummary.mockImplementation(() => new Promise(() => {}));
    const { container } = render(<LlmHistoryCard sid={sid} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('renders first-calibration message when session_count is 0', async () => {
    client.getLlmHistorySummary.mockResolvedValue({
      tv_key: 'u8g',
      session_count: 0,
      has_final_settings: false,
    });
    render(<LlmHistoryCard sid={sid} />);
    expect(await screen.findByText(/First calibration for this TV/)).toBeInTheDocument();
  });

  it('renders prior-session count when session_count is greater than 0', async () => {
    client.getLlmHistorySummary.mockResolvedValue({
      tv_key: 'u8g',
      session_count: 3,
      has_final_settings: true,
    });
    render(<LlmHistoryCard sid={sid} />);
    expect(await screen.findByText(/Priming the LLM from 3 prior sessions/)).toBeInTheDocument();
  });

  it('renders muted line when prior runs have no saved settings', async () => {
    client.getLlmHistorySummary.mockResolvedValue({
      tv_key: 'u8g',
      session_count: 2,
      has_final_settings: false,
    });
    render(<LlmHistoryCard sid={sid} />);
    expect(await screen.findByText(/Prior runs have no saved settings yet/)).toBeInTheDocument();
  });

  it('does not render muted line when has_final_settings is true', async () => {
    client.getLlmHistorySummary.mockResolvedValue({
      tv_key: 'u8g',
      session_count: 1,
      has_final_settings: true,
    });
    render(<LlmHistoryCard sid={sid} />);
    await screen.findByText(/Priming the LLM from 1 prior session/);
    expect(screen.queryByText(/Prior runs have no saved settings yet/)).not.toBeInTheDocument();
  });

  it('renders subtle error message when API call rejects', async () => {
    client.getLlmHistorySummary.mockRejectedValue(new Error('network error'));
    render(<LlmHistoryCard sid={sid} />);
    expect(await screen.findByText('Unable to load history.')).toBeInTheDocument();
  });
});
