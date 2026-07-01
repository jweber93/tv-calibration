import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { OverlayDeChart } from './OverlayDeChart';
import { C } from './tokens.js';

let lastChartProps = null;
vi.mock('react-chartjs-2', () => ({
  Bar: (props) => {
    lastChartProps = props;
    return <canvas />;
  },
}));

describe('OverlayDeChart', () => {
  it('renders nothing when both measurement sets are empty', () => {
    const { container } = render(<OverlayDeChart measurementsA={[]} measurementsB={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('renders nothing when both measurement sets are null', () => {
    const { container } = render(<OverlayDeChart measurementsA={null} measurementsB={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('renders chart when only measurementsA has data', () => {
    const measurementsA = [{ stimulus_pct: 50, delta_e: 1.5 }];
    const { container } = render(<OverlayDeChart measurementsA={measurementsA} measurementsB={[]} />);
    expect(container.querySelector('canvas')).toBeInTheDocument();
  });

  it('renders chart when only measurementsB has data', () => {
    const measurementsB = [{ stimulus_pct: 50, delta_e: 1.8 }];
    const { container } = render(<OverlayDeChart measurementsA={[]} measurementsB={measurementsB} />);
    expect(container.querySelector('canvas')).toBeInTheDocument();
  });

  it('does not render "null%" labels when stimulus_pct is null', () => {
    const measurementsA = [
      { stimulus_pct: 50, delta_e: 1.5 },
      { stimulus_pct: null, delta_e: 2.0 },
    ];
    render(<OverlayDeChart measurementsA={measurementsA} measurementsB={[]} />);
    expect(screen.queryByText('null%')).toBeNull();
  });

  it('renders chart with multiple data points', () => {
    const measurementsA = [
      { stimulus_pct: 10, delta_e: 1.0 },
      { stimulus_pct: 50, delta_e: 1.5 },
      { stimulus_pct: 100, delta_e: 2.0 },
    ];
    const measurementsB = [
      { stimulus_pct: 10, delta_e: 0.8 },
      { stimulus_pct: 50, delta_e: 1.2 },
      { stimulus_pct: 100, delta_e: 1.5 },
    ];
    const { container } = render(<OverlayDeChart measurementsA={measurementsA} measurementsB={measurementsB} />);
    expect(container.querySelector('canvas')).toBeInTheDocument();
  });

  it('renders chart when using label field instead of stimulus_pct', () => {
    const measurementsA = [{ label: 'Red 50%', delta_e: 1.5 }];
    const measurementsB = [{ label: 'Red 50%', delta_e: 1.2 }];
    const { container } = render(<OverlayDeChart measurementsA={measurementsA} measurementsB={measurementsB} />);
    expect(container.querySelector('canvas')).toBeInTheDocument();
  });

  it('handles mixed label and stimulus_pct sources', () => {
    const measurementsA = [
      { stimulus_pct: 25, delta_e: 1.0 },
      { label: 'Blue 75%', delta_e: 1.8 },
    ];
    const measurementsB = [
      { stimulus_pct: 25, delta_e: 0.9 },
    ];
    const { container } = render(<OverlayDeChart measurementsA={measurementsA} measurementsB={measurementsB} />);
    expect(container.querySelector('canvas')).toBeInTheDocument();
  });

  it('renders chart when all measurements have null stimulus_pct and no label (empty labels)', () => {
    const measurementsA = [
      { stimulus_pct: null, delta_e: 1.5 },
    ];
    const measurementsB = [
      { stimulus_pct: null, delta_e: 1.2 },
    ];
    const { container } = render(<OverlayDeChart measurementsA={measurementsA} measurementsB={measurementsB} />);
    expect(container.querySelector('canvas')).toBeInTheDocument();
  });

  it('renders chart when one set has null stimulus_pct but the other has valid data', () => {
    const measurementsA = [
      { stimulus_pct: 50, delta_e: 1.5 },
      { stimulus_pct: null, delta_e: 2.0 },
    ];
    const measurementsB = [{ stimulus_pct: 50, delta_e: 1.2 }];
    const { container } = render(<OverlayDeChart measurementsA={measurementsA} measurementsB={measurementsB} />);
    expect(container.querySelector('canvas')).toBeInTheDocument();
  });

  it('renders chart when measurementsA is undefined but measurementsB has data', () => {
    const measurementsB = [{ stimulus_pct: 50, delta_e: 1.2 }];
    const { container } = render(<OverlayDeChart measurementsA={undefined} measurementsB={measurementsB} />);
    expect(container.querySelector('canvas')).toBeInTheDocument();
  });

  it('renders chart when measurementsB is undefined but measurementsA has data', () => {
    const measurementsA = [{ stimulus_pct: 50, delta_e: 1.2 }];
    const { container } = render(<OverlayDeChart measurementsA={measurementsA} measurementsB={undefined} />);
    expect(container.querySelector('canvas')).toBeInTheDocument();
  });

  it('renders nothing when both sets are undefined', () => {
    const { container } = render(<OverlayDeChart measurementsA={undefined} measurementsB={undefined} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('colors Session A passing patches (dE <= 2) differently from failing patches (dE > 3)', () => {
    const measurementsA = [
      { stimulus_pct: 10, delta_e: 0.5 },
      { stimulus_pct: 50, delta_e: 8.0 },
    ];
    render(<OverlayDeChart measurementsA={measurementsA} measurementsB={[]} />);
    const [passColor, failColor] = lastChartProps.data.datasets[0].backgroundColor;
    expect(passColor).toBe(C.green);
    expect(failColor).toBe(C.red);
    expect(passColor).not.toBe(failColor);
  });

  it('matches Session A and Session B severity coloring for the same dE values', () => {
    const measurementsA = [
      { stimulus_pct: 10, delta_e: 1.0 },
      { stimulus_pct: 50, delta_e: 2.5 },
      { stimulus_pct: 90, delta_e: 5.0 },
    ];
    const measurementsB = [
      { stimulus_pct: 10, delta_e: 1.0 },
      { stimulus_pct: 50, delta_e: 2.5 },
      { stimulus_pct: 90, delta_e: 5.0 },
    ];
    render(<OverlayDeChart measurementsA={measurementsA} measurementsB={measurementsB} />);
    const [colorsA, colorsB] = lastChartProps.data.datasets.map(d => d.backgroundColor);
    expect(colorsA).toEqual(colorsB);
    expect(colorsA).toEqual([C.green, C.amber, C.red]);
  });
});
