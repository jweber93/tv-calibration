import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect } from 'vitest';
import { SessionWarnings } from './SessionWarnings';

describe('SessionWarnings', () => {
  it('renders nothing when there are no warnings', () => {
    const { container } = render(<SessionWarnings warnings={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('renders nothing when warnings is undefined', () => {
    const { container } = render(<SessionWarnings />);
    expect(container).toBeEmptyDOMElement();
  });

  it('lists every warning message', () => {
    render(
      <SessionWarnings
        warnings={[
          "Default session code_scale='10bit' was not applied: 10bit code_scale currently requires Full signal range",
          "Default session pattern_generator='nope' was not applied: nope",
        ]}
      />,
    );
    expect(screen.getByTestId('session-warnings')).toBeInTheDocument();
    expect(screen.getAllByRole('listitem')).toHaveLength(2);
    expect(screen.getByText(/code_scale='10bit' was not applied/)).toBeInTheDocument();
  });

  it('hides the banner after Dismiss is clicked', async () => {
    render(<SessionWarnings warnings={["Default session signal_range='limited' was not applied: nope"]} />);
    expect(screen.getByTestId('session-warnings')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Dismiss' }));
    expect(screen.queryByTestId('session-warnings')).not.toBeInTheDocument();
  });

  it('shows warnings again for a new session (remount via key)', async () => {
    const { rerender } = render(<SessionWarnings key="s1" warnings={['first']} />);
    await userEvent.click(screen.getByRole('button', { name: 'Dismiss' }));
    rerender(<SessionWarnings key="s2" warnings={['second']} />);
    expect(screen.getByTestId('session-warnings')).toBeInTheDocument();
    expect(screen.getByText('second')).toBeInTheDocument();
  });
});
