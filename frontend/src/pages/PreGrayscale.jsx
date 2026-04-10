import { Card } from '../components/Card';
import { Button } from '../components/Button';
import { MeasurementTable } from '../components/MeasurementTable';
import { ImportLog } from '../components/ImportLog';
import { UploadZone } from '../components/UploadZone';
import { WatchFolder } from '../components/WatchFolder';
import { BridgeCard } from '../components/BridgeCard';
import { DogegenCard } from '../components/DogegenCard';
import { ZroInstructions } from '../components/ZroInstructions';
import { DeChart } from '../charts/DeChart';

export function PreGrayscale({ session, watchStatus, watchDefaultPath, bridgeStatus, bridgeUrl, dogegenStatus, onUpload, onStartWatch, onStopWatch, onSaveBridgeUrl, onSaveDogegenConfig, onStartDogegen, onStopDogegen, onMeasure, onNext, onPrev }) {
  const gd = session.grayscale_data || {};
  const done  = gd.done || 0;
  const total = gd.total || 11;
  const pct   = Math.round(done / total * 100);
  const meas  = gd.measurements || [];

  return (
    <>
      <ZroInstructions instructions={session.zro_instructions} />

      <Card title="ZRO Bridge" id="bridge-card">
        <BridgeCard
          bridgeStatus={bridgeStatus} bridgeUrl={bridgeUrl}
          session={session} dogegenStatus={dogegenStatus}
          onSaveUrl={onSaveBridgeUrl} onMeasure={onMeasure}
        />
      </Card>

      {session.pattern_generator === 'dogegen' && (
        <Card title="Dogegen Companion" id="dogegen-card">
          <DogegenCard
            dogegenStatus={dogegenStatus}
            session={session}
            onSaveConfig={onSaveDogegenConfig}
            onStart={onStartDogegen}
            onStop={onStopDogegen}
          />
        </Card>
      )}

      <Card title="Auto-Watch Path" id="watch-folder">
        <WatchFolder
          watchStatus={watchStatus} defaultPath={watchStatus.path || watchDefaultPath}
          onStart={onStartWatch} onStop={onStopWatch}
        />
      </Card>

      <Card title="Import ZRO CSV">
        <UploadZone onUpload={onUpload} />
      </Card>

      <Card title="Pre-Calibration Grayscale Progress">
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12 }}>
          <div style={{ flex: 1, background: 'var(--surface2)', height: 8, borderRadius: 4, overflow: 'hidden' }}>
            <div style={{
              height: '100%', width: `${pct}%`,
              background: pct === 100 ? 'var(--green)' : 'var(--accent)',
              borderRadius: 4, transition: 'width .3s',
            }} />
          </div>
          <div className="text-sm muted">{done}/{total}</div>
        </div>
        {meas.length > 0 && <DeChart measurements={meas} label="Pre-Cal ΔE" />}
        <div className="mt-3"><MeasurementTable measurements={meas} /></div>
      </Card>

      <Card title="Import Log">
        <ImportLog imports={session.zro_imports} />
      </Card>

      <div className="btn-group">
        <Button onClick={onPrev}>← Back</Button>
        <Button primary onClick={onNext} disabled={done < total}>
          {done < total ? `Need ${total - done} more readings` : 'Continue to Luminance →'}
        </Button>
      </div>
    </>
  );
}
