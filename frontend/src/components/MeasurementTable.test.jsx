import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect } from 'vitest';
import { MeasurementTable } from './MeasurementTable';

const sampleMeasurements = [
  { label: '0%', stimulus_pct: 0, timestamp: '2026-01-01T00:00:00Z', Y: 0.1, x: 0.3127, y: 0.3290, cct: 6504, delta_e: 0.5, effective_gamma: null },
  { label: '10%', stimulus_pct: 10, timestamp: '2026-01-01T00:00:01Z', Y: 5.2, x: 0.3130, y: 0.3295, cct: 6480, delta_e: 0.8, effective_gamma: 2.18 },
  { label: '100%', stimulus_pct: 100, timestamp: '2026-01-01T00:00:02Z', Y: 300.0, x: 0.3125, y: 0.3288, cct: 6520, delta_e: 0.3, effective_gamma: 2.21 },
];

describe('MeasurementTable', () => {
  it('renders empty state when no measurements', () => {
    render(<MeasurementTable measurements={[]} />);
    expect(screen.getByText(/No measurements yet/i)).toBeInTheDocument();
  });

  it('renders measurements with basic columns', () => {
    render(<MeasurementTable measurements={sampleMeasurements} includeGamma={false} />);
    expect(screen.getByText('10%')).toBeInTheDocument();
    expect(screen.getByText('300.0')).toBeInTheDocument();
  });

  it('renders gamma column when includeGamma is true', () => {
    render(<MeasurementTable measurements={sampleMeasurements} includeGamma={true} />);
    expect(screen.getByText('2.180')).toBeInTheDocument();
    expect(screen.getByText('2.210')).toBeInTheDocument();
  });

  it('does not render gamma values when includeGamma is false', () => {
    render(<MeasurementTable measurements={sampleMeasurements} includeGamma={false} />);
    const table = screen.getByRole('table');
    expect(table.textContent).not.toContain('2.180');
  });

  it('gamma tooltip states SDR target 2.2, not 2.40', async () => {
    const user = userEvent.setup();
    render(<MeasurementTable measurements={sampleMeasurements} includeGamma={true} />);
    const gammaHeader = screen.getByText('\u03b3').closest('th');
    const trigger = gammaHeader.querySelector('.tooltip-trigger');
    await user.hover(trigger);
    await waitFor(() => {
      expect(screen.getByText(/SDR target: 2\.2/)).toBeInTheDocument();
    });
  });
});
