import { useState } from 'react';
import { useSession } from './hooks/useSession';
import { Setup }         from './pages/Setup';
import { SelectMode }    from './pages/SelectMode';
import { Prepare }       from './pages/Prepare';
import { PreGrayscale }  from './pages/PreGrayscale';
import { Luminance }     from './pages/Luminance';
import { WhiteBalance }  from './pages/WhiteBalance';
import { Gamma }         from './pages/Gamma';
import { ColorTuner }    from './pages/ColorTuner';
import { PostGrayscale } from './pages/PostGrayscale';
import { Report }        from './pages/Report';
import { ConfirmModal }  from './components/ConfirmModal';

function QualityDot({ gate }) {
  if (!gate) return null;
  const color = gate.passed ? 'var(--green)' : 'var(--red)';
  const title = gate.passed ? `✓ ${gate.detail}` : `✗ ${gate.detail}`;
  return (
    <span
      title={title}
      style={{ width: 6, height: 6, borderRadius: '50%', background: color, display: 'inline-block', marginLeft: 4, flexShrink: 0, verticalAlign: 'middle' }}
    />
  );
}

function ProgressBar({ session, onJump }) {
  if (!session?.steps) return null;
  const gates = session.quality_gates || {};
  return (
    <div className="progress-bar">
      {session.steps.map((step, i) => {
        const done = i < session.step_index;
        const cls = done ? 'done' : i === session.step_index ? 'active' : '';
        const icon = done ? '✓' : i + 1;
        return (
          <div
            className={`step-tab ${cls}${done ? ' step-tab-clickable' : ''}`}
            key={step.id || i}
            onClick={done ? () => onJump(i) : undefined}
            title={done ? `Go back to ${step.label}` : undefined}
          >
            <span className="step-num">{icon}</span>
            {step.label}
            {done && <QualityDot gate={gates[step.id]} />}
          </div>
        );
      })}
    </div>
  );
}

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
  const { session, profiles, watchStatus, watchDefaultPath, bridgeStatus, bridgeUrl, dogegenStatus, adbStatus, loading } = sess;
  const [showStartOver, setShowStartOver] = useState(false);

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
      return <Setup profiles={profiles} onCreateSession={sess.createSession} />;
    }
    switch (session.step) {
      case 'select_mode':    return <SelectMode    {...stepProps} onConfirmMode={sess.confirmMode} />;
      case 'prepare':        return <Prepare       {...stepProps} onConfirmPrepared={sess.confirmPrepared} onSetLightspaceTier={sess.setLightspaceTier} onSetGrayscaleRamp={sess.setGrayscaleRamp} onSetSignalRange={sess.setSignalRange} onSetCodeScale={sess.setCodeScale} onSetPatternGenerator={sess.setPatternGenerator} onConfigureLlm={sess.configureLlm} onGetLlmStatus={sess.getLlmStatus} />;
      case 'pre_grayscale':  return <PreGrayscale  {...stepProps} />;
      case 'luminance':      return <Luminance     {...stepProps} adbStatus={adbStatus} onAdbSetPicture={sess.adbSetPicture} onAdbGetPicture={sess.adbGetPicture} onAdbDeploy={sess.adbDeploy} onRefreshAdb={sess.refreshAdbStatus} />;
      case 'white_balance':  return <WhiteBalance  {...stepProps} />;
      case 'gamma':          return <Gamma         {...stepProps} onSetGammaWorkflow={sess.setGammaWorkflow} />;
      case 'color_tuner':    return <ColorTuner    {...stepProps} adbStatus={adbStatus} onAdbApply={sess.adbApply} onAdbReset={sess.adbReset} onAdbDeploy={sess.adbDeploy} onRefreshAdb={sess.refreshAdbStatus} />;
      case 'post_grayscale': return <PostGrayscale {...stepProps} />;
      case 'report':         return <Report        session={session} onPrev={sess.prevStep} />;
      default:               return <Setup         profiles={profiles} onCreateSession={sess.createSession} />;
    }
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
                style={{ fontSize: '0.72rem', padding: '3px 10px', background: 'transparent', border: '1px solid var(--border)', borderRadius: 4, color: 'var(--muted)', cursor: 'pointer' }}
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
              <span style={{ width: 8, height: 8, borderRadius: '50%', background: bridgeOk ? 'var(--green)' : 'var(--muted)', flexShrink: 0 }} />
              Bridge
            </button>
          )}
          {(dogegenRunning || dogegenConfigured) && (
            <button onClick={() => scrollToCard('dogegen-card')} style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: '0.72rem', color: dogegenRunning ? 'var(--green)' : 'var(--red)', cursor: 'pointer', background: 'none', border: 'none', padding: 0, fontFamily: 'inherit' }}>
              <span style={{ width: 8, height: 8, borderRadius: '50%', background: dogegenRunning ? 'var(--green)' : 'var(--muted)', flexShrink: 0 }} />
              Dogegen
            </button>
          )}
        </div>
      </header>

      {/* Step progress bar */}
      <ProgressBar session={session} onJump={sess.jumpToStep} />

      {/* Main content */}
      <main className="main">
        {renderStep()}
      </main>
    </div>
  );
}
