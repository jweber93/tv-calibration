/**
 * OSDStepList — displays step-by-step OSD navigation instructions from the
 * Command Translator (#167).
 *
 * Props:
 *   steps  — array of { step_number, menu_path, action, value, rationale, completed }
 *   title  — section heading (e.g. "OSD Navigation")
 *   usesAdb — boolean, shows ADB hint when true
 *   adbInstructions — string shown when usesAdb is true
 *   onToggleStep  — (stepNumber) => void callback for checkbox clicks
 *
 * Also accepts an optional `plan` object (from LLM) with next_step and confidence.
 */

import { useState } from 'react';

const SCOPE_BADGE = {
  global: { label: 'Global', color: 'var(--accent)' },
  local:  { label: 'Local',  color: 'var(--yellow, #f0a500)' },
};

function StepItem({ step, onToggle }) {
  const checked = !!step.completed;
  return (
    <div
      style={{
        display: 'flex',
        gap: 10,
        padding: '8px 0',
        borderBottom: '1px solid var(--border-subtle, rgba(255,255,255,0.06))',
        opacity: checked ? 0.6 : 1,
      }}
    >
      {/* Checkbox */}
      <input
        type="checkbox"
        checked={checked}
        onChange={() => onToggle(step.step_number)}
        style={{ marginTop: 3, accentColor: 'var(--accent)' }}
      />

      {/* Step number badge */}
      <span
        style={{
          minWidth: 24,
          height: 24,
          borderRadius: '50%',
          background: checked ? 'var(--accent-dim)' : 'var(--surface3)',
          color: checked ? 'var(--accent)' : 'var(--muted)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          fontSize: '0.75rem',
          fontWeight: 700,
        }}
      >
        {checked ? '\u2713' : step.step_number}
      </span>

      {/* Content */}
      <div style={{ flex: 1, minWidth: 0 }}>
        {/* Menu path */}
        <div
          style={{
            fontWeight: 600,
            fontSize: '0.83rem',
            color: checked ? 'var(--muted)' : 'var(--text)',
          }}
        >
          {step.menu_path}
        </div>

        {/* Action + value */}
        <div style={{ fontSize: '0.82rem', color: 'var(--accent)', marginTop: 1 }}>
          {step.action}
          {step.value && (
            <span style={{ marginLeft: 6, fontWeight: 400 }}>
              {step.value}
            </span>
          )}
        </div>

        {/* Rationale */}
        {step.rationale && (
          <div
            style={{
              fontSize: '0.78rem',
              color: checked ? 'var(--muted)' : 'var(--muted)',
              marginTop: 3,
              lineHeight: 1.45,
            }}
          >
            {step.rationale}
          </div>
        )}
      </div>
    </div>
  );
}

export function OSDStepList({ steps, title = 'OSD Navigation', usesAdb, adbInstructions, onToggleStep }) {
  const [localSteps, setLocalSteps] = useState(steps || []);

  // Sync with parent steps prop changes
  if (steps && JSON.stringify(steps) !== JSON.stringify(localSteps)) {
    setLocalSteps(steps);
  }

  const handleToggle = (stepNumber) => {
    const updated = localSteps.map((s) =>
      s.step_number === stepNumber ? { ...s, completed: !s.completed } : s
    );
    setLocalSteps(updated);
    if (onToggleStep) onToggleStep(stepNumber);
  };

  const completedCount = localSteps.filter((s) => s.completed).length;
  const totalCount = localSteps.length;

  if (!localSteps.length) return null;

  return (
    <div className="card mb-0">
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
        <div className="card-title">{title}</div>
        {totalCount > 0 && (
          <span
            style={{
              fontSize: '0.72rem',
              color: 'var(--muted)',
              marginLeft: 'auto',
            }}
          >
            {completedCount}/{totalCount} done
          </span>
        )}
      </div>

      {/* ADB hint */}
      {usesAdb && adbInstructions && (
        <div
          style={{
            fontSize: '0.8rem',
            color: 'var(--yellow, #f0a500)',
            padding: '6px 10px',
            background: 'rgba(240,165,0,0.08)',
            borderRadius: 6,
            marginBottom: 6,
          }}
        >
          {adbInstructions}
        </div>
      )}

      {/* Steps */}
      {localSteps.map((step) => (
        <StepItem key={step.step_number} step={step} onToggle={handleToggle} />
      ))}
    </div>
  );
}

/**
 * CombinedInsightCard — merges LlmInsightCard's AdjustmentPlan with OSDStepList.
 *
 * Used on calibration pages where the LLM generates both adjustment recommendations
 * and OSD navigation steps. This component shows them together so the user sees:
 *   1. What to adjust (from LLM)
 *   2. How to navigate there (OSD steps)
 *
 * Props:
 *   insight  — { text, plan: { adjustments, next_step, confidence }, phase, timestamp }
 *   streaming — bool
 *   error    — string | null
 *   onDismiss — () => void
 *   tvKey     — string, for OSD translation API call
 *   sid       — string, session ID
 */

import { useEffect, useState } from 'react';
import { LlmInsightCard } from './LlmInsightCard';
import { api } from '../api/client';

const NEXT_STEP_LABEL = {
  rerun_grayscale: 'Re-run Grayscale',
  proceed_cms:     'Proceed to CMS',
  rerun_wb:        'Re-run White Balance',
  verify:          'Verify / Done',
};

export function CombinedInsightCard({ insight, streaming, error, onDismiss, tvKey, sid }) {
  const [osdSteps, setOsdSteps] = useState([]);
  const [adbInfo, setAdbInfo] = useState({ usesAdb: false, adbInstructions: null });
  const [osdLoading, setOsdLoading] = useState(false);
  const [osdError, setOsdError] = useState(null);

  // Translate LLM plan to OSD steps when insight changes
  useEffect(() => {
    if (!insight?.plan?.adjustments?.length || !sid) return;

    setOsdLoading(true);
    setOsdError(null);

    api.translateOsd(sid, insight.plan)
      .then((result) => {
        setOsdSteps(result.steps || []);
        setAdbInfo({ usesAdb: result.uses_adb, adbInstructions: result.adb_instructions });
      })
      .catch((err) => {
        setOsdError(err.message || 'Failed to translate OSD steps');
      })
      .finally(() => setOsdLoading(false));
  }, [insight?.plan, sid]);

  const hasPlan = insight?.plan && Array.isArray(insight.plan.adjustments);

  return (
    <>
      {/* Original LLM insight card */}
      <LlmInsightCard
        insight={insight}
        streaming={streaming}
        error={error}
        onDismiss={onDismiss}
      />

      {/* OSD navigation steps (only when we have a plan) */}
      {hasPlan && !error && (
        <div style={{ marginTop: -8 }}>
          {osdLoading && (
            <div
              style={{
                padding: '10px 14px',
                fontSize: '0.82rem',
                color: 'var(--muted)',
              }}
            >
              Generating OSD navigation steps...
            </div>
          )}
          {osdError && (
            <div
              style={{
                padding: '8px 14px',
                fontSize: '0.78rem',
                color: 'var(--red)',
                background: 'rgba(231,76,60,0.05)',
                borderRadius: 4,
              }}
            >
              {osdError}
            </div>
          )}
          {!osdLoading && !osdError && (
            <OSDStepList
              steps={osdSteps}
              title="How to Navigate"
              usesAdb={adbInfo.usesAdb}
              adbInstructions={adbInfo.adbInstructions}
            />
          )}
        </div>
      )}
    </>
  );
}

// Re-export for importers
export { OSDStepList };
