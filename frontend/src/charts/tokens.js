export const TICK = { color: '#63666e', font: { family: 'Inter, sans-serif', size: 10 } };
export const GRID = { color: '#2a2d33' };

export const C = {
  cyan:      '#2ec7e6',
  cyanFill:  'rgba(46,199,230,0.08)',
  green:     '#38d29a',
  greenFill: 'rgba(56,210,154,0.08)',
  amber:     '#f5b748',
  red:       '#f0644b',
  redFill:   'rgba(240,100,75,0.06)',
  muted:     '#63666e',
};

export function deColor(v) {
  if (v == null) return C.muted;
  return v <= 2 ? C.green : v <= 3 ? C.amber : C.red;
}
