import { PhaseRail } from './PhaseRail';
import { InstrumentPanel } from './InstrumentPanel';

export function AppShell({
  session, onJump, children,
  watchStatus, watchDefaultPath,
  bridgeStatus, bridgeUrl,
  dogegenStatus, adbStatus,
  onMeasure, onSaveBridgeUrl,
  onStartWatch, onStopWatch,
  onUpload,
  onSaveDogegenConfig, onStartDogegen, onStopDogegen,
}) {
  const showInstrument = session != null;

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
          <InstrumentPanel
            session={session}
            watchStatus={watchStatus}
            watchDefaultPath={watchDefaultPath}
            bridgeStatus={bridgeStatus}
            bridgeUrl={bridgeUrl}
            dogegenStatus={dogegenStatus}
            adbStatus={adbStatus}
            onMeasure={onMeasure}
            onSaveBridgeUrl={onSaveBridgeUrl}
            onStartWatch={onStartWatch}
            onStopWatch={onStopWatch}
            onUpload={onUpload}
            onSaveDogegenConfig={onSaveDogegenConfig}
            onStartDogegen={onStartDogegen}
            onStopDogegen={onStopDogegen}
          />
        </aside>
      )}
    </div>
  );
}
