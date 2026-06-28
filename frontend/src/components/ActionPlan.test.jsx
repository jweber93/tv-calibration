import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi } from 'vitest';
import { ActionPlan } from './ActionPlan';

const step = (overrides = {}) => ({
  control: 'Brightness',
  direction: 'up',
  summary: 'Raise brightness',
  amount: 5,
  ...overrides,
});

const adbReady = { connected: true, cms_tool_deployed: true };

describe('ActionPlan', () => {
  it('renders nothing when plan is null', () => {
    const { container } = render(<ActionPlan plan={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('renders nothing when plan is empty', () => {
    const { container } = render(<ActionPlan plan={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('renders "What to Adjust" heading when plan has steps', () => {
    render(<ActionPlan plan={[step()]} />);
    expect(screen.getByText('What to Adjust')).toBeInTheDocument();
  });

  it('renders menuPath in mono when provided', () => {
    render(<ActionPlan plan={[step()]} menuPath="Picture > Advanced" />);
    expect(screen.getByText('Picture > Advanced')).toBeInTheDocument();
  });

  it('does not render menuPath when not provided', () => {
    render(<ActionPlan plan={[step()]} />);
    expect(screen.queryByText('Picture > Advanced')).not.toBeInTheDocument();
  });

  it('renders one row per plan step', () => {
    const plan = [step({ control: 'A' }), step({ control: 'B' }), step({ control: 'C' })];
    render(<ActionPlan plan={plan} />);
    expect(screen.getByText('A')).toBeInTheDocument();
    expect(screen.getByText('B')).toBeInTheDocument();
    expect(screen.getByText('C')).toBeInTheDocument();
  });

  it('renders direction glyph for each step', () => {
    const plan = [
      step({ direction: 'up' }),
      step({ direction: 'down' }),
      step({ direction: 'hold' }),
    ];
    render(<ActionPlan plan={plan} />);
    expect(screen.getByLabelText('increase')).toBeInTheDocument();
    expect(screen.getByLabelText('decrease')).toBeInTheDocument();
    expect(screen.getByLabelText('hold')).toBeInTheDocument();
  });

  it('renders summary text for each step', () => {
    render(<ActionPlan plan={[step({ summary: 'Raise brightness slightly' })]} />);
    expect(screen.getByText('Raise brightness slightly')).toBeInTheDocument();
  });

  it('renders reason text when provided', () => {
    render(<ActionPlan plan={[step({ reason: 'Shadow detail is crushed' })]} />);
    expect(screen.getByText('Shadow detail is crushed')).toBeInTheDocument();
  });

  it('renders magnitude bar when step has a positive amount', () => {
    render(<ActionPlan plan={[step({ amount: 5 })]} adbStatus={adbReady} onApply={vi.fn()} />);
    expect(screen.getByTestId('magnitude-bar')).toBeInTheDocument();
  });

  it('renders magnitude bar when step.amount is 0 (null-check, not falsy-check)', () => {
    render(<ActionPlan plan={[step({ amount: 0 })]} adbStatus={adbReady} onApply={vi.fn()} />);
    expect(screen.getByTestId('magnitude-bar')).toBeInTheDocument();
  });

  it('does not render magnitude bar when step has no amount', () => {
    render(
      <ActionPlan plan={[step({ amount: undefined })]} adbStatus={adbReady} onApply={vi.fn()} />,
    );
    expect(screen.queryByTestId('magnitude-bar')).not.toBeInTheDocument();
  });

  it('Apply button absent for amount: 0 step (falsy guard in canApply)', () => {
    render(
      <ActionPlan
        plan={[step({ amount: 0, direction: 'up' })]}
        adbStatus={adbReady}
        onApply={vi.fn()}
      />,
    );
    expect(screen.queryByText('Apply')).not.toBeInTheDocument();
  });

  it('Apply and Apply all buttons absent when adbStatus not provided', () => {
    render(<ActionPlan plan={[step()]} onApply={vi.fn()} />);
    expect(screen.queryByText('Apply')).not.toBeInTheDocument();
    expect(screen.queryByText('Apply all')).not.toBeInTheDocument();
  });

  it('Apply and Apply all buttons absent when adbStatus.connected is false', () => {
    render(
      <ActionPlan
        plan={[step()]}
        adbStatus={{ connected: false, cms_tool_deployed: true }}
        onApply={vi.fn()}
      />,
    );
    expect(screen.queryByText('Apply')).not.toBeInTheDocument();
    expect(screen.queryByText('Apply all')).not.toBeInTheDocument();
  });

  it('Apply and Apply all buttons absent when adbStatus.cms_tool_deployed is false', () => {
    render(
      <ActionPlan
        plan={[step()]}
        adbStatus={{ connected: true, cms_tool_deployed: false }}
        onApply={vi.fn()}
      />,
    );
    expect(screen.queryByText('Apply')).not.toBeInTheDocument();
    expect(screen.queryByText('Apply all')).not.toBeInTheDocument();
  });

  it('Apply button calls onApply with correct args', async () => {
    const onApply = vi.fn();
    render(
      <ActionPlan
        plan={[step({ control: 'Brightness', amount: 7, direction: 'up' })]}
        adbStatus={adbReady}
        onApply={onApply}
      />,
    );
    await userEvent.click(screen.getByText('Apply'));
    expect(onApply).toHaveBeenCalledOnce();
    expect(onApply).toHaveBeenCalledWith('Brightness', 7, 'up');
  });

  it('Apply all button calls onApply once per non-hold step with an amount', async () => {
    const onApply = vi.fn();
    const plan = [
      step({ control: 'A', direction: 'up', amount: 3 }),
      step({ control: 'B', direction: 'hold', amount: 2 }),
      step({ control: 'C', direction: 'down', amount: 5 }),
      step({ control: 'D', direction: 'up', amount: undefined }),
    ];
    render(<ActionPlan plan={plan} adbStatus={adbReady} onApply={onApply} />);
    await userEvent.click(screen.getByText('Apply all'));
    expect(onApply).toHaveBeenCalledTimes(2);
    expect(onApply).toHaveBeenCalledWith('A', 3, 'up');
    expect(onApply).toHaveBeenCalledWith('C', 5, 'down');
  });

  it('hold steps never render an Apply button', () => {
    render(
      <ActionPlan
        plan={[step({ direction: 'hold', amount: 3 })]}
        adbStatus={adbReady}
        onApply={vi.fn()}
      />,
    );
    expect(screen.queryByText('Apply')).not.toBeInTheDocument();
  });

  it('steps with no amount render no magnitude bar', () => {
    render(<ActionPlan plan={[step({ amount: undefined })]} />);
    expect(screen.queryByTestId('magnitude-bar')).not.toBeInTheDocument();
  });
});
