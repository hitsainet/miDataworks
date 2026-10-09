// Visual tokens (mockup handoff section 3; ADR-020). Defined once here and read by
// tailwind.config.ts and by any component that needs a raw value (charts, SVG).
//
// Indigo is ACCENT ONLY: the active nav item, primary buttons, the logo tile, icon tiles on list
// rows, running progress and the Labeling/Running pills, and the positive class in the judge
// histogram. Anything else that looks indigo is a review finding.
//
// Another app's state is drawn in THAT app's colour: cyan for miLLM, emerald for miStudio,
// orange for miForge (decision D10, R-03.59).

export const APP_NAME = 'miDataworks';
export const APP_TAGLINE = 'Dataset Curation & Labeling';
export const APP_VERSION = '0.1.0';

/** Dark values (the mockup) and their light-mode counterparts. */
export const tokens = {
  bg: { dark: '#0f1629', light: '#f8fafc' }, // slate-950 (miStudio override) / slate-50
  panel: { dark: '#0f172a', light: '#ffffff' }, // slate-900 / white
  card: { dark: '#1e293b', light: '#ffffff' }, // slate-800 / white
  line: { dark: '#334155', light: '#e2e8f0' }, // slate-700 / slate-200
  text: { dark: '#f1f5f9', light: '#0f172a' }, // slate-100 / slate-900
  dim: { dark: '#94a3b8', light: '#64748b' }, // slate-400 / slate-500
  accent: { dark: '#818cf8', light: '#4f46e5' }, // indigo-400 / indigo-600
  accentFill: { dark: '#6366f1', light: '#6366f1' }, // indigo-500
  accentHover: { dark: '#4f46e5', light: '#4f46e5' }, // indigo-600
  green: { dark: '#4ade80', light: '#16a34a' },
  amber: { dark: '#fbbf24', light: '#d97706' },
  red: { dark: '#f87171', light: '#dc2626' },
  cyan: { dark: '#22d3ee', light: '#0891b2' }, // miLLM state
  emerald: { dark: '#34d399', light: '#059669' }, // miStudio state
  orange: { dark: '#fb923c', light: '#ea580c' }, // miForge state
} as const;

/** Which colour names another app's state (R-03.59). */
export const APP_COLORS = {
  millm: 'cyan',
  mistudio: 'emerald',
  miforge: 'orange',
} as const;

/** Tailwind theme extension: the miStudio slate-950 override. */
export const tailwindColors = {
  slate: { 950: '#0f1629' },
};

/** Class fragments for app-state chips, light and dark (one place, so the rule holds). */
export const APP_CHIP_CLASSES: Record<keyof typeof APP_COLORS, string> = {
  millm: 'bg-cyan-500/10 text-cyan-700 dark:text-cyan-400',
  mistudio: 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-400',
  miforge: 'bg-orange-500/10 text-orange-700 dark:text-orange-400',
};

/** Status colours: green, amber, red. Never indigo. */
export const STATUS_CLASSES = {
  ok: 'bg-green-500/15 text-green-700 dark:text-green-400',
  warn: 'bg-amber-500/15 text-amber-700 dark:text-amber-400',
  error: 'bg-red-500/15 text-red-700 dark:text-red-400',
  neutral: 'bg-slate-500/15 text-slate-600 dark:text-slate-400',
  running: 'bg-indigo-500/15 text-indigo-700 dark:text-indigo-400',
} as const;
