import { Pause, Play, RefreshCw, RotateCcw, SkipForward, Trash2, X } from 'lucide-react';
import { FOCUS_MODES, modeLabel } from './modes';
import { formatClock, sessionKey, type CompletedSession, type PendingSession } from './timer';
import type { FocusTimerController } from './useFocusTimer';
import './FocusTimer.css';

export interface FocusStats {
  sessions?: number;
  focus_minutes?: number;
}

interface FocusTimerProps {
  timer: FocusTimerController;
  stats?: FocusStats;
  statsError?: string | null;
  saveError?: string | null;
  saving?: boolean;
}

const finishTimeFormatter = new Intl.DateTimeFormat('ru-RU', {
  day: '2-digit',
  month: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
});

function formatFinishedAt(timestamp: number): string {
  return finishTimeFormatter.format(new Date(timestamp));
}

function pendingStatus(item: PendingSession): string {
  if (item.lastErrorKind === 'permanent') return 'Сервер отклонил сессию';
  if (item.failedAttempts > 0) {
    return `Ошибка отправки · попыток: ${item.failedAttempts}`;
  }
  if (item.requiresManualRetry) return 'Ожидает подтверждения';
  return 'Отправляется…';
}

/** Removing an unsaved session is destructive, so it always needs a confirmation. */
function confirmDiscard(timer: FocusTimerController, session: CompletedSession) {
  const message =
    'Удалить несохранённую сессию? Она не будет добавлена в статистику.';
  const telegram = (
    window as unknown as {
      Telegram?: {
        WebApp?: {
          showConfirm?: (
            message: string,
            callback: (confirmed: boolean) => void,
          ) => void;
        };
      };
    }
  ).Telegram?.WebApp;
  if (telegram?.showConfirm) {
    telegram.showConfirm(message, (confirmed: boolean) => {
      if (confirmed) timer.discardPending(session);
    });
    return;
  }
  if (window.confirm(message)) timer.discardPending(session);
}

export default function FocusTimer({
  timer,
  stats,
  statsError,
  saveError,
  saving = false,
}: FocusTimerProps) {
  const primaryLabel = timer.isRunning ? 'Pause' : timer.isPaused ? 'Resume' : 'Start';
  const notice = timer.restoreNotice;

  function handlePrimary() {
    if (timer.isRunning) timer.pause();
    else if (timer.isPaused) timer.resume();
    else timer.start();
  }

  return (
    <section className="card timer">
      <div className="quick modes">
        {FOCUS_MODES.map((mode) => (
          <button
            key={mode.id}
            className={timer.mode === mode.id ? 'selected' : ''}
            onClick={() => timer.setMode(mode.id)}
          >
            {mode.label}
          </button>
        ))}
      </div>
      <span>{modeLabel(timer.mode).toUpperCase()}</span>
      <div className="clock">{formatClock(timer.remainingSeconds)}</div>
      <div className="controls">
        <button onClick={handlePrimary}>
          {timer.isRunning ? <Pause /> : <Play />}
          {primaryLabel}
        </button>
        <button className="icon" onClick={() => timer.reset()} aria-label="Reset">
          <RotateCcw />
        </button>
      </div>
      {statsError ? (
        <p className="error">Не удалось загрузить статистику: {statsError}</p>
      ) : (
        <p>
          {stats?.sessions ?? 0} completed sessions · {stats?.focus_minutes ?? 0} focus minutes
        </p>
      )}
      {notice &&
        (notice.kind === 'error' ? (
          <p className="error">
            Не удалось восстановить таймер: {notice.message} Запущен новый таймер.
            <button
              className="icon"
              onClick={() => timer.dismissRestoreNotice()}
              aria-label="Скрыть уведомление"
            >
              <X />
            </button>
          </p>
        ) : (
          <div className="notice">
            <span>
              {[
                notice.timerRestored ? 'Таймер восстановлен.' : null,
                notice.hadPending ? 'Восстановлена несохранённая сессия.' : null,
              ]
                .filter(Boolean)
                .join(' ')}
            </span>
            <button
              className="icon"
              onClick={() => timer.dismissRestoreNotice()}
              aria-label="Скрыть уведомление"
            >
              <X />
            </button>
          </div>
        ))}
      {timer.hasSaveError && (
        <p className="error">
          Не удалось сохранить сессию{saveError ? `: ${saveError}` : '.'} Данные сессии остались в
          приложении.
        </p>
      )}
      {timer.hasPending && (
        <div className="pending">
          <p>
            {timer.pendingCount === 1
              ? 'Завершённая сессия ещё не сохранена.'
              : `Завершённых сессий без сохранения: ${timer.pendingCount}.`}
          </p>
          {timer.hasUnprotectedPending && (
            <p className="error">
              Предыдущий запрос мог быть обработан сервером. Повторная отправка может создать
              дубликат.
            </p>
          )}
          <ul className="pending-list">
            {timer.pending.map((item) => (
              <li className="pending-item" key={sessionKey(item.session)}>
                <div>
                  <b>{modeLabel(item.session.mode)}</b>
                  <small>{formatFinishedAt(item.session.finishedAt)}</small>
                  <small>{pendingStatus(item)}</small>
                  {item.lastError && <small className="error">{item.lastError}</small>}
                </div>
                <div className="pending-actions">
                  <button
                    onClick={() => timer.retryPending(item.session)}
                    disabled={saving}
                    aria-label={`Повторить сохранение сессии ${modeLabel(item.session.mode)}`}
                  >
                    <RefreshCw className={saving ? 'spin' : ''} />
                    Повторить
                  </button>
                  {item.lastErrorKind === 'permanent' && timer.retryableCount > 0 && (
                    <button
                      onClick={() => timer.retryNextPending(item.session)}
                      disabled={saving}
                      aria-label="Пропустить сессию и повторить следующую"
                    >
                      <SkipForward />
                      Пропустить
                    </button>
                  )}
                  <button
                    className="danger"
                    onClick={() => confirmDiscard(timer, item.session)}
                    disabled={saving}
                    aria-label={`Удалить несохранённую сессию ${modeLabel(item.session.mode)}`}
                  >
                    <Trash2 />
                    Удалить
                  </button>
                </div>
              </li>
            ))}
          </ul>
          {timer.pendingCount > 1 && timer.retryableCount > 0 && (
            <div className="pending-summary">
              <button onClick={() => timer.retryAllPending()} disabled={saving}>
                <RefreshCw className={saving ? 'spin' : ''} />
                Повторить все ({timer.retryableCount})
              </button>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
