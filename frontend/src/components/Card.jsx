export function Card({ title, children, className = '', style }) {
  return (
    <div className={`card ${className}`} style={style}>
      {title && <div className="card-title">{title}</div>}
      {children}
    </div>
  );
}

export function StatCard({ value, label, color = '' }) {
  return (
    <div className={`stat-card ${color ? color + '-top' : ''}`}>
      <div className="stat-value">{value}</div>
      <div className="stat-label">{label}</div>
    </div>
  );
}
