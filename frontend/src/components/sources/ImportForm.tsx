// Origin: miStudio (Onegaishimas/miStudio) frontend/src/components/datasets/DownloadForm.tsx @ c829a2cc
// Mode: adapt (docs/REUSE.md). Kept: Repository ID, Split and Config fields, the masked token field
// with hold-to-reveal, autocomplete="off" and the password-manager ignore attributes; Preview beside
// the primary action. Changed (FPRD 001 section 4, handoff section 4): a Revision field; field order
// and hints from the mockup; "Import" (lucide Database) replaces "Download"; Preview (ScanSearch)
// runs on the backend; the token is cleared after EVERY request, whatever the outcome; the error
// sits beside its field; indigo focus rings instead of emerald.
import { Database, Eye, EyeOff, ScanSearch } from 'lucide-react';
import { useState } from 'react';
import type { CSSProperties, FormEvent } from 'react';

import { Button } from '@/components/common/Button';
import { useSourcesStore } from '@/stores/sourcesStore';
import type { ImportOutcome } from '@/stores/sourcesStore';
import type { HfRequest } from '@/types/sources';
import { validateHfRepoId } from '@/utils/hfRepoId';

import { PreviewModal } from './PreviewModal';

const field =
  'w-full rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 px-3 py-2 text-sm text-slate-900 dark:text-slate-100 placeholder-slate-400 dark:placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent disabled:opacity-60';
const labelCls = 'block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1';
const hintCls = 'text-xs text-slate-500 dark:text-slate-400 mt-1';

export function ImportForm({ onOutcome }: { onOutcome?: (outcome: ImportOutcome) => void }) {
  const previewHf = useSourcesStore((s) => s.previewHf);
  const importHf = useSourcesStore((s) => s.importHf);
  const [repoId, setRepoId] = useState('');
  const [split, setSplit] = useState('');
  const [config, setConfig] = useState('');
  const [revision, setRevision] = useState('');
  const [token, setToken] = useState('');
  const [showToken, setShowToken] = useState(false);
  const [confirmLarge, setConfirmLarge] = useState(false);
  const [repoError, setRepoError] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [previewOpen, setPreviewOpen] = useState(false);

  const request = (overrides: Partial<HfRequest> = {}): HfRequest => ({ repo_id: repoId, split, config, revision, ...overrides });

  const valid = () => {
    const verdict = validateHfRepoId(repoId);
    setRepoError(verdict === true ? null : verdict);
    return verdict === true;
  };

  const preview = async (overrides: Partial<HfRequest> = {}) => {
    if (!valid()) return;
    setFormError(null);
    setPreviewOpen(true);
    const sent = token;
    setToken(''); // cleared after every request (FPRD 001 section 4)
    await previewHf(request(overrides), sent || undefined);
  };

  const submit = async (e?: FormEvent) => {
    e?.preventDefault();
    if (!valid()) return;
    setFormError(null);
    setBusy(true);
    const sent = token;
    setToken('');
    const outcome = await importHf({ ...request(), confirm_large: confirmLarge }, sent || undefined);
    setBusy(false);
    if (outcome.kind === 'refused') {
      if (outcome.code === 'repo_id_invalid') setRepoError(outcome.message);
      else setFormError(outcome.message);
    } else {
      setPreviewOpen(false);
    }
    onOutcome?.(outcome);
  };

  return (
    <form onSubmit={(e) => void submit(e)} aria-labelledby="import-title" className="rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 p-5">
      <h2 id="import-title" className="font-semibold mb-4 text-slate-900 dark:text-slate-100">Import from Hugging Face</h2>
      <div className="mb-3">
        <label htmlFor="hf-repo" className={labelCls}>Repository ID</label>
        <input id="hf-repo" type="text" autoComplete="off" value={repoId} onChange={(e) => setRepoId(e.target.value)} placeholder="owner/dataset-name" className={`${field} font-mono`} aria-invalid={repoError ? true : undefined} aria-describedby={repoError ? 'hf-repo-error' : undefined} disabled={busy} />
        {repoError && <p id="hf-repo-error" role="alert" className="text-xs mt-1 text-red-700 dark:text-red-400">{repoError}</p>}
      </div>
      <div className="grid gap-x-5 gap-y-3 sm:grid-cols-3 mb-3">
        <div>
          <label htmlFor="hf-split" className={labelCls}>Split (optional)</label>
          <input id="hf-split" type="text" autoComplete="off" value={split} onChange={(e) => setSplit(e.target.value)} className={`${field} font-mono`} disabled={busy} />
          <p className={hintCls}>Every split if empty</p>
        </div>
        <div>
          <label htmlFor="hf-config" className={labelCls}>Config (optional)</label>
          <input id="hf-config" type="text" autoComplete="off" value={config} onChange={(e) => setConfig(e.target.value)} className={field} disabled={busy} />
          <p className={hintCls}>Dataset configuration</p>
        </div>
        <div>
          <label htmlFor="hf-revision" className={labelCls}>Revision</label>
          <input id="hf-revision" type="text" autoComplete="off" value={revision} onChange={(e) => setRevision(e.target.value)} className={`${field} font-mono`} disabled={busy} />
          <p className={hintCls}>Pinned to the current commit if empty, so a version never changes underneath you</p>
        </div>
      </div>
      <div className="mb-3">
        <label htmlFor="hf-token" className={labelCls}>Access token (optional)</label>
        <div className="relative">
          <input
            id="hf-token"
            name="dataworks-hf-credential-input"
            type="text"
            autoComplete="off"
            data-lpignore="true"
            data-1p-ignore="true"
            data-form-type="other"
            value={token}
            onChange={(e) => setToken(e.target.value)}
            placeholder="hf_..."
            className={`${field} pr-10 font-mono`}
            style={{ WebkitTextSecurity: showToken ? 'none' : 'disc' } as CSSProperties}
            disabled={busy}
          />
          {token && (
            <button type="button" onMouseDown={() => setShowToken(true)} onMouseUp={() => setShowToken(false)} onMouseLeave={() => setShowToken(false)} onTouchStart={() => setShowToken(true)} onTouchEnd={() => setShowToken(false)} className="absolute right-2 top-1/2 -translate-y-1/2 p-1 text-slate-500 dark:text-slate-400" title="Hold to reveal the token" aria-label="Hold to reveal the token" tabIndex={-1}>
              {showToken ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
            </button>
          )}
        </div>
        <p className={hintCls}>Needed for private or gated datasets. Uses the token in Settings if empty; never stored with the dataset.</p>
      </div>
      <label className="flex items-start gap-2 text-xs text-slate-600 dark:text-slate-400 mb-4">
        <input type="checkbox" checked={confirmLarge} onChange={(e) => setConfirmLarge(e.target.checked)} className="mt-0.5 focus:ring-2 focus:ring-indigo-500" />
        Import even if it is larger than the confirmation size in Settings → Storage (50 GB by default).
      </label>
      {formError && <p role="alert" className="text-sm mb-3 text-red-700 dark:text-red-400">{formError}</p>}
      <div className="grid gap-3 sm:grid-cols-2">
        <Button type="button" variant="secondary" leftIcon={<ScanSearch className="w-4 h-4" />} onClick={() => void preview()} disabled={busy || !repoId.trim()}>Preview</Button>
        <Button type="submit" leftIcon={<Database className="w-4 h-4" />} disabled={busy || !repoId.trim()}>{busy ? 'Importing…' : 'Import'}</Button>
      </div>
      <PreviewModal
        isOpen={previewOpen}
        onClose={() => setPreviewOpen(false)}
        onPickConfig={(c) => { setConfig(c); void preview({ config: c }); }}
        onPickSplit={(s) => setSplit(s)}
        onImport={() => void submit()}
      />
    </form>
  );
}
