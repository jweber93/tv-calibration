import '../charts/index.js';
import { Line } from 'react-chartjs-2';
import { TICK, GRID, C } from './tokens.js';

export function OverlayGammaChart({ measurementsA, measurementsB }) {
  const ptsA = (measurementsA || []).filter(m => m.effective_gamma != null);
  const ptsB = (measurementsB || []).filter(m => m.effective_gamma != null);

  if (!ptsA.length && !ptsB.length) return null;

  const labelsB = ptsB.map(m => m.stimulus_pct + '%');

  const datasets = [
    {
      label: 'Session A (before)',
      data: ptsA.map(m => m.effective_gamma),
      borderColor: C.red, backgroundColor: C.redFill,
      tension: 0.3, pointBackgroundColor: C.red, pointRadius: 4, pointStyle: 'circle',
      fill: false, borderDash: [6, 3],
    },
    {
      label: 'Session B (after)',
      data: ptsB.map(m => m.effective_gamma),
      borderColor: C.green, backgroundColor: C.greenFill,
      tension: 0.3, pointBackgroundColor: C.green, pointRadius: 5, pointStyle: 'rectRot',
      fill: true,
    },
    {
      label: 'Target 2.2',
      data: ptsB.map(() => 2.2),
      borderColor: C.muted, borderDash: [4, 4], pointRadius: 0,
    },
  ];

  return (
    <div className="chart-wrap" style={{ height: 220 }}>
      <Line
        data={{
          labels: labelsB,
          datasets,
        }}
        options={{
          responsive: true, maintainAspectRatio: false,
          plugins: {
            legend: { labels: { color: C.muted, font: { size: 10 } } },
          },
          scales: {
            x: { ticks: TICK, grid: GRID },
            y: { ticks: TICK, grid: GRID, title: { display: true, text: 'γ', color: C.muted } },
          },
        }}
      />
    </div>
  );
}
