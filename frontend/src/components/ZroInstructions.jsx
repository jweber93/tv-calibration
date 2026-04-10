export function ZroInstructions({ instructions }) {
  if (!instructions) return null;
  return (
    <div className="zro-card">
      <div className="zro-card-title">ColourSpace: {instructions.title}</div>
      <div className="zro-card-summary">{instructions.summary}</div>
      <ol className="zro-steps">
        {(instructions.steps || []).map((s, i) => (
          <li className="zro-step" key={i}>
            <div className="zro-step-num">{i + 1}</div>
            <div className="zro-step-body">
              <div className="zro-step-label">{s.step}</div>
              <div className="zro-step-detail">{s.detail}</div>
            </div>
          </li>
        ))}
      </ol>
      {instructions.patches?.length > 0 && (
        <div className="mt-3">
          <div className="text-sm muted" style={{ marginBottom: 6 }}>Patch reference values</div>
          <div className="patch-list">
            {instructions.patches.map((p, i) => {
              const [r, g, b] = p.rgb;
              const scale = Math.max(r, g, b) > 255 ? 255 / 1023 : 1;
              const swatch = `rgb(${Math.round(r * scale)},${Math.round(g * scale)},${Math.round(b * scale)})`;
              return (
                <div className="patch-swatch" key={i}>
                  <div className="patch-color" style={{ background: swatch }} />
                  {p.label} <span className="muted">({r},{g},{b})</span>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
