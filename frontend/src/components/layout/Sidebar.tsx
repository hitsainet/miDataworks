// Origin: miStudio (Onegaishimas/miStudio) frontend/src/components/layout/Sidebar.tsx @ c829a2cc
// Mode: adapt (docs/REUSE.md). Kept: the 224 px sidebar collapsing to 64 px, `rounded-lg px-3
// py-2.5` items, the foot with Settings and version. Changed: nav items come from the panel
// registry (config/panels.ts) instead of a local list; indigo accent with the mockup's 32 px
// logo tile and white Factory icon; a scrolling tab strip below 768 px (handoff section 6).
import { ChevronLeft, ChevronRight, Factory } from 'lucide-react';

import { APP_NAME, APP_TAGLINE, APP_VERSION } from '@/config/brand';
import { PANELS } from '@/config/panels';
import type { ActivePanel, PanelDef } from '@/config/panels';
import { useUIStore } from '@/stores/uiStore';

interface SidebarProps {
  activePanel: ActivePanel;
  onPanelChange: (panel: ActivePanel) => void;
}

const mainItems: readonly PanelDef[] = PANELS.filter((p: PanelDef) => !p.foot);
const footItems: readonly PanelDef[] = PANELS.filter((p: PanelDef) => p.foot);

function itemClass(active: boolean, collapsed: boolean): string {
  return [
    'w-full flex items-center gap-3 px-3 py-2.5 rounded-lg mb-0.5 text-left transition-colors',
    active
      ? 'bg-indigo-500/10 text-indigo-600 dark:text-indigo-400'
      : 'text-slate-600 dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-800 hover:text-slate-900 dark:hover:text-slate-200',
    collapsed ? 'justify-center' : '',
  ].join(' ');
}

export function Sidebar({ activePanel, onPanelChange }: SidebarProps) {
  const collapsed = useUIStore((s) => s.sidebar.collapsed);
  const toggleSidebar = useUIStore((s) => s.toggleSidebar);

  const navButton = (panel: PanelDef) => {
    const Icon = panel.icon;
    return (
      <button
        key={panel.id}
        type="button"
        data-testid={`nav-${panel.id}`}
        aria-current={activePanel === panel.id ? 'page' : undefined}
        onClick={() => onPanelChange(panel.id as ActivePanel)}
        className={itemClass(activePanel === panel.id, collapsed)}
        title={collapsed ? panel.label : undefined}
      >
        <Icon className="w-[18px] h-[18px] shrink-0" aria-hidden="true" />
        {!collapsed && <span className="text-sm font-medium">{panel.label}</span>}
      </button>
    );
  };

  return (
    <aside
      data-testid="sidebar"
      className={`hidden md:flex shrink-0 flex-col h-screen sticky top-0 border-r border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 transition-all duration-200 ${collapsed ? 'w-16' : 'w-56'}`}
    >
      <div className="h-14 flex items-center gap-3 px-4 border-b border-slate-200 dark:border-slate-700">
        <div className="w-8 h-8 rounded-lg flex items-center justify-center shrink-0 bg-indigo-500" data-testid="logo-tile">
          <Factory size={18} color="#fff" aria-hidden="true" />
        </div>
        {!collapsed && (
          <div className="min-w-0">
            <div className="font-semibold leading-tight text-slate-900 dark:text-slate-100">{APP_NAME}</div>
            <div className="text-[10px] text-slate-500 dark:text-slate-400 truncate">{APP_TAGLINE}</div>
          </div>
        )}
      </div>
      <nav className="p-2 flex-1 overflow-y-auto" aria-label="Screens">
        {mainItems.map(navButton)}
      </nav>
      <div className="p-2 border-t border-slate-200 dark:border-slate-700">
        {footItems.map(navButton)}
        <div className="flex items-center justify-between px-3 pt-2">
          {!collapsed && <span className="text-xs font-mono text-slate-500 dark:text-slate-400">v{APP_VERSION}</span>}
          <button
            type="button"
            onClick={toggleSidebar}
            className="p-1 rounded-lg text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800"
            aria-label={collapsed ? 'Expand the sidebar' : 'Collapse the sidebar'}
          >
            {collapsed ? <ChevronRight size={16} /> : <ChevronLeft size={16} />}
          </button>
        </div>
      </div>
    </aside>
  );
}

/** Below 768 px the sidebar becomes a scrolling tab strip (handoff section 6). */
export function MobileTabs({ activePanel, onPanelChange }: SidebarProps) {
  return (
    <div className="md:hidden flex gap-1 overflow-x-auto px-4 py-2 border-b border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900" data-testid="mobile-tabs">
      {PANELS.map((panel: PanelDef) => (
        <button
          key={panel.id}
          type="button"
          onClick={() => onPanelChange(panel.id as ActivePanel)}
          className={`px-3 py-1.5 rounded-lg text-xs whitespace-nowrap ${activePanel === panel.id ? 'bg-indigo-500/10 text-indigo-600 dark:text-indigo-400' : 'text-slate-500 dark:text-slate-400'}`}
        >
          {panel.label}
        </button>
      ))}
    </div>
  );
}
