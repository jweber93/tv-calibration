import { useEffect, useState } from 'react';
import { Card } from '../components/Card';
import { Button } from '../components/Button';
import { DogegenCard } from '../components/DogegenCard';
import { LlmConnectionCard } from '../components/LlmConnectionCard';
import { LlmHistoryCard } from '../components/LlmHistoryCard';
import { Tooltip } from '../components/Tooltip';
import { CollapsibleSection } from '../components/CollapsibleSection';

export function Prepare({ session, dogegenStatus, onConfirmPrepared, onPrev, onSetLightspaceTier, onSetGrayscaleRamp, onSetSignalRange, onSetCodeScale, onSetPatternGenerator, onSaveDogegenConfig, onStartDogegen, onStopDogegen, onConfigureLlm }) {
  const pd = session.prepare_data || {};

  const currentTier        = session.lightspace_tier      || 'free';
  const currentSteps       = session.grayscale_ramp_steps || 11;
  const currentSignalRange = session.signal_range         || 'full';
  const currentCodeScale   = session.code_scale           || '8bit';
  const currentGenerator   = session.pattern_generator    || 'dogegen';
  const rampOptions        = pd.grayscale_ramp_options    || [];
  const generatorOptions   = pd.pattern_generator_options || [];
  const codeScaleOptions   = pd.code_scale_options        || [];

  const [selectedTier,        setSelectedTier]        = useState(currentTier);
  const [selectedSteps,       setSelectedSteps]       = useState(currentSteps);
  const [selectedSignalRange, setSelectedSignalRange] = useState(currentSignalRange);
  const [selectedCodeScale,   setSelectedCodeScale]   = useState(currentCodeScale);
  const [selectedGenerator,   setSelectedGenerator]   = useState(currentGenerator);

  useEffect(() => setSelectedTier(currentTier), [currentTier]);
  useEffect(() => setSelectedSteps(currentSteps), [currentSteps]);
  useEffect(() => setSelectedSignalRange(currentSignalRange), [currentSignalRange]);
  useEffect(() => setSelectedCodeScale(currentCodeScale), [currentCodeScale]);
  useEffect(() => setSelectedGenerator(currentGenerator), [currentGenerator]);

  const availableRamps = selectedGenerator === 'lightspace_connect'
    ? rampOptions.filter(o => !o.requires_paid || selectedTier === 'paid')
    : rampOptions.filter(o => o.steps !== 51);

  async function applyLightspaceSettings(tier, steps) {
    if (!onSetLightspaceTier) return;
    // If switching to free tier, clamp 51-step to 21-step
    const safeSteps = (tier === 'free' && steps === 51) ? 21 : steps;
    await onSetLightspaceTier(tier, safeSteps);
    setSelectedTier(tier);
    setSelectedSteps(safeSteps);
  }

  function handleTierChange(tier) {
    const safeSteps = (tier === 'free' && selectedSteps === 51) ? 21 : selectedSteps;
    setSelectedTier(tier);
    setSelectedSteps(safeSteps);
    applyLightspaceSettings(tier, safeSteps);
  }

  function handleRampChange(steps) {
    setSelectedSteps(steps);
    if (selectedGenerator === 'lightspace_connect') {
      applyLightspaceSettings(selectedTier, steps);
      return;
    }
    if (onSetGrayscaleRamp) onSetGrayscaleRamp(steps);
  }

  function handleSignalRangeChange(range) {
    setSelectedSignalRange(range);
    if (onSetSignalRange) onSetSignalRange(range);
  }

  function handleGeneratorChange(generator) {
    setSelectedGenerator(generator);
    if (onSetPatternGenerator) onSetPatternGenerator(generator);
  }

  function handleCodeScaleChange(codeScale) {
    setSelectedCodeScale(codeScale);
    if (onSetCodeScale) onSetCodeScale(codeScale);
  }

  const measurementSummary = [
    `${selectedSteps}-step`,
    selectedSignalRange === 'full' ? 'Full Range' : 'Limited Range',
    selectedCodeScale === '8bit' ? '8-bit' : selectedCodeScale === '10bit' ? '10-bit' : selectedCodeScale,
  ].join(' · ');

  const hasChecklistContent = (pd.zro_setup_steps || []).length > 0
    || pd.service_menu_access
    || pd.picture_mode
    || (pd.reset_settings || []).length > 0
    || (pd.disable_settings || []).length > 0
    || (pd.configure_settings || []).length > 0;

  return (
    <>
      {/* Section 1: Hardware — always expanded */}
      <CollapsibleSection title="Hardware" storageKey="prepare-hardware" defaultOpen>
        <Card title="Pattern Generator">
          <div className="text-sm muted" style={{ marginBottom: 12 }}>
            Pick the patch generator that is feeding the calibrated HDMI path into the TV.
            The setup checklist and measurement guidance below will adapt to this choice.
          </div>
          <div className="mode-grid">
            {generatorOptions.map(opt => (
              <div
                key={opt.key}
                className={`mode-card ${selectedGenerator === opt.key ? 'selected' : ''}`}
                onClick={() => handleGeneratorChange(opt.key)}
                style={{ cursor: 'pointer' }}
              >
                <div className="mode-card-label">{opt.label}</div>
                <div className="mode-card-desc">{opt.desc}</div>
              </div>
            ))}
          </div>
        </Card>

        {selectedGenerator === 'lightspace_connect' && (
          <Card title="LightSpace Connect">
            <div className="text-sm muted" style={{ marginBottom: 12 }}>
              Select your LightSpace Connect tier to configure the grayscale ramp density.
              A denser ramp gives the guidance engine more data points for better gamma
              curve fitting and ABL detection.
            </div>
            <div className="mode-grid" style={{ marginBottom: 12 }}>
              {['free', 'paid'].map(tier => (
                <div
                  key={tier}
                  className={`mode-card ${selectedTier === tier ? 'selected' : ''}`}
                  onClick={() => handleTierChange(tier)}
                  style={{ cursor: 'pointer' }}
                >
                  <div className="mode-card-label">
                    {tier === 'free' ? 'Free' : 'Paid'}
                  </div>
                  <div className="mode-card-desc">
                    {tier === 'free'
                      ? '25-patch limit — supports 11- and 21-step ramps'
                      : 'No patch limit — supports up to 51-step ramps'}
                  </div>
                </div>
              ))}
            </div>
          </Card>
        )}

        {selectedGenerator === 'dogegen' && (
          <>
            <Card title="Dogegen">
              <div className="text-sm muted">
                For the recommended HDR10 path, use `ST2084 Rec2020`, `10 bpc`, `RGB 4:4:4 Full`,
                a `10%` window on black, the app signal range set to `Full`, and the `21-step`
                grayscale ramp for pre/post checks.
              </div>
            </Card>
            <Card title="Dogegen Companion" id="dogegen-card">
              <DogegenCard
                dogegenStatus={dogegenStatus}
                session={session}
                onSaveConfig={onSaveDogegenConfig}
                onStart={onStartDogegen}
                onStop={onStopDogegen}
              />
            </Card>
          </>
        )}
      </CollapsibleSection>

      {/* Section 2: Measurement Settings — collapsed by default, shows summary */}
      <CollapsibleSection title="Measurement Settings" storageKey="prepare-measurement" defaultOpen={false} summary={measurementSummary}>
        <Card title={<>Grayscale Ramp <Tooltip text="Number of gray patches measured from 0–100% stimulus. More steps give the guidance engine more data for curve fitting and ABL detection." /></>}>
          <div className="text-sm muted" style={{ marginBottom: 12 }}>
            {selectedGenerator === 'dogegen'
              ? 'Dogegen works well with a denser grayscale sweep. For HDR10 on the U8G, use the 21-step ramp so the pre/post grayscale pages show the full 0–100% pass in 5% steps.'
              : 'Choose the grayscale ramp density that matches the LightSpace Connect tier you are using.'}
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {availableRamps.map(opt => (
              <label
                key={opt.steps}
                style={{ display: 'flex', alignItems: 'flex-start', gap: 10, cursor: 'pointer' }}
              >
                <input
                  type="radio"
                  name="ramp"
                  value={opt.steps}
                  checked={selectedSteps === opt.steps}
                  onChange={() => handleRampChange(opt.steps)}
                  style={{ marginTop: 2, flexShrink: 0 }}
                />
                <span>
                  <span className="text-sm"><strong>{opt.label}</strong></span>
                  <span className="text-sm muted" style={{ display: 'block' }}>
                    {selectedGenerator === 'dogegen' && opt.steps === 21
                      ? 'Recommended for Dogegen HDR workflows on the U8G.'
                      : opt.summary}
                  </span>
                </span>
              </label>
            ))}
          </div>
        </Card>

        <Card title={<>Signal Range <Tooltip text="Whether patch codes use full (0–255 / 0–1023) or limited (16–235 / 64–940) range. Getting this wrong shifts every stimulus percent and skews gamma readings." /></>}>
          <div className="text-sm muted" style={{ marginBottom: 12 }}>
            Match this to the patch generator's output signal range. Full Range is common on PC/HDMI
            generator paths such as Dogegen, while Limited Range is common for TV workflows such as
            Apple TV + LightSpace Connect. Getting this wrong shifts every stimulus percent and skews
            gamma readings.
          </div>
          {pd.pattern_generator_signal_range_hint && (
            <div className="text-sm muted" style={{ marginBottom: 12 }}>
              Current generator hint: {pd.pattern_generator_signal_range_hint}
            </div>
          )}
          <div className="mode-grid">
            {[
              { id: 'limited', label: 'Limited Range', desc: '8-bit 16..235 or 10-bit 64..940 (typical TV video path)' },
              { id: 'full',    label: 'Full Range',    desc: '8-bit 0..255 or 10-bit 0..1023 (recommended for Dogegen HDR on PC)' },
            ].map(opt => (
              <div
                key={opt.id}
                className={`mode-card ${selectedSignalRange === opt.id ? 'selected' : ''}`}
                onClick={() => handleSignalRangeChange(opt.id)}
                style={{ cursor: 'pointer' }}
              >
                <div className="mode-card-label">{opt.label}</div>
                <div className="mode-card-desc">{opt.desc}</div>
              </div>
            ))}
          </div>
        </Card>

        <Card title={<>Patch Code Scale <Tooltip text="Bit depth of patch codes sent to the generator. Use 10-bit for HDR Dogegen paths (0–1023) and 8-bit for standard SDR paths (0–255)." /></>}>
          <div className="text-sm muted" style={{ marginBottom: 12 }}>
            This controls the actual patch code values shown in the workflow. For Dogegen HDR on PC, use
            `10-bit Codes` so the app shows and interprets `0..1023` values instead of `0..255`.
          </div>
          <div className="mode-grid">
            {codeScaleOptions.map(opt => (
              <div
                key={opt.key}
                className={`mode-card ${selectedCodeScale === opt.key ? 'selected' : ''}`}
                onClick={() => handleCodeScaleChange(opt.key)}
                style={{ cursor: 'pointer' }}
              >
                <div className="mode-card-label">{opt.label}</div>
                <div className="mode-card-desc">{opt.desc}</div>
              </div>
            ))}
          </div>
        </Card>
      </CollapsibleSection>

      {/* Section 3: TV Setup Checklist — collapsed by default, auto-expand if service menu warning */}
      {hasChecklistContent && (
        <CollapsibleSection title="TV Setup Checklist" storageKey="prepare-checklist" defaultOpen={!!pd.service_menu_access}>
          {(pd.zro_setup_steps || []).length > 0 && (
            <div className="zro-card">
              <div className="zro-card-title">ColourSpace Setup — {session.mode}</div>
              <div className="zro-card-summary">
                Complete these steps in ColourSpace and your selected patch generator before starting calibration measurements.
              </div>
              <ol className="zro-steps">
                {pd.zro_setup_steps.map((step, i) => (
                  <li className="zro-step" key={i}>
                    <div className="zro-step-num">{i + 1}</div>
                    <div className="zro-step-body">
                      <div className="zro-step-label">{step.step}</div>
                      <div className="zro-step-detail">{step.detail}</div>
                    </div>
                  </li>
                ))}
              </ol>
            </div>
          )}

          {pd.service_menu_access && (
            <div className="zro-card warning">
              <div className="zro-card-title">⚠ Service Menu Access</div>
              <div className="text-sm" style={{ whiteSpace: 'pre-line', lineHeight: 1.6 }}>
                {pd.service_menu_access}
              </div>
            </div>
          )}

          <Card title="TV Picture Mode">
            <div className="text-sm"><strong>{pd.picture_mode || '—'}</strong></div>
            {pd.wb_menu_path    && <div className="text-sm muted mt-2">White Balance: <span className="accent">{pd.wb_menu_path}</span></div>}
            {pd.gamma_menu_path && <div className="text-sm muted">Gamma: <span className="accent">{pd.gamma_menu_path}</span></div>}
            {pd.gamma_note      && <div className="text-sm muted mt-2">{pd.gamma_note}</div>}
          </Card>

          <div className="two-col">
            {(pd.reset_settings || []).length > 0 && (
              <Card title="Reset to Defaults Before Calibrating">
                <ul className="checklist">
                  {pd.reset_settings.map((item, i) => (
                    <li key={i}>
                      <span className="checklist-label">{item.setting}</span>
                      <span className="checklist-value">→ {item.value}</span>
                    </li>
                  ))}
                </ul>
              </Card>
            )}
            {(pd.disable_settings || []).length > 0 && (
              <Card title="Disable Before Calibrating">
                <ul className="checklist">
                  {pd.disable_settings.map((item, i) => (
                    <li key={i}>
                      <span className="checklist-label">{item.setting}</span>
                      <span className="checklist-value">→ {item.value}</span>
                    </li>
                  ))}
                </ul>
              </Card>
            )}
          </div>

          {(pd.configure_settings || []).length > 0 && (
            <Card title="Configure">
              <ul className="checklist">
                {pd.configure_settings.map((item, i) => (
                  <li key={i}>
                    <span className="checklist-label">{item.setting}</span>
                    <span className="checklist-value">→ {item.value}</span>
                  </li>
                ))}
              </ul>
            </Card>
          )}
        </CollapsibleSection>
      )}

      {/* Section 4: AI Assistant — collapsed by default, user remembers preference */}
      <CollapsibleSection title="AI Assistant" storageKey="prepare-ai" defaultOpen={false}>
        <LlmHistoryCard sid={session.id} />
        <LlmConnectionCard sid={session.id} onConfigureLlm={onConfigureLlm} />
      </CollapsibleSection>

      <div className="btn-group">
        <Button onClick={onPrev}>← Back</Button>
        <Button primary onClick={onConfirmPrepared}>TV is Configured — Start Measuring</Button>
      </div>
    </>
  );
}
