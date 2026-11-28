// The unlock dialog (feature 014). Mounted once by App: while mounted it is the client's PIN
// prompter, so any Settings write refused with 403 PIN_REQUIRED, on any screen (Settings, the
// shortcut level, approving a Settings change), asks for the PIN here and is then sent again.
import { useEffect, useState } from 'react';
import { Lock } from 'lucide-react';

import { registerPinPrompter } from '@/api/pinSession';
import { Button } from '@/components/common/Button';
import { Modal } from '@/components/common/Modal';
import { usePinStore } from '@/stores/pinStore';

export function PinPrompt() {
  const prompt = usePinStore((s) => s.prompt);
  const requestPin = usePinStore((s) => s.requestPin);
  const submitPin = usePinStore((s) => s.submitPin);
  const cancelPrompt = usePinStore((s) => s.cancelPrompt);
  const [pin, setPin] = useState('');

  useEffect(() => registerPinPrompter((reason) => requestPin(reason)), [requestPin]);

  const [openedFor, setOpenedFor] = useState(prompt?.reason ?? null);
  if ((prompt?.reason ?? null) !== openedFor) {
    setOpenedFor(prompt?.reason ?? null);
    setPin('');
  }

  return (
    <Modal
      id="settings-pin-prompt"
      title="Enter the Settings PIN"
      isOpen={prompt !== null}
      onClose={cancelPrompt}
      size="sm"
      footer={
        <div className="flex justify-end gap-2">
          <Button variant="secondary" onClick={cancelPrompt}>Cancel</Button>
          <Button disabled={!pin || prompt?.busy} onClick={() => void submitPin(pin)} data-testid="pin-unlock">
            Unlock
          </Button>
        </div>
      }
    >
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (pin) void submitPin(pin);
        }}
      >
        <p className="flex items-start gap-2 text-sm mb-3 text-slate-700 dark:text-slate-300">
          <Lock size={16} aria-hidden="true" className="mt-0.5 shrink-0" />
          <span>{prompt?.reason ?? 'Settings are locked with a PIN.'} The change is sent once the PIN is accepted.</span>
        </p>
        <label htmlFor="settings-pin-input" className="block text-sm font-medium mb-1.5 text-slate-800 dark:text-slate-200">PIN</label>
        <input
          id="settings-pin-input"
          type="password"
          inputMode="numeric"
          autoComplete="off"
          autoFocus
          className="w-full rounded-control px-3 py-2.5 text-sm border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-900 text-slate-900 dark:text-slate-100"
          value={pin}
          onChange={(e) => setPin(e.target.value)}
        />
        {prompt?.error && <p role="alert" className="mt-2 text-sm text-red-700 dark:text-red-400">{prompt.error}</p>}
      </form>
    </Modal>
  );
}
