// Origin: miLLM (Onegaishimas/miLLM) admin-ui/src/components/common/Modal.tsx @ 0efff20
// Mode: adapt (docs/REUSE.md; was copy until 2026-10-31), recoloured to indigo with light-mode pairs; checked for React 19-only APIs (none: forwardRef only).
// Changed 2026-10-31 (operator: every detail popup closes the same way): Escape goes through the
// shared useEscapeToClose (the popup opened last closes first), and the X is the shared CloseButton.
import { useEffect, useCallback } from 'react';
import type { ReactNode } from 'react';
import { useUIStore } from '@/stores/uiStore';

import { CloseButton, useEscapeToClose } from './DetailPopup';

export interface ModalProps {
  id: string;
  title?: string;
  children: ReactNode;
  footer?: ReactNode;
  size?: 'sm' | 'md' | 'lg' | 'xl' | '2xl' | '3xl';
  closeOnOverlay?: boolean;
  closeOnEscape?: boolean;
  isOpen?: boolean;
  onClose?: () => void;
}

const sizeStyles = {
  sm: 'max-w-sm',
  md: 'max-w-md',
  lg: 'max-w-lg',
  xl: 'max-w-xl',
  '2xl': 'max-w-2xl',
  '3xl': 'max-w-3xl',
};

export function Modal({
  id,
  title,
  children,
  footer,
  size = 'md',
  closeOnOverlay = true,
  closeOnEscape = true,
  isOpen: controlledIsOpen,
  onClose,
}: ModalProps) {
  const modal = useUIStore((s) => s.modal);
  const closeModal = useUIStore((s) => s.closeModal);
  // Support both controlled and store-based modal state
  const isOpen = controlledIsOpen !== undefined ? controlledIsOpen : modal.id === id;
  const handleClose = onClose || closeModal;

  useEscapeToClose(isOpen && closeOnEscape, handleClose);

  const handleOverlayClick = useCallback(() => {
    if (closeOnOverlay) {
      handleClose();
    }
  }, [closeOnOverlay, handleClose]);

  useEffect(() => {
    if (isOpen) document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = '';
    };
  }, [isOpen]);

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/60 backdrop-blur-xs animate-fade-in"
        onClick={handleOverlayClick}
      />

      {/* Modal */}
      <div
        className={`
          relative w-full mx-4
          bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-700/50 rounded-card
          shadow-2xl animate-slide-in
          ${sizeStyles[size]}
        `}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        {title && (
          <div className="flex items-center justify-between px-6 py-4 border-b border-slate-200 dark:border-slate-700/50">
            <h2 className="text-lg font-semibold text-slate-900 dark:text-slate-100">{title}</h2>
            <CloseButton onClose={handleClose} what={title} />
          </div>
        )}

        {/* Body */}
        <div className="px-6 py-4 max-h-[60vh] overflow-y-auto">{children}</div>

        {/* Footer */}
        {footer && (
          <div className="px-6 py-4 border-t border-slate-200 dark:border-slate-700/50 flex justify-end gap-3">
            {footer}
          </div>
        )}
      </div>
    </div>
  );
}

export default Modal;
