import '../charts/index.js';
import { Line } from 'react-chartjs-2';
import { TICK, GRID, C } from './tokens.js';
import { isPqEotf } from '../utils/fmt.js';

export function OverlayGammaChart({ measurementsA, measurementsB, eotf }) {
  const rawA = measurementsA ?? [];
  const rawB = measurementsB ?? [];

  const ptsA = (Array.isArray(rawA) ? rawA : []).filter(m => m?.effective_gamma != null && m?.stimulus_pct != null);
  const ptsB = (Array.isArray(rawB) ? rawB : []).filter(m => m?.effective_gamma != null && m?.stimulus_pct != null);

  if (!ptsA.length && !ptsB.length) return null;

  const mapA = new Map(ptsA.map(m => [Number(m.stimulus_pct), m.effective_gamma]));
  const mapB = new Map(ptsB.map(m => [Number(m.stimulus_pct), m.effective_gamma]));

  const allStimuli = Array.from(new Set([...mapA.keys(), ...mapB.keys()])).sort((a, b) => a - b);
  const labels = allStimuli.map(s => `${s}%`);

  const dataA = allStimuli.map(s => (mapA.has(s) ? mapA.get(s) : null));
  const dataB = allStimuli.map(s => (mapB.has(s) ? mapB.get(s) : null));

  const pq = isPqEotf(eotf);
  const target = pq ? 1.0 : 2.2;

  const datasets = [
    {
      label: 'Session A (before)',
      data: dataA,
      borderColor: C.red, backgroundColor: C.redFill,
      tension: 0.3, pointBackgroundColor: C.red, pointRadius: 4, pointStyle: 'circle',
      fill: false, borderDash: [6, 3],
    },
    {
      label: 'Session B (after)',
      data: dataB,
      borderColor: C.green, backgroundColor: C.greenFill,
      tension: 0.3, pointBackgroundColor: C.green, pointRadius: 5, pointStyle: 'rectRot',
      fill: true,
    },
    {
      label: pq ? 'Target 1.0 (PQ)' : 'Target 2.2',
      data: allStimuli.map(() => target),
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
