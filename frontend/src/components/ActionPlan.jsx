import { dirClass, dirIcon } from '../utils/fmt';

export function ActionPlan({ plan, title }) {
  if (!plan?.length) return null;
  return (
    <div className="card mb-0">
      <div className="card-title">{title}</div>
      {plan.map((a, i) => (
        <div className="action-card" key={i}>
          <div className={`action-icon ${dirClass(a.direction)}`}>{dirIcon(a.direction)}</div>
          <div className="action-body">
            <div className="action-summary">
              {a.summary}
              {a.priority && <span className={`badge badge-${a.priority}`}>{a.priority}</span>}
            </div>
            <div className="action-reason">{a.reason}</div>
          </div>
        </div>
      ))}
    </div>
  );
}
