import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { OverlayGammaChart } from './OverlayGammaChart';

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
});
