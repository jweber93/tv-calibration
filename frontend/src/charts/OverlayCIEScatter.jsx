import '../charts/index.js';
import { Scatter } from 'react-chartjs-2';
import { TICK, GRID, C } from './tokens.js';

const TICK_SM = { ...TICK, font: { ...TICK.font, size: 9 } };

export function OverlayCIEScatter({ measurementsA, measurementsB, targetXy }) {
  const ptsA = (measurementsA || []).filter(m => m.x && m.y);
  const ptsB = (measurementsB || []).filter(m => m.x && m.y);

  if (!ptsA.length && !ptsB.length) return null;

  const datasets = [];

  if (ptsA.length) {
    datasets.push({
      label: 'Session A (before)',
      data: ptsA.map(m => ({ x: m.x, y: m.y, label: m.label })),
      backgroundColor: 'rgba(240,100,75,0.5)',
      pointRadius: 5, pointStyle: 'circle',
    });
  }

  if (ptsB.length) {
    datasets.push({
      label: 'Session B (after)',
      data: ptsB.map(m => ({ x: m.x, y: m.y, label: m.label })),
      backgroundColor: 'rgba(56,210,154,0.6)',
      pointRadius: 6, pointStyle: 'rectRot',
    });
  }

  if (targetXy) {
    datasets.push({
      label: 'D65 Target',
      data: [{ x: targetXy[0], y: targetXy[1] }],
      backgroundColor: C.amber,
      pointRadius: 8, pointStyle: 'crossRot',
      borderColor: C.amber, borderWidth: 2,
    });
  }

  return (
    <div className="chart-wrap" style={{ height: 220 }}>
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
