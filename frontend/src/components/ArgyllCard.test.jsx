import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { ArgyllCard } from './ArgyllCard';

function renderCard(props = {}) {
  return render(
    <ArgyllCard
      argyllPrefs={props.argyllPrefs}
      onScanInstruments={props.onScanInstruments || vi.fn().mockResolvedValue({ instruments: [] })}
      onSaveInstrument={props.onSaveInstrument || vi.fn().mockResolvedValue({})}
    />
  );
}

describe('ArgyllCard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows "No meter selected" when there are no saved prefs', () => {
    renderCard();
    expect(screen.getByText('No meter selected')).toBeInTheDocument();
  });

  it('shows the saved instrument name when prefs are set', () => {
    renderCard({ argyllPrefs: { port: '1', instrument_name: 'Klein K10-A on COM3' } });
    expect(screen.getByText('Meter: Klein K10-A on COM3')).toBeInTheDocument();
  });

  it('Scan populates the port dropdown from onScanInstruments', async () => {
    const onScanInstruments = vi.fn().mockResolvedValue({
      instruments: [{ index: 1, name: 'X-Rite i1 Display Pro, USB' }],
    });
    renderCard({ onScanInstruments });

    await userEvent.click(screen.getByText('Settings'));
    await userEvent.click(screen.getByText('Scan'));

    expect(onScanInstruments).toHaveBeenCalled();
    expect(await screen.findByText('X-Rite i1 Display Pro, USB')).toBeInTheDocument();
  });

  it('Save defaults refresh mode / rate / integration mode to null when left unset', async () => {
    const onSaveInstrument = vi.fn().mockResolvedValue({});
    renderCard({ onSaveInstrument });

    await userEvent.click(screen.getByText('Settings'));
    await userEvent.click(screen.getByText('Save'));

    expect(onSaveInstrument).toHaveBeenCalledWith(
      expect.objectContaining({
        refresh_mode: null,
        refresh_rate_hz: null,
        integration_mode: null,
      })
    );
  });

  it('Save includes the chosen refresh mode, refresh rate, and integration mode', async () => {
    const onSaveInstrument = vi.fn().mockResolvedValue({});
    renderCard({ onSaveInstrument });

    await userEvent.click(screen.getByText('Settings'));
    await userEvent.selectOptions(
      screen.getByText('Auto (instrument default)').closest('select'),
      'refresh'
    );
    await userEvent.type(screen.getByPlaceholderText('e.g. 120'), '120');
    await userEvent.selectOptions(
      screen.getByText('Adaptive (Argyll default)').closest('select'),
      'non_adaptive'
    );
    await userEvent.click(screen.getByText('Save'));

    expect(onSaveInstrument).toHaveBeenCalledWith(
      expect.objectContaining({
        refresh_mode: 'refresh',
        refresh_rate_hz: 120,
        integration_mode: 'non_adaptive',
      })
    );
  });

  it('shows an error message when saving fails', async () => {
    const onSaveInstrument = vi.fn().mockRejectedValue(new Error('bridge unreachable'));
    renderCard({ onSaveInstrument });

    await userEvent.click(screen.getByText('Settings'));
    await userEvent.click(screen.getByText('Save'));

    expect(await screen.findByText('Error: bridge unreachable')).toBeInTheDocument();
  });
});
