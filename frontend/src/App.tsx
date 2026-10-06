// The shell (R-03.58, ADR-020): sidebar, top bar, approvals banner, the active screen and the
// operations drawer. The active screen is read from the URL hash or the persisted choice, both
// narrowed through the one panel registry.
import { useEffect, useState } from 'react';

import ErrorBoundary from '@/components/common/ErrorBoundary';
import { ToastContainer } from '@/components/common/Toast';
import { ApprovalsBanner } from '@/components/approvals/ApprovalsBanner';
import { Header } from '@/components/layout/Header';
import { MobileTabs, Sidebar } from '@/components/layout/Sidebar';
import { OperationsDrawer } from '@/components/operations/OperationsDrawer';
import { EmptyPanel } from '@/components/panels/EmptyPanel';
import { SettingsPanel } from '@/components/panels/SettingsPanel';
import { DEFAULT_PANEL, getPanel, isActivePanel } from '@/config/panels';
import type { ActivePanel } from '@/config/panels';
import { useHealthStore } from '@/stores/healthStore';
import { safeStorage } from '@/stores/safeStorage';
import { applyTheme, useUIStore } from '@/stores/uiStore';

export const PANEL_STORAGE_KEY = 'midataworks-active-panel';
export const HEALTH_POLL_MS = 10000;

export function initialPanel(): ActivePanel {
  const fromHash = window.location.hash.replace(/^#\/?/, '');
  if (isActivePanel(fromHash)) return fromHash;
  const stored = safeStorage.getItem(PANEL_STORAGE_KEY);
  return isActivePanel(typeof stored === 'string' ? stored : null) ? (stored as ActivePanel) : DEFAULT_PANEL;
}

export default function App() {
  const [panel, setPanel] = useState<ActivePanel>(initialPanel);
  const [opsOpen, setOpsOpen] = useState(false);
  const theme = useUIStore((s) => s.theme);
  const fetchHealth = useHealthStore((s) => s.fetchHealth);

  useEffect(() => applyTheme(theme), [theme]);
  useEffect(() => {
    void fetchHealth();
    const timer = setInterval(() => void fetchHealth(), HEALTH_POLL_MS);
    return () => clearInterval(timer);
  }, [fetchHealth]);
  useEffect(() => {
    void safeStorage.setItem(PANEL_STORAGE_KEY, panel);
    if (window.location.hash !== `#/${panel}`) window.history.replaceState(null, '', `#/${panel}`);
  }, [panel]);

  const def = getPanel(panel);
  return (
    <div className="min-h-screen flex text-sm font-sans bg-slate-50 dark:bg-slate-950 text-slate-900 dark:text-slate-100">
      <Sidebar activePanel={panel} onPanelChange={setPanel} />
      <main className="flex-1 min-w-0">
        <Header onOpenOperations={() => setOpsOpen(true)} />
        <MobileTabs activePanel={panel} onPanelChange={setPanel} />
        <div className="px-4 md:px-8 py-6 max-w-6xl" data-testid={`screen-${panel}`}>
          <ApprovalsBanner />
          <ErrorBoundary key={panel}>
            {panel === 'settings' ? <SettingsPanel panel={def} /> : <EmptyPanel panel={def} />}
          </ErrorBoundary>
        </div>
      </main>
      <OperationsDrawer open={opsOpen} onClose={() => setOpsOpen(false)} />
      <ToastContainer />
    </div>
  );
}
