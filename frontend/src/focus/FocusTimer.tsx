import { useEffect, useRef, useState } from 'react';
import { Pause, Play, RefreshCw, RotateCcw, Settings2, SkipForward, Trash2, Volume2, VolumeX, X } from 'lucide-react';
import { FOCUS_MODES, isValidDurationMinutes, modeLabel } from './modes';
import { formatClock, sessionKey, type CompletedSession, type PendingSession } from './timer';
import { newCompletionKeys, playCompletionSound, readSoundEnabled, writeSoundEnabled } from './sound';
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
  const primaryLabel = timer.isRunning ? 'Пауза' : timer.isPaused ? 'Продолжить' : 'Старт';
  const notice = timer.restoreNotice;
  const [durationInput, setDurationInput] = useState(String(timer.durationMinutes));
  const [durationError, setDurationError] = useState<string | null>(null);
  const [soundEnabled, setSoundEnabled] = useState(() => readSoundEnabled());
  const knownPendingRef = useRef<Set<string>>(
    new Set(timer.pending.map((item) => sessionKey(item.session))),
  );

  useEffect(() => {
    setDurationInput(String(timer.durationMinutes));
    setDurationError(null);
  }, [timer.mode, timer.durationMinutes]);

  useEffect(() => {
    const fresh = newCompletionKeys(knownPendingRef.current, timer.pending);
    for (const key of fresh) knownPendingRef.current.add(key);
    if (fresh.length > 0 && soundEnabled) playCompletionSound();
  }, [timer.pending, soundEnabled]);

  function handlePrimary() {
    if (timer.isRunning) timer.pause();
    else if (timer.isPaused) timer.resume();
    else timer.start();
  }

  function applyDuration() {
    if (timer.isRunning || timer.isPaused) {
      setDurationError('Сначала завершите или сбросьте текущую сессию.');
      return;
    }
    const minutes = Number(durationInput);
    if (!isValidDurationMinutes(minutes)) {
      setDurationError('Введите целое число от 1 до 180 минут.');
      return;
    }
    if (!timer.setDuration(minutes)) {
      setDurationError('Не удалось изменить длительность.');
      return;
    }
    setDurationError(null);
  }

  function toggleSound() {
    const next = !soundEnabled;
    setSoundEnabled(next);
    writeSoundEnabled(next);
  }

  return (
    <section className="timer">
      <div className="segmented modes">
        {FOCUS_MODES.map((mode) => (
          <button
            key={mode.id}
            className={timer.mode === mode.id ? 'active' : ''}
            onClick={() => timer.setMode(mode.id)}
          >
            {mode.label}
          </button>
        ))}
      </div>
      <div className="timer-mode">
        {modeLabel(timer.mode)} · {timer.durationMinutes} мин
      </div>
      <div className="timer-clock">{formatClock(timer.remainingSeconds)}</div>
      <div className="timer-controls">
        <button className="primary-button timer-primary" onClick={handlePrimary}>
          {timer.isRunning ? <Pause /> : <Play />}
          {primaryLabel}
        </button>
        <button className="icon timer-reset" onClick={() => timer.reset()} aria-label="Сбросить таймер">
          <RotateCcw />
        </button>
      </div>
      {timer.status === 'completed' && (
        <p className="timer-complete" role="status">
          Таймер завершён.
        </p>
      )}
      <details className="timer-settings">
        <summary><Settings2 size={15} />Настройки</summary>
        <div className="timer-settings-body">
          <div className="timer-setting-row">
            <label htmlFor="focus-duration">Длительность</label>
            <div className="timer-duration-field">
              <input
                id="focus-duration"
                type="number"
                min="1"
                max="180"
                step="1"
                inputMode="numeric"
                value={durationInput}
                disabled={timer.isRunning || timer.isPaused}
                onChange={(event) => setDurationInput(event.target.value)}
                onBlur={applyDuration}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') {
                    event.preventDefault();
                    applyDuration();
                  }
                  if (event.key === 'Escape') {
                    setDurationInput(String(timer.durationMinutes));
                    setDurationError(null);
                  }
                }}
                aria-label="Своя длительность в минутах"
              />
              <span>мин</span>
            </div>
          </div>
          <small className="timer-recommended">
            Рекомендуется: {timer.recommendedMinutes} мин
            {timer.isCustomDuration ? ' · сейчас своя' : ''}
          </small>
          {durationError && <span className="error">{durationError}</span>}
          <button className="sound-toggle" onClick={toggleSound} type="button">
            {soundEnabled ? <Volume2 size={16} /> : <VolumeX size={16} />}
            {soundEnabled ? 'Звук завершения включён' : 'Звук завершения выключен'}
          </button>
        </div>
      </details>
      {statsError ? (
        <p className="error">Не удалось загрузить статистику: {statsError}</p>
      ) : (
        <p className="timer-stats">
          {stats?.sessions ?? 0} сессий · {stats?.focus_minutes ?? 0} минут
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
