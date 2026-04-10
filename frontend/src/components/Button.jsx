export function Button({ children, onClick, primary, small, danger, disabled, className = '', type = 'button' }) {
  const cls = [
    'btn',
    primary ? 'btn-primary' : '',
    small   ? 'btn-sm'      : '',
    danger  ? 'btn-danger'  : '',
    className,
  ].filter(Boolean).join(' ');

  return (
    <button type={type} className={cls} onClick={onClick} disabled={disabled}>
      {children}
    </button>
  );
}
