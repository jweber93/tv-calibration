import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi } from 'vitest';
import { DogegenCard } from './DogegenCard';

function renderCard(props = {}) {
  return render(
    <DogegenCard
      dogegenStatus={props.dogegenStatus}
      session={props.session || { mode: 'HDR10' }}
      onSaveConfig={props.onSaveConfig || vi.fn()}
      onStart={props.onStart || vi.fn()}
      onStop={props.onStop || vi.fn()}
    />
  );
}

describe('DogegenCard', () => {
  it('shows local unconfigured message when no status and no agent url', () => {
    renderCard({ dogegenStatus: { running: false, configured: false } });
    expect(screen.getByText('Dogegen path not configured')).toBeInTheDocument();
  });

  it('shows local running message without remote suffix', () => {
    renderCard({ dogegenStatus: { running: true, configured: true } });
    expect(screen.getByText('Dogegen running')).toBeInTheDocument();
  });

  it('shows remote-agent running message when agent_url is set', () => {
    renderCard({
      dogegenStatus: {
        running: true,
        configured: true,
        agent_url: 'http://192.168.1.50:7071',
        agent_reachable: true,
      },
    });
    expect(screen.getByText('Dogegen running (remote agent)')).toBeInTheDocument();
  });

  it('shows remote ready message when configured but not running', () => {
    renderCard({
      dogegenStatus: {
        running: false,
        configured: true,
        agent_url: 'http://192.168.1.50:7071',
        agent_reachable: true,
      },
    });
    expect(screen.getByText('Dogegen ready to launch via remote agent')).toBeInTheDocument();
  });

  it('shows "agent unreachable" when agent_reachable is false', () => {
    renderCard({
      dogegenStatus: {
        running: false,
        configured: false,
        agent_url: 'http://192.168.1.50:7071',
        agent_reachable: false,
        last_error: 'Cannot reach Dogegen Companion Agent — is it running on the Windows PC?',
      },
    });
    expect(screen.getByText('Dogegen agent unreachable')).toBeInTheDocument();
  });

  it('Save includes agent_url alongside the existing config fields', async () => {
    const onSaveConfig = vi.fn().mockResolvedValue({ running: false, configured: false });
    renderCard({ dogegenStatus: { running: false, configured: false }, onSaveConfig });

    await userEvent.click(screen.getByText('Settings'));
    await userEvent.type(
      screen.getByPlaceholderText('http://<windows-pc>:7071'),
      'http://192.168.1.50:7071'
    );
    await userEvent.click(screen.getByText('Save'));

    expect(onSaveConfig).toHaveBeenCalledWith(
      expect.objectContaining({ agent_url: 'http://192.168.1.50:7071' })
    );
  });

  it('disables local-only fields (path, resolve host, window %, maxcll) when an agent url is configured', async () => {
    renderCard({
      dogegenStatus: {
        running: false,
        configured: true,
        agent_url: 'http://192.168.1.50:7071',
        agent_reachable: true,
      },
    });
    await userEvent.click(screen.getByText('Settings'));
    expect(screen.getByPlaceholderText('C:\\Program Files\\Dogegen\\Dogegen.exe')).toBeDisabled();
    expect(screen.getByPlaceholderText('127.0.0.1')).toBeDisabled();
  });
});
