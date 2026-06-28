import { useState, Component } from 'react';
import { useSession } from './hooks/useSession';

class StepErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }
  static getDerivedStateFromError(error) {
    return { error };
  }
  render() {
    if (this.state.error) {
      return (
        <div style={{ padding: 32, maxWidth: 520, margin: '60px auto', textAlign: 'center' }}>
          <div style={{ fontSize: '1.4rem', fontWeight: 700, marginBottom: 12, color: 'var(--ink)' }}>Something went wrong</div>
          <div style={{ color: 'var(--ink2)', fontSize: '0.9rem', marginBottom: 24, fontFamily: 'monospace', background: 'var(--panel2)', padding: '10px 14px', borderRadius: 6, textAlign: 'left', wordBreak: 'break-all' }}>
            {this.state.error.message}
          </div>
          <button
            className="btn btn-danger"
            onClick={this.props.onStartOver}
          >
            Discard session and start over
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
import { Setup }         from './pages/Setup';
import { Prepare }       from './pages/Prepare';
import { PreGrayscale }  from './pages/PreGrayscale';
import { Luminance }     from './pages/Luminance';
import { WhiteBalance }  from './pages/WhiteBalance';
import { Gamma }         from './pages/Gamma';
import { ColorTuner }    from './pages/ColorTuner';
import { PostGrayscale } from './pages/PostGrayscale';
import { SuggestedPatches } from './pages/SuggestedPatches';
import { Report } from './pages/Report';
import { ComparisonPage } from './pages/ComparisonPage';
import { ConfirmModal }  from './components/ConfirmModal';
import { LlmInsightCard } from './components/LlmInsightCard';
import { TvSettingsInput } from './components/TvSettingsInput';
import { LogsPanel }     from './components/LogsPanel';
import { AppShell }     from './components/AppShell';


function scrollToCard(id) {
  const el = document.getElementById(id);
  if (!el) return;
  el.scrollIntoView({ behavior: 'smooth', block: 'center' });
  el.classList.remove('card-pulse');
  void el.offsetWidth;
  el.classList.add('card-pulse');
  setTimeout(() => el.classList.remove('card-pulse'), 900);
}

export default function App() {
  const sess = useSession();
  const { session, profiles, watchStatus, watchDefaultPath, bridgeStatus, bridgeUrl, dogegenStatus, adbStatus, loading,
          llmInsight, llmStreaming, llmError, dismissLlmInsight, saveTvSettings, getSuggestedPatches, runSuggestedPatches } = sess;
  const [showStartOver, setShowStartOver] = useState(false);
  const [showComparison, setShowComparison] = useState(false);

  function handleStartOver() {
    setShowStartOver(true);
  }

  function handleConfirmStartOver() {
    setShowStartOver(false);
    sess.deleteSession();
  }

  if (loading) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100vh' }}>
        <span className="spinner" />
      </div>
    );
  }

  // Common props passed to every measurement step
  const stepProps = {
    session, watchStatus, watchDefaultPath, bridgeStatus, bridgeUrl, dogegenStatus,
    onUpload:       sess.uploadCsv,
    onStartWatch:   sess.startWatch,
    onStopWatch:    sess.stopWatch,
    onSaveBridgeUrl: sess.saveBridgeUrl,
    onMeasure:      sess.triggerMeasure,
    onSaveDogegenConfig: sess.saveDogegenConfig,
    onStartDogegen: sess.startDogegen,
    onStopDogegen:  sess.stopDogegen,
    onNext:         sess.nextStep,
    onPrev:         sess.prevStep,
  };

  function renderStep() {
    if (!session) {
      return <Setup profiles={profiles} onCreateSession={sess.createSession} onConfirmMode={sess.confirmMode} />;
    }
    switch (session.step) {
      case 'select_mode':    return null;
      case 'prepare':        return <Prepare       {...stepProps} onConfirmPrepared={sess.confirmPrepared} onSetLightspaceTier={sess.setLightspaceTier} onSetGrayscaleRamp={sess.setGrayscaleRamp} onSetSignalRange={sess.setSignalRange} onSetCodeScale={sess.setCodeScale} onSetPatternGenerator={sess.setPatternGenerator} onConfigureLlm={sess.configureLlm} onGetLlmStatus={sess.getLlmStatus} />;
      case 'pre_grayscale':  return <PreGrayscale  {...stepProps} />;
      case 'luminance':      return <Luminance     {...stepProps} adbStatus={adbStatus} onAdbSetPicture={sess.adbSetPicture} onAdbGetPicture={sess.adbGetPicture} onAdbDeploy={sess.adbDeploy} onRefreshAdb={sess.refreshAdbStatus} />;
      case 'white_balance':  return <WhiteBalance  {...stepProps} />;
      case 'gamma':          return <Gamma         {...stepProps} onSetGammaWorkflow={sess.setGammaWorkflow} />;
      case 'color_tuner':    return <ColorTuner    {...stepProps} adbStatus={adbStatus} onAdbApply={sess.adbApply} onAdbReset={sess.adbReset} onAdbDeploy={sess.adbDeploy} onRefreshAdb={sess.refreshAdbStatus} />;
      case 'post_grayscale': return <PostGrayscale {...stepProps} />;
      case 'suggested_patches': return <SuggestedPatches {...stepProps} getSuggestedPatches={getSuggestedPatches} runSuggestedPatches={runSuggestedPatches} />;
      case 'report':         return <Report        session={session} onPrev={sess.prevStep} />;
      default:               return <Setup         profiles={profiles} onCreateSession={sess.createSession} />;
    }
  }

  function handleToggleComparison() {
    setShowComparison(prev => !prev);
  }

  const bridgeOk = bridgeStatus?.ok;
  const bridgeConfigured = bridgeStatus?.configured;
  const dogegenRunning = dogegenStatus?.running;
  const dogegenConfigured = dogegenStatus?.configured;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', minHeight: '100vh' }}>
      {showStartOver && (
        <ConfirmModal
          title="Discard this calibration session?"
          body={`${session?.tv} — ${session?.mode}\nAll measurements will be permanently deleted.`}
          confirmLabel="Discard session"
          onConfirm={handleConfirmStartOver}
          onCancel={() => setShowStartOver(false)}
        />
      )}
      {/* Header */}
      <header className="header">
        <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
          <div className="header-accent-bar" />
          <div>
            <div className="header-label">ColourSpace ZRO / Zero</div>
            <div className="header-title">Calibration <span>Helper</span></div>
          </div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          {session?.id && (
            <>
              <div className="session-badge" title={`Session ${session.id}`}>{session.tv} — {session.mode}</div>
              <button
                onClick={handleStartOver}
                style={{ fontSize: '0.72rem', padding: '3px 10px', background: 'transparent', border: '1px solid var(--line)', borderRadius: 4, color: 'var(--ink2)', cursor: 'pointer' }}
              >
                Start Over
              </button>
            </>
          )}
          {watchStatus.watching && (
            <button className="badge badge-watch" onClick={() => scrollToCard('watch-folder')} style={{ cursor: 'pointer', border: 'none', background: 'none', padding: 0 }}>● WATCHING</button>
          )}
          {(bridgeOk || bridgeConfigured) && (
            <button onClick={() => scrollToCard('bridge-card')} style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: '0.72rem', color: bridgeOk ? 'var(--green)' : 'var(--red)', cursor: 'pointer', background: 'none', border: 'none', padding: 0, fontFamily: 'inherit' }}>
              <span style={{ width: 8, height: 8, borderRadius: '50%', background: bridgeOk ? 'var(--green)' : 'var(--ink2)', flexShrink: 0 }} />
              Bridge
            </button>
          )}
          {(dogegenRunning || dogegenConfigured) && (
            <button onClick={() => scrollToCard('dogegen-card')} style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: '0.72rem', color: dogegenRunning ? 'var(--green)' : 'var(--red)', cursor: 'pointer', background: 'none', border: 'none', padding: 0, fontFamily: 'inherit' }}>
              <span style={{ width: 8, height: 8, borderRadius: '50%', background: dogegenRunning ? 'var(--green)' : 'var(--ink2)', flexShrink: 0 }} />
              Dogegen
            </button>
          )}
          <button
            onClick={handleToggleComparison}
            style={{ fontSize: '0.72rem', padding: '3px 10px', background: showComparison ? 'var(--accent-dim)' : 'transparent', border: `1px solid ${showComparison ? 'var(--accent)' : 'var(--line)'}`, borderRadius: 4, color: showComparison ? 'var(--accent)' : 'var(--ink2)', cursor: 'pointer' }}
          >
            {showComparison ? '↑ Comparison' : 'Compare'}
          </button>
        </div>
      </header>

      {/* Main content */}
      <AppShell
        session={session}
        onJump={sess.jumpToStep}
        watchStatus={watchStatus}
        watchDefaultPath={watchDefaultPath}
        bridgeStatus={bridgeStatus}
        bridgeUrl={bridgeUrl}
        dogegenStatus={dogegenStatus}
        adbStatus={adbStatus}
        onMeasure={sess.triggerMeasure}
        onSaveBridgeUrl={sess.saveBridgeUrl}
        onStartWatch={sess.startWatch}
        onStopWatch={sess.stopWatch}
        onUpload={sess.uploadCsv}
        onSaveDogegenConfig={sess.saveDogegenConfig}
        onStartDogegen={sess.startDogegen}
        onStopDogegen={sess.stopDogegen}
      >
        {showComparison ? (
          <ComparisonPage profiles={profiles} />
        ) : (
          <>
            {session && saveTvSettings && (
              <TvSettingsInput onSave={saveTvSettings} />
            )}
            <LlmInsightCard
              insight={llmInsight}
              streaming={llmStreaming}
              error={llmError}
              onDismiss={dismissLlmInsight}
            />
            <StepErrorBoundary onStartOver={handleConfirmStartOver}>
              {renderStep()}
            </StepErrorBoundary>
          </>
        )}
      </AppShell>
      <LogsPanel />
    </div>
  );
}
