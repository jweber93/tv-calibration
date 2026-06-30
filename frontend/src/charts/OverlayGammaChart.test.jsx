import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { OverlayGammaChart } from './OverlayGammaChart';

let lastChartProps = null;
vi.mock('react-chartjs-2', () => ({
  Line: (props) => {
    lastChartProps = props;
    return <canvas />;
  },
}));

describe('OverlayGammaChart', () => {
  it('renders nothing when both measurement sets are empty', () => {
    const { container } = render(<OverlayGammaChart measurementsA={[]} measurementsB={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('renders chart when only measurementsA has data', () => {
    const measurementsA = [{ stimulus_pct: 50, effective_gamma: 2.1 }];
    const { container } = render(<OverlayGammaChart measurementsA={measurementsA} measurementsB={[]} />);
    expect(container.querySelector('canvas')).toBeInTheDocument();
  });

  it('renders chart when only measurementsB has data', () => {
    const measurementsB = [{ stimulus_pct: 50, effective_gamma: 2.3 }];
    const { container } = render(<OverlayGammaChart measurementsA={[]} measurementsB={measurementsB} />);
    expect(container.querySelector('canvas')).toBeInTheDocument();
  });

  it('does not render "null%" labels when stimulus_pct is null in measurementsA', () => {
    const measurementsA = [
      { stimulus_pct: 50, effective_gamma: 2.1 },
      { stimulus_pct: null, effective_gamma: 2.0 },
    ];
    render(<OverlayGammaChart measurementsA={measurementsA} measurementsB={[]} />);
    expect(screen.queryByText('null%')).toBeNull();
  });

  it('does not render "null%" labels when stimulus_pct is null in measurementsB', () => {
    const measurementsB = [
      { stimulus_pct: 50, effective_gamma: 2.3 },
      { stimulus_pct: null, effective_gamma: 2.4 },
    ];
    render(<OverlayGammaChart measurementsA={[]} measurementsB={measurementsB} />);
    expect(screen.queryByText('null%')).toBeNull();
  });

  it('renders chart with both datasets having valid data', () => {
    const measurementsA = [{ stimulus_pct: 25, effective_gamma: 2.0 }];
    const measurementsB = [
      { stimulus_pct: 10, effective_gamma: 2.1 },
      { stimulus_pct: 50, effective_gamma: 2.3 },
    ];
    const { container } = render(<OverlayGammaChart measurementsA={measurementsA} measurementsB={measurementsB} />);
    expect(container.querySelector('canvas')).toBeInTheDocument();
  });

  it('renders all Session A points when ramp counts differ', () => {
    const measA = Array.from({ length: 21 }, (_, i) => ({
      stimulus_pct: i * 5,
      effective_gamma: 2.2
    }));
    const measB = Array.from({ length: 11 }, (_, i) => ({
      stimulus_pct: i * 10,
      effective_gamma: 2.3
    }));
    const { container } = render(<OverlayGammaChart measurementsA={measA} measurementsB={measB} />);
    expect(container.querySelector('canvas')).toBeInTheDocument();
  });

  it('handles unordered input by sorting stimuli', () => {
    const measA = [
      { stimulus_pct: 100, effective_gamma: 2.5 },
      { stimulus_pct: 50, effective_gamma: 2.1 },
      { stimulus_pct: 10, effective_gamma: 1.8 },
    ];
    const measB = [
      { stimulus_pct: 25, effective_gamma: 2.0 },
      { stimulus_pct: 75, effective_gamma: 2.3 },
    ];
    const { container } = render(<OverlayGammaChart measurementsA={measA} measurementsB={measB} />);
    expect(container.querySelector('canvas')).toBeInTheDocument();
  });

  it('handles string stimulus_pct values by normalizing to numbers', () => {
    const measA = [
      { stimulus_pct: '10', effective_gamma: 1.8 },
      { stimulus_pct: '50', effective_gamma: 2.1 },
    ];
    const measB = [
      { stimulus_pct: '30', effective_gamma: 2.0 },
    ];
    const { container } = render(<OverlayGammaChart measurementsA={measA} measurementsB={measB} />);
    expect(container.querySelector('canvas')).toBeInTheDocument();
  });

  it('handles null measurementsA gracefully', () => {
    const measB = [{ stimulus_pct: 50, effective_gamma: 2.3 }];
    const { container } = render(<OverlayGammaChart measurementsA={null} measurementsB={measB} />);
    expect(container.querySelector('canvas')).toBeInTheDocument();
  });

  it('handles undefined measurementsB gracefully', () => {
    const measA = [{ stimulus_pct: 50, effective_gamma: 2.1 }];
    const { container } = render(<OverlayGammaChart measurementsA={measA} measurementsB={undefined} />);
    expect(container.querySelector('canvas')).toBeInTheDocument();
  });

  it('handles non-array measurementsA by treating as empty', () => {
    const measB = [{ stimulus_pct: 50, effective_gamma: 2.3 }];
    const { container } = render(<OverlayGammaChart measurementsA="not an array" measurementsB={measB} />);
    expect(container.querySelector('canvas')).toBeInTheDocument();
  });
it('renders null when both sets are null', () => {
    const { container } = render(<OverlayGammaChart measurementsA={null} measurementsB={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('renders nothing when both sets have only null stimulus_pct', () => {
    const measurementsA = [{ stimulus_pct: null, effective_gamma: 2.0 }];
    const measurementsB = [{ stimulus_pct: null, effective_gamma: 2.1 }];
    const { container } = render(<OverlayGammaChart measurementsA={measurementsA} measurementsB={measurementsB} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('renders nothing when both sets have only null effective_gamma', () => {
    const measurementsA = [{ stimulus_pct: 50, effective_gamma: null }];
    const measurementsB = [{ stimulus_pct: 50, effective_gamma: undefined }];
    const { container } = render(<OverlayGammaChart measurementsA={measurementsA} measurementsB={measurementsB} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('passes "Target 2.2" reference dataset when eotf is unset (SDR)', () => {
    const measurementsA = [{ stimulus_pct: 50, effective_gamma: 2.1 }];
    render(<OverlayGammaChart measurementsA={measurementsA} measurementsB={[]} />);
    const target = lastChartProps.data.datasets.find(d => d.label.startsWith('Target'));
    expect(target.label).toBe('Target 2.2');
    expect(target.data).toEqual([2.2]);
  });

  it('passes "Target 1.0 (PQ)" reference dataset, not "Target 2.2", when eotf="PQ (ST.2084)"', () => {
    const measurementsA = [{ stimulus_pct: 50, effective_gamma: 1.0 }];
    render(<OverlayGammaChart measurementsA={measurementsA} measurementsB={[]} eotf="PQ (ST.2084)" />);
    const target = lastChartProps.data.datasets.find(d => d.label.startsWith('Target'));
    expect(target.label).toBe('Target 1.0 (PQ)');
    expect(target.data).toEqual([1.0]);
  });
});
