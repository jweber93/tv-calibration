import { Tooltip } from './Tooltip';

export function Card({ title, children, className = '', style, id }) {
  return (
    <div id={id} className={`card ${className}`} style={style}>
      {title && <div className="card-title">{title}</div>}
      {children}
    </div>
  );
}

export function StatCard({ value, label, color = '', tooltip }) {
  return (
    <div className={`stat-card ${color ? color + '-top' : ''}`}>
      <div className="stat-value">{value}</div>
      <div className="stat-label">{label}{tooltip && <Tooltip text={tooltip} />}</div>
    </div>
  );
}
