import '../charts/index.js';
import { Line } from 'react-chartjs-2';

const TICK = { color: '#74757c', font: { size: 10 } };
const GRID = { color: '#2e2f33' };

export function GammaChart({ measurements }) {
  const pts = (measurements || []).filter(m => m.effective_gamma != null);
  if (!pts.length) return null;

  return (
    <div className="chart-wrap">
      <Line
        data={{
          labels: pts.map(m => m.stimulus_pct + '%'),
          datasets: [
            {
              label: 'Effective Gamma',
              data: pts.map(m => m.effective_gamma),
              borderColor: '#00b4d8', backgroundColor: 'rgba(0,180,216,0.08)',
              tension: 0.3, pointBackgroundColor: '#00b4d8', fill: true,
            },
            {
              label: 'Target 2.2',
              data: pts.map(() => 2.2),
              borderColor: '#74757c', borderDash: [4, 4], pointRadius: 0,
            },
          ],
        }}
        options={{
          responsive: true, maintainAspectRatio: false,
          plugins: { legend: { labels: { color: '#74757c', font: { size: 10 } } } },
          scales: {
            x: { ticks: TICK, grid: GRID },
            y: { ticks: TICK, grid: GRID, title: { display: true, text: 'γ', color: '#74757c' } },
          },
        }}
      />
    </div>
  );
}
