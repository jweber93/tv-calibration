import { render, screen, waitFor } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { PredictedSettingsCard } from './PredictedSettingsCard';

describe('PredictedSettingsCard', () => {
  it('renders nothing while loading', () => {
    const getPredictedSettings = vi.fn(() => new Promise(() => {}));
    const { container } = render(
      <PredictedSettingsCard getPredictedSettings={getPredictedSettings} phase="white_balance" />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it('renders muted reason when predicted is null', async () => {
    const getPredictedSettings = vi.fn().mockResolvedValue({ predicted: null, reason: 'LLM not configured' });
    render(
      <PredictedSettingsCard getPredictedSettings={getPredictedSettings} phase="white_balance" />,
    );
    await waitFor(() => {
      expect(screen.getByText('LLM not configured')).toBeInTheDocument();
    });
  });

  it('renders "measuring from defaults" when source is cold_start', async () => {
    const getPredictedSettings = vi.fn().mockResolvedValue({
      predicted: { settings: [], source: 'cold_start', confidence: 0 },
    });
    render(
      <PredictedSettingsCard getPredictedSettings={getPredictedSettings} phase="white_balance" />,
    );
    await waitFor(() => {
      expect(screen.getByText('No prior data for this TV — measuring from defaults.')).toBeInTheDocument();
    });
  });

  it('renders "measuring from defaults" when settings is empty', async () => {
    const getPredictedSettings = vi.fn().mockResolvedValue({
      predicted: { settings: [], source: 'history', confidence: 0.5 },
    });
    render(
      <PredictedSettingsCard getPredictedSettings={getPredictedSettings} phase="white_balance" />,
    );
    await waitFor(() => {
      expect(screen.getByText('No prior data for this TV — measuring from defaults.')).toBeInTheDocument();
    });
  });

  it('renders heading and settings when source is history with data', async () => {
    const getPredictedSettings = vi.fn().mockResolvedValue({
      predicted: {
        settings: [
          { menu: 'White Balance', setting: 'R-Gain', value: 5, scope: 'global', reason: 'Prior session offset' },
        ],
        source: 'history',
        confidence: 0.85,
      },
    });
    render(
      <PredictedSettingsCard getPredictedSettings={getPredictedSettings} phase="white_balance" />,
    );
    await waitFor(() => {
      expect(screen.getByText('Predicted Starting Point')).toBeInTheDocument();
      expect(screen.getByText(/White Balance.*R-Gain/)).toBeInTheDocument();
      expect(screen.getByText('5')).toBeInTheDocument();
      expect(screen.getByText('85% confidence')).toBeInTheDocument();
    });
  });

  it('calls getPredictedSettings with the correct phase', async () => {
    const getPredictedSettings = vi.fn().mockResolvedValue({ predicted: null });
    render(
      <PredictedSettingsCard getPredictedSettings={getPredictedSettings} phase="gamma" />,
    );
    await waitFor(() => {
      expect(getPredictedSettings).toHaveBeenCalledWith('gamma');
    });
  });

  it('renders nothing when API call rejects', async () => {
    const getPredictedSettings = vi.fn().mockRejectedValue(new Error('network error'));
    render(
      <PredictedSettingsCard getPredictedSettings={getPredictedSettings} phase="white_balance" />,
    );
    await waitFor(() => {
      expect(screen.getByText('Prediction unavailable.')).toBeInTheDocument();
    });
  });

  it('renders every setting\'s reason in its own row (regression: issue #353)', async () => {
    const getPredictedSettings = vi.fn().mockResolvedValue({
      predicted: {
        settings: [
          { menu: 'White Balance', setting: 'R-Gain', value: 5, scope: 'global', reason: 'Prior session offset' },
          { menu: 'White Balance', setting: 'G-Gain', value: 2, scope: 'global', reason: 'Close to neutral at green high' },
          { menu: 'White Balance', setting: 'B-Gain', value: -3, scope: 'global', reason: 'Blue pulled high last cal' },
        ],
        source: 'history',
        confidence: 0.85,
      },
    });
    render(
      <PredictedSettingsCard getPredictedSettings={getPredictedSettings} phase="white_balance" />,
    );
    await waitFor(() => {
      expect(screen.getByText('Prior session offset')).toBeInTheDocument();
      expect(screen.getByText('Close to neutral at green high')).toBeInTheDocument();
      expect(screen.getByText('Blue pulled high last cal')).toBeInTheDocument();
    });
  });

  it('hides legacy single-reason footer block (regression: issue #353)', async () => {
    const getPredictedSettings = vi.fn().mockResolvedValue({
      predicted: {
        settings: [
          { menu: 'White Balance', setting: 'R-Gain', value: 5, scope: 'global', reason: 'Prior session offset' },
          { menu: 'White Balance', setting: 'G-Gain', value: 2, scope: 'global' },
        ],
        source: 'history',
        confidence: 0.85,
      },
    });
    render(
      <PredictedSettingsCard getPredictedSettings={getPredictedSettings} phase="white_balance" />,
    );
    await waitFor(() => {
      // R-Gain shows its own reason
      expect(screen.getByText('Prior session offset')).toBeInTheDocument();
      // Only the row that has a reason should render it — G-Gain has none
      expect(screen.queryByText(/2/)).toBeInTheDocument(); // the value still renders
      // No reason text node is duplicated as a footer block
      const reasonNodes = screen.getAllByText('Prior session offset');
      expect(reasonNodes).toHaveLength(1);
    });
  });

  it('handles non-Promise return (session not ready)', async () => {
    const getPredictedSettings = vi.fn().mockReturnValue(null);
    render(
      <PredictedSettingsCard getPredictedSettings={getPredictedSettings} phase="white_balance" />,
    );
    await waitFor(() => {
      expect(screen.getByText('Prediction unavailable.')).toBeInTheDocument();
    });
  });
});
