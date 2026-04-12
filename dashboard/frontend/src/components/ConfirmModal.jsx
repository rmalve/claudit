import { useEffect, useRef } from 'react'

export default function ConfirmModal({ open, title, message, confirmLabel = 'Confirm', cancelLabel = 'Cancel', onConfirm, onCancel, destructive = false }) {
  const confirmRef = useRef(null)

  useEffect(() => {
    if (open && confirmRef.current) {
      confirmRef.current.focus()
    }
  }, [open])

  useEffect(() => {
    if (!open) return
    const handler = (e) => { if (e.key === 'Escape') onCancel() }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [open, onCancel])

  if (!open) return null

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="fixed inset-0 bg-black/60" onClick={onCancel} />
      <div className="relative bg-brand-surface border border-brand-border rounded-xl shadow-2xl w-full max-w-md mx-4 p-6">
        <h3 className="text-lg font-semibold text-brand-text mb-2">{title}</h3>
        <p className="text-sm text-brand-text-secondary mb-6">{message}</p>
        <div className="flex justify-end gap-3">
          <button
            onClick={onCancel}
            className="px-4 py-2 text-sm rounded-lg border border-brand-border text-brand-text-secondary hover:bg-brand-bg-secondary transition-colors"
          >
            {cancelLabel}
          </button>
          <button
            ref={confirmRef}
            onClick={onConfirm}
            className={`px-4 py-2 text-sm rounded-lg font-medium transition-colors ${
              destructive
                ? 'bg-brand-accent hover:bg-brand-accent-dark text-white'
                : 'bg-brand-accent hover:bg-brand-accent-dark text-white'
            }`}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}
