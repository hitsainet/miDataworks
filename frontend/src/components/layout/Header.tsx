// Origin: miStudio (Onegaishimas/miStudio) frontend/src/components/layout/Header.tsx @ c829a2cc
// Mode: adapt (docs/REUSE.md). Kept: the 56 px top bar and the theme toggle. Changed: no GPU chip
// (miDataworks owns no GPU, ADR-025); the app's own CPU, memory and disk; connection chips for
// miLLM in cyan and miStudio in emerald (R-03.59); an operations button.
import { Activity, Cpu, HardDrive, Link2, MemoryStick, Moon, Server, Sun } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';

import { APP_CHIP_CLASSES } from '@/config/brand';
import { useHealthStore } from '@/stores/healthStore';
import { useJobsStore } from '@/stores/jobsStore';
import { useUIStore } from '@/stores/uiStore';
import type { Dependency } from '@/types/api';
import { formatBytes } from '@/utils/format';

function Stat({ icon: Icon, label, value }: { icon: LucideIcon; label: string; value: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-xs whitespace-nowrap text-slate-500 dark:text-slate-400">
      <Icon size={13} aria-hidden="true" />
      {label}: <b className="font-semibold text-slate-900 dark:text-slate-100 tabular">{value}</b>
    </span>
  );
}

function AppChip({
  app,
  label,
  icon: Icon,
  dep,
}: {
  app: 'millm' | 'mistudio';
  label: string;
  icon: LucideIcon;
  dep: Dependency | undefined;
}) {
  const configured = dep?.configured !== false;
  const state = !dep ? 'checking' : !configured ? 'not configured' : dep.ok ? 'connected' : 'unreachable';
  // Another app's state is drawn in that app's colour, and only when it is actually connected;
  // an unknown or failed state is neutral or red, never the app colour (R-03.59).
  const tone =
    state === 'connected'
      ? APP_CHIP_CLASSES[app]
      : state === 'unreachable'
        ? 'bg-red-500/10 text-red-700 dark:text-red-400'
        : 'bg-slate-500/10 text-slate-500 dark:text-slate-400';
  return (
    <span
      data-testid={`chip-${app}`}
      title={dep?.reason ?? undefined}
      className={`inline-flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-full ${tone}`}
    >
      <Icon size={12} aria-hidden="true" /> {label} · {state}
    </span>
  );
}

export function Header({ onOpenOperations }: { onOpenOperations: () => void }) {
  const theme = useUIStore((s) => s.theme);
  const toggleTheme = useUIStore((s) => s.toggleTheme);
  const health = useHealthStore((s) => s.health);
  const unreachable = useHealthStore((s) => s.unreachable);
  const activeCount = useJobsStore((s) => s.active.length);
  const r = health?.resources;

  return (
    <header className="h-14 flex flex-wrap items-center justify-end gap-x-5 gap-y-1 px-6 border-b border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900" data-testid="topbar">
      {unreachable && (
        <span className="text-xs text-red-700 dark:text-red-400">The miDataworks API is unreachable; figures are stale.</span>
      )}
      <Stat icon={Cpu} label="CPU" value={r ? `${r.cpu_percent.toFixed(0)}%` : '—'} />
      <Stat icon={MemoryStick} label="RAM" value={r ? `${formatBytes(r.memory_used_bytes)} / ${formatBytes(r.memory_total_bytes)}` : '—'} />
      <Stat icon={HardDrive} label="Disk" value={r ? `${formatBytes(r.disk_used_bytes)} / ${formatBytes(r.disk_total_bytes)}` : '—'} />
      <AppChip app="millm" label="miLLM" icon={Server} dep={health?.dependencies.millm} />
      <AppChip app="mistudio" label="miStudio" icon={Link2} dep={health?.dependencies.mistudio} />
      <button
        type="button"
        onClick={onOpenOperations}
        className="inline-flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-lg border border-slate-200 dark:border-slate-700 text-slate-700 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-800"
      >
        <Activity size={13} aria-hidden="true" /> Show operations{activeCount ? ` (${activeCount} active)` : ''}
      </button>
      <button
        type="button"
        onClick={toggleTheme}
        className="p-1.5 rounded-lg text-slate-500 dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-800"
        aria-label={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
        data-testid="theme-toggle"
      >
        {theme === 'dark' ? <Sun size={16} /> : <Moon size={16} />}
      </button>
    </header>
  );
}
