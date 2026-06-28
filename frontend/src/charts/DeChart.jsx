import '../charts/index.js';
import { Bar } from 'react-chartjs-2';
import { TICK, GRID, C, deColor } from './tokens.js';

export function DeChart({ measurements, label }) {
  if (!measurements?.length) return null;

  const rows = [...measurements].sort((a, b) => {
    const ap = a.stimulus_pct;
    const bp = b.stimulus_pct;
    if (ap == null && bp == null) return String(a.timestamp || '').localeCompare(String(b.timestamp || ''));
    if (ap == null) return 1;
    if (bp == null) return -1;
    return ap - bp;
  });
  const labels = rows.map(m => m.label || m.stimulus_pct + '%');
  const data   = rows.map(m => m.delta_e);
  const colors = data.map(deColor);

  return (
    <div className="chart-wrap">
      <Bar
        data={{ labels, datasets: [{ label, data, backgroundColor: colors, borderRadius: 3 }] }}
        options={{
          responsive: true, maintainAspectRatio: false,
          plugins: { legend: { display: false } },
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
