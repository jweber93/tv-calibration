import '../charts/index.js';
import { Line } from 'react-chartjs-2';

const TICK = { color: '#74757c', font: { size: 10 } };
const GRID = { color: '#2e2f33' };

export function OverlayGammaChart({ measurementsA, measurementsB }) {
  const ptsA = (measurementsA || []).filter(m => m.effective_gamma != null);
  const ptsB = (measurementsB || []).filter(m => m.effective_gamma != null);

  if (!ptsA.length && !ptsB.length) return null;

  const labelsA = ptsA.map(m => m.stimulus_pct + '%');
  const labelsB = ptsB.map(m => m.stimulus_pct + '%');

  const datasets = [
    {
      label: 'Session A (before)',
      data: ptsA.map(m => m.effective_gamma),
      borderColor: '#e74c3c', backgroundColor: 'rgba(231,76,60,0.06)',
      tension: 0.3, pointBackgroundColor: '#e74c3c', pointRadius: 4, pointStyle: 'circle',
      fill: false, borderDash: [6, 3],
    },
    {
      label: 'Session B (after)',
      data: ptsB.map(m => m.effective_gamma),
      borderColor: '#22c987', backgroundColor: 'rgba(34,201,135,0.08)',
      tension: 0.3, pointBackgroundColor: '#22c987', pointRadius: 5, pointStyle: 'rectRot',
      fill: true,
    },
    {
      label: 'Target 2.2',
      data: ptsB.map(() => 2.2),
      borderColor: '#74757c', borderDash: [4, 4], pointRadius: 0,
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
            legend: { labels: { color: '#74757c', font: { size: 10 } } },
          },
          scales: {
            x: { ticks: TICK, grid: GRID },
            y: { ticks: TICK, grid: GRID, title: { display: true, text: 'γ', color: '#74757c' } },
          },
        }}
      />
    </div>
  );
}
