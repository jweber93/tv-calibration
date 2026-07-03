import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi } from 'vitest';
import { ColorTuner } from './ColorTuner';

const adbReady = { connected: true, cms_tool_deployed: true };

const baseSession = {
  id: 'session-1',
  cms_data: {
    colors: [{ name: 'Red 100%', rgb: [255, 0, 0] }],
    measurements: [{ label: 'Red 100%', delta_e: 3.2 }],
    control_plan: [
      { control: 'Hue', direction: 'down', amount: 2, summary: 'Lower Hue' },
    ],
    latest_hint: true,
  },
};

describe('ColorTuner', () => {
  it('renders Color Status with colour swatches and delta_e values', () => {
    render(
      <ColorTuner
        session={baseSession}
        adbStatus={adbReady}
        onAdbApply={vi.fn()}
      />,
    );
    expect(screen.getByText('Red 100%')).toBeInTheDocument();
    expect(screen.getByText(/ΔE/)).toBeInTheDocument();
  });

  it('renders "What to Adjust" when control_plan is present', () => {
    render(
      <ColorTuner
        session={baseSession}
        adbStatus={adbReady}
        onAdbApply={vi.fn()}
      />,
    );
    expect(screen.getByText('What to Adjust')).toBeInTheDocument();
  });

  it('does not render "What to Adjust" when latest_hint is false', () => {
    const session = { ...baseSession, cms_data: { ...baseSession.cms_data, latest_hint: false } };
    render(
      <ColorTuner session={session} adbStatus={adbReady} onAdbApply={vi.fn()} />,
    );
    expect(screen.queryByText('What to Adjust')).not.toBeInTheDocument();
  });

  it('Apply button calls onAdbApply with positive delta for direction=up', async () => {
    const onAdbApply = vi.fn();
    const session = {
      ...baseSession,
      cms_data: {
        ...baseSession.cms_data,
        control_plan: [
          { control: 'Hue', direction: 'up', amount: 3, summary: 'Raise Hue' },
        ],
      },
    };
    render(
      <ColorTuner
        session={session}
        adbStatus={adbReady}
        onAdbApply={onAdbApply}
      />,
    );
    await userEvent.click(screen.getByText('Apply'));
    expect(onAdbApply).toHaveBeenCalledOnce();
    expect(onAdbApply).toHaveBeenCalledWith('Red', 'Hue', 3);
  });

  it('Apply button calls onAdbApply with negative delta for direction=down', async () => {
    const onAdbApply = vi.fn();
    render(
      <ColorTuner
        session={baseSession}
        adbStatus={adbReady}
        onAdbApply={onAdbApply}
      />,
    );
    await userEvent.click(screen.getByText('Apply'));
    expect(onAdbApply).toHaveBeenCalledOnce();
    // direction=down, amount=2 → delta = -2 (relative adjustment)
    expect(onAdbApply).toHaveBeenCalledWith('Red', 'Hue', -2);
  });

  it('Apply all sends one call per non-hold step with correct deltas', async () => {
    const onAdbApply = vi.fn();
    const session = {
      ...baseSession,
      cms_data: {
        ...baseSession.cms_data,
        control_plan: [
          { control: 'Hue', direction: 'up', amount: 3, summary: 'Raise Hue' },
          { control: 'Saturation', direction: 'down', amount: 1, summary: 'Lower Sat' },
          { control: 'Brightness', direction: 'hold', amount: 2, summary: 'Hold Bri' },
        ],
      },
    };
    render(
      <ColorTuner
        session={session}
        adbStatus={adbReady}
        onAdbApply={onAdbApply}
      />,
    );
    await userEvent.click(screen.getByText('Apply all'));
    expect(onAdbApply).toHaveBeenCalledTimes(2);
    expect(onAdbApply).toHaveBeenCalledWith('Red', 'Hue', 3);
    expect(onAdbApply).toHaveBeenCalledWith('Red', 'Saturation', -1);
    // hold step should NOT be called
  });

  it('Reset CMS button is present when ADB is ready', () => {
    render(
      <ColorTuner session={baseSession} adbStatus={adbReady} onAdbApply={vi.fn()} />,
    );
    expect(screen.getByText('Reset All CMS to 0')).toBeInTheDocument();
  });

  it('does not render CMS measurements when there are none', () => {
    const session = { ...baseSession, cms_data: { ...baseSession.cms_data, measurements: [] } };
    render(
      <ColorTuner session={session} adbStatus={adbReady} onAdbApply={vi.fn()} />,
    );
    expect(screen.queryByText('CMS Measurements')).not.toBeInTheDocument();
  });

  it('renders CMS measurements section when there are readings', () => {
    render(
      <ColorTuner session={baseSession} adbStatus={adbReady} onAdbApply={vi.fn()} />,
    );
    expect(screen.getByText('CMS Measurements')).toBeInTheDocument();
  });

  it('deploy button shown when ADB connected but cms_tool.dex missing', () => {
    render(
      <ColorTuner
        session={baseSession}
        adbStatus={{ connected: true, cms_tool_deployed: false }}
        onAdbApply={vi.fn()}
      />,
    );
    expect(screen.getByText('Deploy cms_tool.dex')).toBeInTheDocument();
  });

  it('Reset button disabled when ADB not connected', () => {
    render(
      <ColorTuner
        session={baseSession}
        adbStatus={{ connected: false, cms_tool_deployed: false }}
        onAdbApply={vi.fn()}
      />,
    );
    const btn = screen.getByText('Reset All CMS to 0');
    expect(btn).toBeDisabled();
  });

  it('navigation buttons present', () => {
    const onPrev = vi.fn();
    const onNext = vi.fn();
    render(
      <ColorTuner
        session={baseSession}
        adbStatus={adbReady}
        onAdbApply={vi.fn()}
        onPrev={onPrev}
        onNext={onNext}
      />,
    );
    expect(screen.getByText('← Back')).toBeInTheDocument();
    expect(screen.getByText('Continue to Post-Cal Grayscale →')).toBeInTheDocument();
  });
});
