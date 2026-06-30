import '../charts/index.js';
import { Line } from 'react-chartjs-2';
import { TICK, GRID, C } from './tokens.js';

export function OverlayGammaChart({ measurementsA, measurementsB }) {
  const ptsA = (measurementsA || []).filter(m => m.effective_gamma != null && m.stimulus_pct != null);
  const ptsB = (measurementsB || []).filter(m => m.effective_gamma != null && m.stimulus_pct != null);

  if (!ptsA.length && !ptsB.length) return null;

  const allStimuli = Array.from(new Set([...ptsA, ...ptsB].map(m => m.stimulus_pct))).sort((a,b)=>a-b);
const labels = allStimuli.map(s => `${s}%`);

  const datasets = [
    {
      label: 'Session A (before)',
      data: allStimuli.map(s => ptsA.find(m => m.stimulus_pct === s)?.effective_gamma ?? null),
      borderColor: C.red, backgroundColor: C.redFill,
      tension: 0.3, pointBackgroundColor: C.red, pointRadius: 4, pointStyle: 'circle',
      fill: false, borderDash: [6, 3],
    },
    {
      label: 'Session B (after)',
      data: allStimuli.map(s => ptsB.find(m => m.stimulus_pct === s)?.effective_gamma ?? null),
      borderColor: C.green, backgroundColor: C.greenFill,
      tension: 0.3, pointBackgroundColor: C.green, pointRadius: 5, pointStyle: 'rectRot',
      fill: true,
    },
    {
      label: 'Target 2.2',
      data: allStimuli.map(() => 2.2),
      borderColor: C.muted, borderDash: [4, 4], pointRadius: 0,
    },
  ];

  return (
    <div className="chart-wrap" style={{ height: 220 }}>
      <Line
        data={{
          labels: labels,
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
