import '../charts/index.js';
import { Scatter } from 'react-chartjs-2';

const TICK = { color: '#74757c', font: { size: 9 } };
const GRID = { color: '#2e2f33' };

export function CIEScatter({ measurements, targetXy }) {
  const pts = (measurements || []).filter(m => m.x && m.y);
  const datasets = [];

  if (pts.length) {
    datasets.push({
      label: 'Measured',
      data: pts.map(m => ({ x: m.x, y: m.y, label: m.label })),
      backgroundColor: '#00b4d8', pointRadius: 5,
    });
  }
  if (targetXy) {
    datasets.push({
      label: 'D65 Target',
      data: [{ x: targetXy[0], y: targetXy[1] }],
      backgroundColor: '#22c987', pointRadius: 8, pointStyle: 'crossRot',
      borderColor: '#22c987', borderWidth: 2,
    });
  }

  if (!datasets.length) return null;

  return (
    <div className="chart-wrap">
      <Scatter
        data={{ datasets }}
        options={{
          responsive: true, maintainAspectRatio: false,
          plugins: {
            legend: { labels: { color: '#74757c', font: { size: 10 } } },
            tooltip: {
              callbacks: {
                label: ctx => `${ctx.raw.label || ''} (${ctx.raw.x?.toFixed(4)}, ${ctx.raw.y?.toFixed(4)})`,
              },
            },
          },
          scales: {
            x: { min: 0.2, max: 0.45, ticks: TICK, grid: GRID, title: { display: true, text: 'CIE x', color: '#74757c' } },
            y: { min: 0.2, max: 0.45, ticks: TICK, grid: GRID, title: { display: true, text: 'CIE y', color: '#74757c' } },
          },
        }}
      />
    </div>
  );
}
