// "How to add an operator package" (T-10): packages reach the image only by a commit, never at
// runtime; their entry points then appear here as Not allowed until the operator allows them.
import { Modal } from '@/components/common/Modal';

export function AddPackageGuide({ open, onClose }: { open: boolean; onClose: () => void }) {
  return (
    <Modal id="add-operator-package" isOpen={open} onClose={onClose} title="How to add an operator package" size="lg">
      <ol className="list-decimal space-y-2 pl-5 text-sm text-slate-700 dark:text-slate-300" data-testid="add-package-guide">
        <li>
          Add the package, pinned to an exact version, to <span className="font-mono">backend/requirements-operators.txt</span> in a
          commit. Nothing is installed while the app runs.
        </li>
        <li>The package declares its operators in the <span className="font-mono">midataworks.operators</span> entry-point group.</li>
        <li>CI builds the backend image with it; the deployment picks up the new image.</li>
        <li>
          Its entry points then appear on this screen as Not allowed. They are not even imported until you allow one, because
          importing a module runs its code.
        </li>
        <li>Allow an entry point below, with a reason. A new package version needs a new decision.</li>
      </ol>
    </Modal>
  );
}
