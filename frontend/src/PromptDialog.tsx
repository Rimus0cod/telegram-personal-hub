import { useEffect, useId, useState, type FormEvent } from 'react';
import './dialog.css';

interface PromptDialogProps {
  open: boolean;
  title: string;
  label: string;
  initialValue?: string;
  inputType?: 'text' | 'date';
  confirmLabel?: string;
  busyLabel?: string;
  busy?: boolean;
  error?: string | null;
  validate?: (value: string) => string | null;
  onSubmit: (value: string) => void;
  onCancel: () => void;
}

/**
 * Minimal accessible replacement for native prompt dialogs. Telegram WebViews do
 * not reliably implement native prompt dialogs, so tasks and the nutrition
 * day-copy flow use this form instead. It keeps the project's card styling and
 * does not pull in a UI library.
 */
export default function PromptDialog({
  open,
  title,
  label,
  initialValue = '',
  inputType = 'text',
  confirmLabel = 'Сохранить',
  busyLabel = 'Сохраняем…',
  busy = false,
  error,
  validate,
  onSubmit,
  onCancel,
}: PromptDialogProps) {
  const [value, setValue] = useState(initialValue);
  const [localError, setLocalError] = useState<string | null>(null);
  const inputId = useId();
  const titleId = useId();

  useEffect(() => {
    if (open) {
      setValue(initialValue);
      setLocalError(null);
    }
  }, [open, initialValue]);

  useEffect(() => {
    if (!open) return;
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !busy) onCancel();
    };
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [open, busy, onCancel]);

  if (!open) return null;

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    const trimmed = value.trim();
    if (!trimmed) {
      setLocalError('Заполните поле.');
      return;
    }
    const validation = validate?.(trimmed) ?? null;
    if (validation) {
      setLocalError(validation);
      return;
    }
    setLocalError(null);
    onSubmit(trimmed);
  }

  return (
    <div
      className="dialog-backdrop"
      onClick={() => {
        if (!busy) onCancel();
      }}
    >
      <section
        className="dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onClick={(event) => event.stopPropagation()}
      >
        <form onSubmit={handleSubmit}>
          <h2 id={titleId}>{title}</h2>
          <label htmlFor={inputId}>
            {label}
            <input
              id={inputId}
              type={inputType}
              value={value}
              onChange={(event) => {
                setValue(event.target.value);
                setLocalError(null);
              }}
              autoFocus
              disabled={busy}
            />
          </label>
          {(localError ?? error) && (
            <p className="dialog-error" role="alert">
              {localError ?? error}
            </p>
          )}
          <div className="dialog-actions">
            <button
              type="button"
              className="secondary"
              onClick={onCancel}
              disabled={busy}
            >
              Отмена
            </button>
            <button type="submit" disabled={busy || !value.trim()}>
              {busy ? busyLabel : confirmLabel}
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}
