import '../charts/index.js';
import { Bar } from 'react-chartjs-2';

const TICK = { color: '#74757c', font: { size: 10 } };
const GRID = { color: '#2e2f33' };

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
  const colors = data.map(v => v <= 2 ? '#22c987' : v <= 3 ? '#f5a623' : '#e74c3c');

  return (
    <div className="chart-wrap">
      <Bar
        data={{ labels, datasets: [{ label, data, backgroundColor: colors, borderRadius: 3 }] }}
        options={{
          responsive: true, maintainAspectRatio: false,
          plugins: { legend: { display: false } },
          scales: {
            x: { ticks: TICK, grid: GRID },
            y: { beginAtZero: true, ticks: TICK, grid: GRID, title: { display: true, text: 'ΔE', color: '#74757c', font: { size: 10 } } },
          },
          animation: { duration: 300 },
        }}
      />
    </div>
  );
}
