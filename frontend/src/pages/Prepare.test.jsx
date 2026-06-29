import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { Prepare } from './Prepare';

// Mock the two LLM cards with minimal stubs that expose identifying text,
// and mock DogegenCard so we don't need a backend.
vi.mock('../components/LlmHistoryCard', () => ({
  LlmHistoryCard: () => <div>LlmHistoryCard Stub</div>,
}));
vi.mock('../components/LlmConnectionCard', () => ({
  LlmConnectionCard: () => <div>LlmConnectionCard Stub</div>,
}));
vi.mock('../components/DogegenCard', () => ({
  DogegenCard: () => <div>DogegenCard Stub</div>,
}));

const baseSession = {
  id: 'session-1',
  mode: 'HDR10',
  prepare_data: {
    grayscale_ramp_options: [
      { steps: 11, label: '11-step (5% steps)', summary: '11 patches, 5% steps from 0–100%' },
      { steps: 21, label: '21-step', summary: '21 patches' },
    ],
    pattern_generator_options: [
      { key: 'dogegen', label: 'Dogegen', desc: 'PC HDMI pattern generator' },
    ],
    code_scale_options: [
      { key: '8bit', label: '8-bit', desc: '0..255' },
    ],
  },
  lightspace_tier: 'free',
  grayscale_ramp_steps: 11,
  signal_range: 'full',
  code_scale: '8bit',
  pattern_generator: 'dogegen',
};

describe('Prepare page — AI Assistant section (regression: issue #354)', () => {
  it('wraps LLM cards in a CollapsibleSection titled "AI Assistant"', () => {
    render(<Prepare session={baseSession} />);

    // CollapsibleSection renders a button with the title; it should be the
    // collapsible trigger, not a flat plain div.
    const aiButton = screen.getByRole('button', { name: /AI Assistant/i });
    expect(aiButton).toBeInTheDocument();
    expect(aiButton).toHaveAttribute('aria-expanded');
  });

  it('keeps the AI Assistant section collapsed by default', () => {
    render(<Prepare session={baseSession} />);
    const aiButton = screen.getByRole('button', { name: /AI Assistant/i });
    // CollapsibleSection wires aria-expanded to its open state.
    expect(aiButton).toHaveAttribute('aria-expanded', 'false');
    // The stub bodies of the two LLM cards should NOT be in the DOM while closed.
    expect(screen.queryByText('LlmHistoryCard Stub')).not.toBeInTheDocument();
    expect(screen.queryByText('LlmConnectionCard Stub')).not.toBeInTheDocument();
  });
});
