// The Settings PIN card (feature 014; operator directive 2026-10-20), modelled on miStudio's: set a
// PIN, change it or remove it with the current one. Not a login (the suite has none by decision):
// it locks changes to endpoints, keys, runtime settings and every other Settings write, and the
// backend enforces it. A forgotten PIN is recovered with SETTINGS_PIN_BYPASS=true in the backend's
// environment (docs/RUNBOOK.md).
import { useEffect, useState } from 'react';
import { Lock, LockOpen } from 'lucide-react';

import { Button } from '@/components/common/Button';
import { usePinStore } from '@/stores/pinStore';

const field = 'w-full rounded-lg px-3 py-2.5 text-sm border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-900 text-slate-900 dark:text-slate-100';
const card = 'rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 p-5';

function PinInput({ id, label, value, onChange }: { id: string; label: string; value: string; onChange: (v: string) => void }) {
  return (
    <div className="mb-3">
      <label htmlFor={id} className="block text-sm font-medium mb-1.5 text-slate-800 dark:text-slate-200">{label}</label>
      <input id={id} type="password" inputMode="numeric" autoComplete="off" className={field} value={value} onChange={(e) => onChange(e.target.value)} />
    </div>
  );
}

export function SettingsPinCard() {
  const status = usePinStore((s) => s.status);
  const error = usePinStore((s) => s.error);
  const notice = usePinStore((s) => s.notice);
  const loadStatus = usePinStore((s) => s.loadStatus);
  const setPin = usePinStore((s) => s.setPin);
  const removePin = usePinStore((s) => s.removePin);
  const lock = usePinStore((s) => s.lock);
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [again, setAgain] = useState('');

  useEffect(() => {
    void loadStatus();
  }, [loadStatus]);

  const configured = status?.configured ?? false;
  const mismatch = next !== '' && again !== '' && next !== again;
  const clear = () => {
    setCurrent('');
    setNext('');
    setAgain('');
  };

  return (
    <section className={card} aria-labelledby="pin-title" data-testid="settings-pin">
      <h2 id="pin-title" className="flex items-center gap-2 font-semibold mb-1 text-slate-900 dark:text-slate-100">
        {configured ? <Lock size={16} aria-hidden="true" /> : <LockOpen size={16} aria-hidden="true" />} Settings PIN
      </h2>
      <p className="text-xs mb-3 text-slate-500 dark:text-slate-400">
        A PIN of 4 to 12 digits locks every change on this screen: endpoints and their API keys, runtime settings, your name and the shortcut
        level, and approving an agent&apos;s settings change. It is not a login. After the PIN is entered, this browser tab stays unlocked for{' '}
        {Math.round((status?.session_ttl_s ?? 1800) / 60)} minutes. Changes an agent asks for still wait for your approval.
      </p>
      {status?.bypass_active && (
        <p role="status" className="mb-3 text-xs rounded-lg p-3 bg-amber-50 dark:bg-amber-950 text-amber-800 dark:text-amber-300">
          SETTINGS_PIN_BYPASS is on in the backend&apos;s environment: Settings is unlocked for everyone, and the PIN can be set or removed
          without the current one. Remove the variable and restart the backend once the PIN is reset.
        </p>
      )}
      <p className="text-sm mb-3 text-slate-700 dark:text-slate-300" data-testid="pin-state">
        {configured ? (status?.session_valid ? 'A PIN is set. This tab is unlocked.' : 'A PIN is set. Settings is locked.') : 'No PIN is set. Anyone using this page can change Settings.'}
      </p>
      {error && <p role="alert" className="mb-3 text-sm text-red-700 dark:text-red-400">{error}</p>}
      {notice && !error && <p className="mb-3 text-sm text-green-700 dark:text-green-400">{notice}</p>}
      {configured && <PinInput id="pin-current" label="Current PIN" value={current} onChange={setCurrent} />}
      <PinInput id="pin-new" label={configured ? 'New PIN' : 'PIN'} value={next} onChange={setNext} />
      <PinInput id="pin-again" label="The new PIN again" value={again} onChange={setAgain} />
      {mismatch && <p className="mb-3 text-xs text-red-700 dark:text-red-400">The two entries differ.</p>}
      <div className="flex flex-wrap gap-2">
        <Button
          disabled={!next || next !== again || (configured && !current && !status?.bypass_active)}
          onClick={() => void setPin(next, configured ? current || undefined : undefined).then((ok) => ok && clear())}
        >
          {configured ? 'Change the PIN' : 'Set the PIN'}
        </Button>
        {configured && (
          <Button variant="secondary" disabled={!current && !status?.bypass_active} onClick={() => void removePin(current).then((ok) => ok && clear())}>
            Remove the PIN
          </Button>
        )}
        {configured && status?.session_valid && (
          <Button variant="secondary" onClick={lock}>Lock now</Button>
        )}
      </div>
    </section>
  );
}
