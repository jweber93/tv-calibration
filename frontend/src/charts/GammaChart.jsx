import '../charts/index.js';
import { Line } from 'react-chartjs-2';
import { TICK, GRID, C } from './tokens.js';
import { isPqEotf } from '../utils/fmt.js';

export function GammaChart({ measurements, eotf }) {
  const pts = (measurements || []).filter(m => m.effective_gamma != null && m.stimulus_pct != null);
  if (!pts.length) return null;

  const pq = isPqEotf(eotf);
  const target = pq ? 1.0 : 2.2;

  return (
    <div className="chart-wrap">
      <Line
        data={{
          labels: pts.map(m => `${m.stimulus_pct}%`),
          datasets: [
            {
              label: 'Effective Gamma',
              data: pts.map(m => m.effective_gamma),
              borderColor: C.cyan, backgroundColor: C.cyanFill,
              tension: 0.3, pointBackgroundColor: C.cyan, fill: true,
            },
            {
              label: pq ? 'Target 1.0 (PQ)' : 'Target 2.2',
              data: pts.map(() => target),
              borderColor: C.muted, borderDash: [4, 4], pointRadius: 0,
            },
          ],
        }}
        options={{
          responsive: true, maintainAspectRatio: false,
          plugins: { legend: { labels: { color: C.muted, font: { size: 10 } } } },
          scales: {
            x: { ticks: TICK, grid: GRID },
            y: { ticks: TICK, grid: GRID, title: { display: true, text: 'γ', color: C.muted } },
          },
        }}
      />
    </div>
  );
}
