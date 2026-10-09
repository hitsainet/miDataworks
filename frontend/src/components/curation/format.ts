// Number and label formatting for feature 004's panels: every figure names its scale (R-03.62).
export const pct = (x: number, digits = 1) => `${(x * 100).toFixed(digits)}%`;
export const pretty = (name: string) => {
  const text = name.replace(/^_dw_/, '').replace(/_/g, ' ').trim();
  return text ? text[0].toUpperCase() + text.slice(1) : name;
};
export const count = (n: number) => n.toLocaleString('en-US');
