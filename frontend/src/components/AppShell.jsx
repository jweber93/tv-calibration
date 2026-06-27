import { PhaseRail } from './PhaseRail';

const MEASUREMENT_STEPS = new Set([
  'pre_grayscale', 'luminance', 'white_balance', 'gamma', 'color_tuner', 'post_grayscale',
]);

export function AppShell({ session, onJump, children }) {
  const showInstrument = session != null && MEASUREMENT_STEPS.has(session.step);

  return (
    <div className="workspace">
      <aside className="workspace-rail">
        <PhaseRail session={session} onJump={onJump} />
      </aside>
      <main className="workspace-main">
        {children}
      </main>
      {showInstrument && (
        <aside className="workspace-instrument">
          {/* InstrumentPanel — issue #4 */}
        </aside>
      )}
    </div>
  );
}
