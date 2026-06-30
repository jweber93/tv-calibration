import '../charts/index.js';
import { Bar } from 'react-chartjs-2';
import { TICK, GRID, C } from './tokens.js';

export function OverlayDeChart({ measurementsA, measurementsB }) {
  if (!(measurementsA || []).length && !(measurementsB || []).length) return null;

  const labelFor = m => m.label ?? (m.stimulus_pct != null ? `${m.stimulus_pct}%` : '');

  const toNum = s => {
    const n = parseInt(s, 10);
    return Number.isFinite(n) ? n : null;
  };

  const allLabels = Array.from(new Set([
    ...(measurementsA || []).map(labelFor),
    ...(measurementsB || []).map(labelFor),
  ])).sort((a, b) => {
    const pa = toNum(a);
    const pb = toNum(b);
    if (pa === null && pb === null) return a.localeCompare(b);
    if (pa === null) return 1;
    if (pb === null) return -1;
    return pa - pb;
  });

  function getDelta(measurements, label) {
    const match = (measurements || []).find(m => labelFor(m) === label);
    return match ? match.delta_e : null;
  }

  const colorsA = allLabels.map(l => {
    const v = getDelta(measurementsA, l);
    if (v == null) return C.redFillMissing;
    return v <= 2 ? C.red : v <= 3 ? C.amber : C.red;
  });

  const colorsB = allLabels.map(l => {
    const v = getDelta(measurementsB, l);
    if (v == null) return C.greenFillMissing;
    return v <= 2 ? C.green : v <= 3 ? C.amber : C.red;
  });

  return (
    <div className="chart-wrap" style={{ height: 220 }}>
      <Bar
        data={{
          labels: allLabels,
          datasets: [
            {
              label: 'Session A (before)',
              data: allLabels.map(l => getDelta(measurementsA, l)),
              backgroundColor: colorsA,
              borderRadius: 3,
              barPercentage: 0.6,
            },
            {
              label: 'Session B (after)',
              data: allLabels.map(l => getDelta(measurementsB, l)),
              backgroundColor: colorsB,
              borderRadius: 3,
              barPercentage: 0.6,
            },
          ],
        }}
        options={{
          responsive: true, maintainAspectRatio: false,
          plugins: {
            legend: { labels: { color: C.muted, font: { size: 10 } } },
          },
          scales: {
            x: { ticks: TICK, grid: GRID },
            y: { beginAtZero: true, ticks: TICK, grid: GRID, title: { display: true, text: 'ΔE', color: C.muted, font: { size: 10 } } },
          },
          animation: { duration: 300 },
        }}
      />
    </div>
  );
}
