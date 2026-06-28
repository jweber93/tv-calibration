import '../charts/index.js';
import { Bar } from 'react-chartjs-2';
import { TICK, GRID, C } from './tokens.js';

export function OverlayDeChart({ measurementsA, measurementsB }) {
  if (!measurementsA?.length && !measurementsB?.length) return null;

  const allLabels = Array.from(new Set([
    ...measurementsA.map(m => m.label || m.stimulus_pct + '%'),
    ...measurementsB.map(m => m.label || m.stimulus_pct + '%'),
  ])).sort((a, b) => {
    const pa = parseInt(a, 10);
    const pb = parseInt(b, 10);
    if (!isNaN(pa) && !isNaN(pb)) return pa - pb;
    return a.localeCompare(b);
  });

  function getDelta(measurements, label) {
    const match = measurements.find(m => (m.label || m.stimulus_pct + '%') === label);
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
