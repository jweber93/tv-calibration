import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { AutocalCard } from './AutocalCard';
import { api } from '../api/client';

vi.mock('../api/client', () => ({
  api: {
    getPrefs: vi.fn(),
    autocalRun: vi.fn(),
    autocalStop: vi.fn(),
    autocalConfirm: vi.fn(),
    savePrefs: vi.fn(),
  },
}));

class MockEventSource {
  constructor(url) {
    this.url = url;
    this.listeners = {};
    MockEventSource.instances.push(this);
  }
  addEventListener(type, cb) {
    (this.listeners[type] ||= []).push(cb);
  }
  close() {}
  emit(type, data) {
    (this.listeners[type] || []).forEach(cb => cb({ data: JSON.stringify(data) }));
  }
}
MockEventSource.instances = [];

const sid = 'test-session-123';

describe('AutocalCard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    MockEventSource.instances = [];
    globalThis.EventSource = MockEventSource;
    api.getPrefs.mockResolvedValue({ autocal: { apply_mode: 'manual', max_iterations: 8 } });
    api.autocalRun.mockResolvedValue({ status: 'running' });
    api.autocalStop.mockResolvedValue({ status: 'stopping' });
    api.autocalConfirm.mockResolvedValue({ status: 'confirmed' });
    api.savePrefs.mockResolvedValue({ ok: true });
  });

  it('renders one status card per colour, all pending before a run', async () => {
    render(<AutocalCard sid={sid} colours={['Red', 'Green']} />);
    expect(await screen.findByText('Red')).toBeInTheDocument();
    expect(screen.getByText('Green')).toBeInTheDocument();
    expect(screen.getAllByText('Not started')).toHaveLength(2);
  });

  it('clicking Start Autocal calls autocalRun with the selected colours and apply mode', async () => {
    render(<AutocalCard sid={sid} colours={['Red']} />);
    await screen.findByText('Not started');
    await userEvent.click(screen.getByText('Start Autocal'));

    expect(api.autocalRun).toHaveBeenCalledWith(sid, { colours: ['Red'], apply_mode: 'manual', max_iterations: 8 });
    expect(await screen.findByText('Stop')).toBeInTheDocument();
  });

  it('updates a colour to "Measuring…" and shows ΔE on autocal_iteration', async () => {
    render(<AutocalCard sid={sid} colours={['Red']} />);
    await screen.findByText('Not started');
    await userEvent.click(screen.getByText('Start Autocal'));
    await screen.findByText('Stop');

    const es = MockEventSource.instances[0];
    act(() => {
      es.emit('autocal_iteration', {
        colour: 'Red', control: 'Hue', iteration: 1, error: 0.05, delta_e: 4.2,
        apply_ok: true, apply_message: 'Raise Hue to 3', requires_user_action: false,
      });
    });

    expect(await screen.findByText('Measuring…')).toBeInTheDocument();
    expect(screen.getByText('ΔE 4.20')).toBeInTheDocument();
  });

  it('shows the confirmation banner on autocal_waiting and clears it after confirm', async () => {
    render(<AutocalCard sid={sid} colours={['Red']} />);
    await screen.findByText('Not started');
    await userEvent.click(screen.getByText('Start Autocal'));
    await screen.findByText('Stop');

    const es = MockEventSource.instances[0];
    act(() => {
      es.emit('autocal_iteration', {
        colour: 'Red', control: 'Saturation', iteration: 1, error: 0.01, delta_e: 5.0,
        apply_ok: true, apply_message: 'Raise Saturation to 4', requires_user_action: true,
      });
      es.emit('autocal_waiting', { colour: 'Red', iteration: 1 });
    });

    expect(await screen.findByText('Waiting on you')).toBeInTheDocument();
    const confirmBtn = screen.getByText('I made the change — Remeasure');
    await userEvent.click(confirmBtn);

    expect(api.autocalConfirm).toHaveBeenCalledWith(sid);
  });

  it('marks a colour converged on autocal_colour_done', async () => {
    render(<AutocalCard sid={sid} colours={['Red']} />);
    await screen.findByText('Not started');
    await userEvent.click(screen.getByText('Start Autocal'));
    await screen.findByText('Stop');

    const es = MockEventSource.instances[0];
    act(() => {
      es.emit('autocal_colour_done', { colour: 'Red', converged: true, reason: 'quality gate met', delta_e: 1.2, iterations: 3 });
    });

    expect(await screen.findByText('Converged')).toBeInTheDocument();
  });

  it('shows a finished message and resets to Start Autocal on autocal_done', async () => {
    render(<AutocalCard sid={sid} colours={['Red']} />);
    await screen.findByText('Not started');
    await userEvent.click(screen.getByText('Start Autocal'));
    await screen.findByText('Stop');

    const es = MockEventSource.instances[0];
    act(() => {
      es.emit('autocal_done', { cancelled: false });
    });

    expect(await screen.findByText('Autocal run finished.')).toBeInTheDocument();
    expect(screen.getByText('Start Autocal')).toBeInTheDocument();
  });

  it('clicking Stop calls autocalStop', async () => {
    render(<AutocalCard sid={sid} colours={['Red']} />);
    await screen.findByText('Not started');
    await userEvent.click(screen.getByText('Start Autocal'));
    await userEvent.click(await screen.findByText('Stop'));

    expect(api.autocalStop).toHaveBeenCalledWith(sid);
  });

  it('switching apply mode to ADB persists the preference', async () => {
    render(<AutocalCard sid={sid} colours={['Red']} />);
    await screen.findByText('Not started');
    await userEvent.click(screen.getByText('ADB (auto)'));

    expect(api.savePrefs).toHaveBeenCalledWith({ autocal_apply_mode: 'adb' });
  });
});
