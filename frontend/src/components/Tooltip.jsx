import { useState } from 'react';

export function Tooltip({ text }) {
  const [visible, setVisible] = useState(false);

  return (
    <span className="tooltip-wrap">
      <span
        className="tooltip-trigger"
        tabIndex={0}
        role="button"
        aria-label="More information"
        onMouseEnter={() => setVisible(true)}
        onMouseLeave={() => setVisible(false)}
        onFocus={() => setVisible(true)}
        onBlur={() => setVisible(false)}
      >
        ⓘ
      </span>
      {visible && (
        <span className="tooltip-popover" role="tooltip">
          {text}
        </span>
      )}
    </span>
  );
}
