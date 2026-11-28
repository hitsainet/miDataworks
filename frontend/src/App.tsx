// The shell (R-03.58, ADR-020): sidebar, top bar, approvals banner, the active screen and the
// operations drawer. The active screen is read from the URL hash or the persisted choice, both
// narrowed through the one panel registry.
import { useEffect, useState } from 'react';

import ErrorBoundary from '@/components/common/ErrorBoundary';
import { ToastContainer } from '@/components/common/Toast';
import { ApprovalsBanner } from '@/components/approvals/ApprovalsBanner';
import { useHelpTopic } from '@/components/help/helpTopicStore';
import { Header } from '@/components/layout/Header';
import { Sidebar } from '@/components/layout/Sidebar';
import { OperationsDrawer } from '@/components/operations/OperationsDrawer';
import { PinPrompt } from '@/components/settings/PinPrompt';
import { PANEL_COMPONENTS } from '@/config/panelComponents';
import { DEFAULT_PANEL, getPanel, isActivePanel } from '@/config/panels';
import type { ActivePanel } from '@/config/panels';
import { useHealthStore } from '@/stores/healthStore';
import { safeStorage } from '@/stores/safeStorage';
import { applyTheme, useUIStore } from '@/stores/uiStore';
import { hashPath } from '@/utils/navigate';

export const PANEL_STORAGE_KEY = 'midataworks-active-panel';
export const HEALTH_POLL_MS = 10000;

export function initialPanel(): ActivePanel {
  const { panel: fromHash, sub } = hashPath();
  if (isActivePanel(fromHash)) {
    if (fromHash === 'help' && sub) useHelpTopic.getState().open(sub); // #/help/<topic> (ENH-004 D2)
    return fromHash;
  }
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
    const onHash = () => {
      const { panel: next, sub } = hashPath();
      if (!isActivePanel(next)) return;
      if (next === 'help' && sub) useHelpTopic.getState().open(sub);
      setPanel(next);
    };
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);
  // A screen change closes the operations drawer (finding 10: it stayed open over "Use this source").
  const [opsPanel, setOpsPanel] = useState(panel);
  if (opsPanel !== panel) {
    setOpsPanel(panel);
    setOpsOpen(false);
  }
  useEffect(() => {
    void safeStorage.setItem(PANEL_STORAGE_KEY, panel);
    // The screen's sub-path (a Help topic) is kept; only a hash naming another screen is replaced.
    if (hashPath().panel !== panel) window.history.replaceState(null, '', `#/${panel}`);
  }, [panel]);

  const def = getPanel(panel);
  const Screen = PANEL_COMPONENTS[panel];
  return (
    <div className="min-h-screen flex text-sm font-sans bg-slate-50 dark:bg-slate-950 text-slate-900 dark:text-slate-100">
      <Sidebar activePanel={panel} onPanelChange={setPanel} />
      <main className="flex-1 min-w-0">
        <Header onOpenOperations={() => setOpsOpen((v) => !v)} />
        <div className="px-4 md:px-8 py-6 max-w-6xl" data-testid={`screen-${panel}`}>
          <ApprovalsBanner onApprovalsScreen={panel === 'approvals'} />
          <ErrorBoundary key={panel}>
            <Screen panel={def} />
          </ErrorBoundary>
        </div>
      </main>
      <OperationsDrawer open={opsOpen} onClose={() => setOpsOpen(false)} />
      <ToastContainer />
      {/* Feature 014: asks for the Settings PIN when a write is refused, on any screen. */}
      <PinPrompt />
    </div>
  );
}
