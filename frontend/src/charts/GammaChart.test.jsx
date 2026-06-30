import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { GammaChart } from './GammaChart';

let lastChartProps = null;
vi.mock('react-chartjs-2', () => ({
  Line: (props) => {
    lastChartProps = props;
    return <canvas />;
  },
}));

describe('GammaChart', () => {
  it('renders nothing when measurements is null', () => {
    const { container } = render(<GammaChart measurements={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('renders nothing when measurements is empty', () => {
    const { container } = render(<GammaChart measurements={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('renders nothing when all measurements have no effective_gamma', () => {
    const measurements = [
      { stimulus_pct: 50, effective_gamma: null },
      { stimulus_pct: 100, effective_gamma: undefined },
    ];
    const { container } = render(<GammaChart measurements={measurements} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('renders nothing when all measurements have no stimulus_pct', () => {
    const measurements = [
      { stimulus_pct: null, effective_gamma: 2.1 },
      { stimulus_pct: undefined, effective_gamma: 2.3 },
    ];
    const { container } = render(<GammaChart measurements={measurements} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('does not render "null%" labels when stimulus_pct is null', () => {
    const measurements = [
      { stimulus_pct: 50, effective_gamma: 2.2 },
      { stimulus_pct: null, effective_gamma: 2.1 },
    ];
    render(<GammaChart measurements={measurements} />);
    expect(screen.queryByText('null%')).toBeNull();
  });

  it('renders chart element when data is valid', () => {
    const measurements = [
      { stimulus_pct: 10, effective_gamma: 2.0 },
      { stimulus_pct: 50, effective_gamma: 2.2 },
      { stimulus_pct: 100, effective_gamma: 2.3 },
    ];
    const { container } = render(<GammaChart measurements={measurements} />);
    expect(container.querySelector('canvas')).toBeInTheDocument();
  });

  it('skips measurements with null effective_gamma even if stimulus_pct is valid', () => {
    const measurements = [
      { stimulus_pct: 10, effective_gamma: null },
      { stimulus_pct: 50, effective_gamma: 2.2 },
    ];
    const { container } = render(<GammaChart measurements={measurements} />);
    expect(container.querySelector('canvas')).toBeInTheDocument();
  });

  it('renders chart element with single valid measurement', () => {
    const measurements = [{ stimulus_pct: 50, effective_gamma: 2.2 }];
    const { container } = render(<GammaChart measurements={measurements} />);
    expect(container.querySelector('canvas')).toBeInTheDocument();
  });

  it('renders nothing when measurements is undefined', () => {
    const { container } = render(<GammaChart />);
    expect(container).toBeEmptyDOMElement();
  });

  it('passes "Target 2.2" reference dataset when eotf is unset (SDR)', () => {
    const measurements = [{ stimulus_pct: 50, effective_gamma: 2.2 }];
    render(<GammaChart measurements={measurements} />);
    const target = lastChartProps.data.datasets.find(d => d.label.startsWith('Target'));
    expect(target.label).toBe('Target 2.2');
    expect(target.data).toEqual([2.2]);
  });

  it('passes "Target 1.0 (PQ)" reference dataset, not "Target 2.2", when eotf="PQ (ST.2084)"', () => {
    const measurements = [{ stimulus_pct: 50, effective_gamma: 1.0 }];
    render(<GammaChart measurements={measurements} eotf="PQ (ST.2084)" />);
    const target = lastChartProps.data.datasets.find(d => d.label.startsWith('Target'));
    expect(target.label).toBe('Target 1.0 (PQ)');
    expect(target.data).toEqual([1.0]);
  });
});
