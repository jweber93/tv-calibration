import '../charts/index.js';
import { Scatter } from 'react-chartjs-2';
import { TICK, GRID, C } from './tokens.js';

const TICK_SM = { ...TICK, font: { ...TICK.font, size: 9 } };

export function CIEScatter({ measurements, targetXy }) {
  const pts = (measurements || []).filter(m => m.x && m.y);
  const datasets = [];

  if (pts.length) {
    datasets.push({
      label: 'Measured',
      data: pts.map(m => ({ x: m.x, y: m.y, label: m.label })),
      backgroundColor: C.cyan, pointRadius: 5,
    });
  }
  if (targetXy) {
    datasets.push({
      label: 'D65 Target',
      data: [{ x: targetXy[0], y: targetXy[1] }],
      backgroundColor: C.green, pointRadius: 8, pointStyle: 'crossRot',
      borderColor: C.green, borderWidth: 2,
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
            legend: { labels: { color: C.muted, font: { size: 10 } } },
            tooltip: {
              callbacks: {
                label: ctx => `${ctx.raw.label || ''} (${ctx.raw.x?.toFixed(4)}, ${ctx.raw.y?.toFixed(4)})`,
              },
            },
          },
          scales: {
            x: { min: 0.2, max: 0.45, ticks: TICK_SM, grid: GRID, title: { display: true, text: 'CIE x', color: C.muted } },
            y: { min: 0.2, max: 0.45, ticks: TICK_SM, grid: GRID, title: { display: true, text: 'CIE y', color: C.muted } },
          },
        }}
      />
    </div>
  );
}
