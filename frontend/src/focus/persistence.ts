import { FOCUS_MODES, modeDurationMs, type FocusMode } from './modes';
import {
  createClientId,
  createTimerState,
  sessionKey,
  type CompletedSession,
  type PendingSession,
  type TimerState,
  type TimerStatus,
} from './timer';

/** Versioned key, so a future schema can migrate instead of guessing. */
export const FOCUS_STORAGE_KEY = 'focus.timer.v1';
/** v2 adds the backend idempotency key (client_id); v3 adds custom duration. */
export const FOCUS_STORAGE_VERSION = 3;
export const FOCUS_STORAGE_LEGACY_VERSIONS = [1, 2] as const;

/** Mirrors the backend constraint for a single recorded session. */
const MIN_SESSION_MINUTES = 1;
const MAX_SESSION_MINUTES = 180;

export interface StorageLike {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem?: (key: string) => void;
}

interface PersistedPendingSession {
  mode: FocusMode;
  durationMinutes: number;
  startedAt: number;
  finishedAt: number;
  failedAttempts: number;
  requiresManualRetry: boolean;
  /** uuid v4; absent in the legacy v1 schema. */
  clientId: string | null;
  /** Last delivery failure; absent in older v2 records and in v1. */
  lastError?: string | null;
  lastErrorKind?: 'transient' | 'permanent' | null;
}

interface PersistedTimerState {
  version: number;
  savedAt: number;
  mode: FocusMode;
  status: TimerStatus;
  durationMs: number;
  remainingMs: number;
  deadlineAt: number | null;
  startedAt: number | null;
  completedAt: number | null;
  sessionClientId: string | null;
  pending: PersistedPendingSession[];
}

export interface RestoredInfo {
  kind: 'restored';
  state: TimerState;
  /** True when a running or paused countdown was restored. */
  timerRestored: boolean;
  /** Number of unsaved sessions that were already stored before the reload. */
  restoredPending: number;
  /** True when at least one restored session needs an explicit retry. */
  manualRetryRequired: boolean;
  /** True when the restored running timer had already passed its deadline. */
  expiredCompletion: boolean;
}

export type LoadResult =
  | { kind: 'empty' }
  | { kind: 'unavailable'; message: string }
  | { kind: 'error'; message: string }
  | RestoredInfo;

export type SaveResult = { ok: true } | { ok: false; message: string };

export function serializeTimerState(state: TimerState, savedAt: number = Date.now()): string {
  const remainingMs =
    state.status === 'running' && state.deadlineAt !== null
      ? Math.max(0, state.deadlineAt - savedAt)
      : state.remainingMs;
  const payload: PersistedTimerState = {
    version: FOCUS_STORAGE_VERSION,
    savedAt,
    mode: state.mode,
    status: state.status,
    durationMs: state.durationMs,
    remainingMs,
    deadlineAt: state.deadlineAt,
    startedAt: state.startedAt,
    completedAt: state.completedAt,
    sessionClientId: state.sessionClientId,
    pending: state.pending.map((item) => ({
      mode: item.session.mode,
      durationMinutes: item.session.durationMinutes,
      startedAt: item.session.startedAt,
      finishedAt: item.session.finishedAt,
      failedAttempts: item.failedAttempts,
      requiresManualRetry: item.requiresManualRetry,
      clientId: item.session.clientId,
      lastError: item.lastError,
      lastErrorKind: item.lastErrorKind,
    })),
  };
  return JSON.stringify(payload);
}

/**
 * Identifies state changes worth writing to storage. A running timer ticks every
 * second, but only its deadline matters, so ticks must not produce writes.
 */
export function persistenceSignature(state: TimerState): string {
  return JSON.stringify([
    state.mode,
    state.status,
    state.durationMs,
    state.status === 'running' ? null : state.remainingMs,
    state.deadlineAt,
    state.startedAt,
    state.completedAt,
    state.sessionClientId,
    state.pending.map((item) => [
      item.session.mode,
      item.session.durationMinutes,
      item.session.startedAt,
      item.session.finishedAt,
      item.failedAttempts,
      item.requiresManualRetry,
      item.session.clientId,
      item.lastError,
      item.lastErrorKind,
    ]),
  ]);
}

export function parseTimerState(raw: string | null | undefined, now: number): LoadResult {
  if (raw === null || raw === undefined || raw.trim() === '') return { kind: 'empty' };

  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    return fail('Данные таймера повреждены и не могут быть прочитаны.');
  }
  if (!isRecord(value)) return fail('Некорректный формат сохранённых данных.');
  const schemaVersion = value.version;
  if (
    schemaVersion !== 1 &&
    schemaVersion !== 2 &&
    schemaVersion !== FOCUS_STORAGE_VERSION
  ) {
    return fail('Неизвестная версия сохранённых данных таймера.');
  }
  // v1 predates client_id. v2 and v3 keep it. A v1 pending session can never be
  // treated as protected from duplicates.
  const hasClientIdSchema = schemaVersion === 2 || schemaVersion === FOCUS_STORAGE_VERSION;
  const storedSessionClientId = hasClientIdSchema
    ? readNullableUuid(value.sessionClientId)
    : null;

  if (!isFocusMode(value.mode)) return fail('Некорректный режим таймера.');
  if (!isTimerStatus(value.status)) return fail('Некорректный статус таймера.');
  const mode = value.mode;
  const durationMs =
    schemaVersion === FOCUS_STORAGE_VERSION
      ? readDurationMs(value.durationMs)
      : modeDurationMs(mode);
  if (durationMs === null) return fail('Некорректная длительность таймера.');

  const savedAt = readNonNegative(value.savedAt);
  if (savedAt === null) return fail('Некорректная метка сохранения.');
  const storedRemaining = readNonNegative(value.remainingMs);
  if (storedRemaining === null) return fail('Некорректное оставшееся время.');
  const deadlineAt = readNullableTimestamp(value.deadlineAt);
  if (deadlineAt === undefined) return fail('Некорректный deadline таймера.');
  const startedAt = readNullableTimestamp(value.startedAt);
  if (startedAt === undefined) return fail('Некорректное время старта.');
  const completedAt = readNullableTimestamp(value.completedAt);
  if (completedAt === undefined) return fail('Некорректное время завершения.');

  if (!Array.isArray(value.pending)) return fail('Некорректная очередь несохранённых сессий.');
  const storedPending = value.pending.map((item) =>
    readPendingSession(item, hasClientIdSchema),
  );
  if (storedPending.some((item) => item === null)) {
    return fail('Некорректная несохранённая сессия в очереди.');
  }

  // Every session read from storage may have had an in-flight POST at reload,
  // so all of them require an explicit retry. The original payload is untouched.
  const pending: PendingSession[] = (storedPending as PendingSession[]).map((item) => ({
    ...item,
    requiresManualRetry: true,
  }));

  const clampRemaining = (valueMs: number) => Math.min(durationMs, Math.max(0, valueMs));
  // A legacy (v1) active timer never had a key, but no POST exists for it yet:
  // assigning one now is safe and keeps its future completion protected.
  const activeClientId =
    value.status === 'running' || value.status === 'paused'
      ? storedSessionClientId ?? createClientId()
      : storedSessionClientId;
  let state: TimerState;
  let expiredCompletion = false;

  switch (value.status) {
    case 'running': {
      if (deadlineAt === null || startedAt === null) {
        return fail('Работающий таймер без deadline или времени старта.');
      }
      if (deadlineAt <= now) {
        const finishedAt = deadlineAt;
        const session: CompletedSession = {
          mode,
          durationMinutes: Math.round(durationMs / 60_000),
          startedAt,
          finishedAt,
          // The timer finished while the app was closed, so no POST for this
          // session ever left the device: a fresh key is safe here.
          clientId: activeClientId ?? createClientId(),
        };
        const alreadyQueued = pending.some(
          (item) => sessionKey(item.session) === sessionKey(session),
        );
        state = {
          mode,
          status: 'completed',
          durationMs,
          remainingMs: 0,
          deadlineAt: null,
          startedAt,
          completedAt: finishedAt,
          sessionClientId: session.clientId,
          pending: alreadyQueued
            ? pending
            : [
                ...pending,
                {
                  session,
                  failedAttempts: 0,
                  requiresManualRetry: false,
                  lastError: null,
                  lastErrorKind: null,
                },
              ],
        };
        expiredCompletion = true;
      } else {
        state = {
          mode,
          status: 'running',
          durationMs,
          remainingMs: clampRemaining(deadlineAt - now),
          deadlineAt,
          startedAt,
          completedAt: null,
          sessionClientId: activeClientId,
          pending,
        };
      }
      break;
    }
    case 'paused': {
      if (startedAt === null) return fail('Приостановленный таймер без времени старта.');
      state = {
        mode,
        status: 'paused',
        durationMs,
        remainingMs: clampRemaining(storedRemaining),
        deadlineAt: null,
        startedAt,
        completedAt: null,
        sessionClientId: activeClientId,
        pending,
      };
      break;
    }
    case 'completed':
    case 'error': {
      if (startedAt === null || completedAt === null) {
        return fail('Завершённый таймер без времени старта или завершения.');
      }
      if (completedAt < startedAt) {
        return fail('Время завершения раньше времени старта.');
      }
      state = {
        mode,
        status: value.status,
        durationMs,
        remainingMs: 0,
        deadlineAt: null,
        startedAt,
        completedAt,
        sessionClientId: storedSessionClientId,
        pending,
      };
      break;
    }
    case 'idle': {
      state = { ...createTimerState(mode, durationMs), pending };
      break;
    }
  }

  return {
    kind: 'restored',
    state,
    timerRestored: state.status === 'running' || state.status === 'paused',
    restoredPending: pending.length,
    manualRetryRequired: pending.some((item) => item.requiresManualRetry),
    expiredCompletion,
  };
}

export function loadTimerState(
  now: number = Date.now(),
  storage: StorageLike | null = defaultStorage(),
): LoadResult {
  if (!storage) return { kind: 'unavailable', message: 'Локальное хранилище недоступно.' };

  let raw: string | null;
  try {
    raw = storage.getItem(FOCUS_STORAGE_KEY);
  } catch {
    return { kind: 'error', message: 'Не удалось прочитать сохранённые данные таймера.' };
  }
  return parseTimerState(raw, now);
}

export function saveTimerState(
  state: TimerState,
  storage: StorageLike | null = defaultStorage(),
  savedAt: number = Date.now(),
): SaveResult {
  if (!storage) return { ok: false, message: 'Локальное хранилище недоступно.' };
  try {
    storage.setItem(FOCUS_STORAGE_KEY, serializeTimerState(state, savedAt));
    return { ok: true };
  } catch {
    return { ok: false, message: 'Не удалось сохранить состояние таймера.' };
  }
}

function defaultStorage(): StorageLike | null {
  try {
    return globalThis.localStorage ?? null;
  } catch {
    // Access itself can throw (private mode, hardened browsers).
    return null;
  }
}

function readPendingSession(
  value: unknown,
  currentSchema: boolean,
): PendingSession | null {
  if (!isRecord(value)) return null;
  if (!isFocusMode(value.mode)) return null;

  const durationMinutes = readInteger(value.durationMinutes);
  if (durationMinutes === null || durationMinutes < 1 || durationMinutes > MAX_SESSION_MINUTES) {
    return null;
  }
  const startedAt = readNonNegative(value.startedAt);
  if (startedAt === null) return null;
  const finishedAt = readNonNegative(value.finishedAt);
  if (finishedAt === null || finishedAt < startedAt) return null;
  const failedAttempts = readInteger(value.failedAttempts);
  if (failedAttempts === null || failedAttempts < 0) return null;

  const storedManualFlag = value.requiresManualRetry;
  if (storedManualFlag !== undefined && typeof storedManualFlag !== 'boolean') return null;

  const lastError =
    typeof value.lastError === 'string' && value.lastError.trim() !== ''
      ? value.lastError.trim()
      : null;
  // Unknown values are advisory metadata, not session data: treat them as
  // absent instead of discarding an otherwise valid payload.
  const lastErrorKind =
    value.lastErrorKind === 'transient' || value.lastErrorKind === 'permanent'
      ? value.lastErrorKind
      : null;

  return {
    session: {
      mode: value.mode,
      durationMinutes,
      startedAt,
      finishedAt,
      // A legacy pending session never gets a fresh UUID: it stays unprotected
      // and requires the user's explicit confirmation.
      clientId: currentSchema ? readNullableUuid(value.clientId) : null,
    },
    failedAttempts,
    // Overwritten by the caller; kept here for type completeness.
    requiresManualRetry: true,
    lastError,
    lastErrorKind,
  };
}

function fail(message: string): LoadResult {
  return { kind: 'error', message };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function readNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function readNonNegative(value: unknown): number | null {
  const number = readNumber(value);
  return number !== null && number >= 0 ? number : null;
}

function readInteger(value: unknown): number | null {
  const number = readNumber(value);
  return number !== null && Number.isInteger(number) ? number : null;
}

function readDurationMs(value: unknown): number | null {
  const durationMs = readInteger(value);
  if (durationMs === null) return null;
  if (durationMs < MIN_SESSION_MINUTES * 60_000 || durationMs > MAX_SESSION_MINUTES * 60_000) {
    return null;
  }
  return durationMs % 60_000 === 0 ? durationMs : null;
}

function readNullableTimestamp(value: unknown): number | null | undefined {
  if (value === null) return null;
  return readNonNegative(value) ?? undefined;
}

const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * A malformed stored key is treated as absent rather than as a new key: the
 * session then keeps the legacy (manual, unprotected) behaviour.
 */
function readNullableUuid(value: unknown): string | null {
  if (typeof value !== 'string') return null;
  return UUID_PATTERN.test(value) ? value.toLowerCase() : null;
}

const TIMER_STATUSES: readonly TimerStatus[] = [
  'idle',
  'running',
  'paused',
  'completed',
  'error',
];

function isTimerStatus(value: unknown): value is TimerStatus {
  return typeof value === 'string' && (TIMER_STATUSES as readonly string[]).includes(value);
}

function isFocusMode(value: unknown): value is FocusMode {
  return (
    typeof value === 'string' &&
    FOCUS_MODES.some((mode) => mode.id === value)
  );
}
