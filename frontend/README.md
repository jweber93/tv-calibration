# Frontend Source

This directory is reserved for the React source tree that produces
`static/assets/index.js` and `static/assets/index.css`.

The original React source has not yet been committed to this repository
(see project README). The compiled assets in `static/assets/` are the
canonical served frontend.

## AiInsightPanel

The AI Calibration Coach panel lives in `static/assets/ai-panel.js` and
`static/assets/ai-panel.css`. It is a standalone native ES module — no
build step required — that overlays the compiled React app.

### Architecture

```
static/assets/
  index.js       # Compiled React app (source not yet in repo)
  index.css      # Compiled Tailwind CSS
  ai-panel.js    # AiInsightPanel — vanilla ES module (this issue)
  ai-panel.css   # Panel styles
```

### How the panel works

1. On load it calls `GET /api/session` to find the current session.
2. It subscribes to `GET /events/{sid}` (SSE) to receive session updates.
3. When `session.step` is one of `white_balance`, `gamma`, `color_tuner`,
   or `post_grayscale` **and** `session.llm_config.endpoint` + `.model`
   are both set, the panel becomes visible.
4. It opens `GET /api/session/{sid}/llm/stream` (SSE) and shows a spinner.
5. On `llm_insight` it renders the markdown-formatted coaching text.
6. On `llm_error` it renders a dismissable error state with a Retry button.
7. The panel can be collapsed (toggle ▼/▲) or fully dismissed (✕).
   Dismissal resets automatically when the step changes.

### When to migrate to React

Once the React source tree is added to this directory, the `AiInsightPanel`
should be ported to a proper React component with a `useLlmStream` hook and
mounted inside the `white_balance`, `gamma`, `color_tuner`, and
`post_grayscale` step pages. The standalone files in `static/assets/` can
then be removed.
