import { DEFAULT_MODE, modeDurationMs, type FocusMode } from './modes';

export type TimerStatus = 'idle' | 'running' | 'paused' | 'completed' | 'error';

/**
 * The countdown is driven by an absolute ``deadlineAt`` timestamp. While the
 * timer is running, ``remainingMs`` is only a snapshot for rendering; every
 * transition recomputes it from ``deadlineAt`` and the caller-provided ``now``.
 */
export interface TimerState {
  mode: FocusMode;
  status: TimerStatus;
  durationMs: number;
  remainingMs: number;
  deadlineAt: number | null;
  startedAt: number | null;
  completedAt: number | null;
  /**
   * Idempotency key of the current run, generated once at Start. It survives
   * pause/resume/reload and becomes the completed session's ``clientId``.
   */
  sessionClientId: string | null;
  /**
   * Completed sessions that have not been acknowledged as saved yet. They are
   * kept in completion order and survive reset, mode switches and new starts so
   * that a failed POST never drops a finished session.
   */
  pending: readonly PendingSession[];
}

export interface PendingSession {
  /** Immutable payload of the finished session; retries reuse it verbatim. */
  session: CompletedSession;
  /** How many delivery attempts failed. Zero while the first attempt is in flight. */
  failedAttempts: number;
  /**
   * Set after a reload: we cannot know whether the earlier POST reached the
   * server, so this session must only be sent by an explicit user retry.
   */
  requiresManualRetry: boolean;
  /** Last delivery failure, kept so the UI can explain what happened. */
  lastError: string | null;
  /**
   * ``permanent`` means retrying the same payload cannot succeed (4xx such as
   * 409/422). It is skipped by "retry all", but the user can still retry it
   * explicitly and the payload is never modified.
   */
  lastErrorKind: DeliveryErrorKind | null;
}

/** Delivery failures are either worth retrying later or not. */
export type DeliveryErrorKind = 'transient' | 'permanent';

export type TimerEvent =
  | { type: 'start'; now: number; sessionId?: string }
  | { type: 'pause'; now: number }
  | { type: 'resume'; now: number }
  | { type: 'reset'; now: number }
  | { type: 'set_mode'; mode: FocusMode; now: number }
  | { type: 'tick'; now: number }
  | { type: 'saved'; key: string }
  | { type: 'fail'; key: string; errorKind?: DeliveryErrorKind; message?: string | null }
  | { type: 'discard'; key: string };

export interface CompletedSession {
  mode: FocusMode;
  durationMinutes: number;
  startedAt: number;
  finishedAt: number;
  /**
   * Idempotency key sent to the backend. ``null`` only for sessions created
   * before Stage 4.4; they must stay manual and unprotected.
   */
  clientId: string | null;
}

/**
 * Stable identity of a completed session, used to acknowledge a save.
 *
 * The idempotency key is the primary identity: two sessions can never share it.
 * Legacy sessions created before Stage 4.4 have no key, so their timestamps are
 * the durable fallback. The prefix keeps both namespaces from colliding.
 */
export function sessionKey(session: CompletedSession): string {
  if (session.clientId) return `client:${session.clientId}`;
  return `legacy:${session.startedAt}:${session.finishedAt}`;
}

/**
 * UUID v4 for the backend idempotency key. ``crypto.randomUUID`` needs a secure
 * context; the manual fallback keeps old Telegram WebViews working.
 */
export function createClientId(): string {
  const cryptoApi = globalThis.crypto;
  if (cryptoApi && typeof cryptoApi.randomUUID === 'function') {
    return cryptoApi.randomUUID();
  }
  const bytes = new Uint8Array(16);
  if (cryptoApi && typeof cryptoApi.getRandomValues === 'function') {
    cryptoApi.getRandomValues(bytes);
  } else {
    for (let index = 0; index < bytes.length; index += 1) {
      bytes[index] = Math.floor(Math.random() * 256);
    }
  }
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

export function createTimerState(mode: FocusMode = DEFAULT_MODE): TimerState {
  const durationMs = modeDurationMs(mode);
  return {
    mode,
    status: 'idle',
    durationMs,
    remainingMs: durationMs,
    deadlineAt: null,
    startedAt: null,
    completedAt: null,
    sessionClientId: null,
    pending: [],
  };
}

/** Remaining time at ``now``; never negative and never above the mode length. */
export function remainingAt(state: TimerState, now: number): number {
  if (state.status === 'running' && state.deadlineAt !== null) {
    return clampRemaining(state.deadlineAt - now, state.durationMs);
  }
  return clampRemaining(state.remainingMs, state.durationMs);
}

/** Whole seconds for the clock. Ceil keeps the initial state at the full length. */
export function displaySeconds(remainingMs: number): number {
  return Math.ceil(Math.max(0, remainingMs) / 1000);
}

export function formatClock(seconds: number): string {
  const safe = Math.max(0, Math.floor(seconds));
  const minutes = Math.floor(safe / 60);
  return `${String(minutes).padStart(2, '0')}:${String(safe % 60).padStart(2, '0')}`;
}

// AI assistance: DeepSeek helped implement and debug the timer state machine and pending-session handling.
export function timerReducer(state: TimerState, event: TimerEvent): TimerState {
  switch (event.type) {
    case 'start': {
      if (state.status === 'running') return state;
      const freshSession = state.status === 'completed' || state.status === 'error';
      const remainingMs =
        freshSession || state.remainingMs <= 0 ? state.durationMs : state.remainingMs;
      return {
        ...state,
        status: 'running',
        remainingMs,
        deadlineAt: event.now + remainingMs,
        startedAt: freshSession || state.startedAt === null ? event.now : state.startedAt,
        completedAt: null,
        sessionClientId:
          freshSession || state.sessionClientId === null
            ? event.sessionId ?? state.sessionClientId
            : state.sessionClientId,
      };
    }
    case 'pause': {
      if (state.status !== 'running' || state.deadlineAt === null) return state;
      const remainingMs = clampRemaining(state.deadlineAt - event.now, state.durationMs);
      if (remainingMs <= 0) return complete(state, state.deadlineAt);
      return { ...state, status: 'paused', remainingMs, deadlineAt: null };
    }
    case 'resume': {
      if (state.status !== 'paused') return state;
      const remainingMs = clampRemaining(state.remainingMs, state.durationMs);
      if (remainingMs <= 0) return complete(state, event.now);
      return { ...state, status: 'running', deadlineAt: event.now + remainingMs };
    }
    case 'reset':
      // Resetting the clock must never discard unsaved sessions.
      return { ...createTimerState(state.mode), pending: state.pending };
    case 'set_mode':
      return { ...createTimerState(event.mode), pending: state.pending };
    case 'tick': {
      if (state.status !== 'running' || state.deadlineAt === null) return state;
      const remainingMs = state.deadlineAt - event.now;
      if (remainingMs <= 0) {
        // The session ended at the deadline, not when the tick was observed.
        return complete(state, state.deadlineAt);
      }
      return { ...state, remainingMs: clampRemaining(remainingMs, state.durationMs) };
    }
    case 'saved': {
      const pending = state.pending.filter((item) => sessionKey(item.session) !== event.key);
      if (pending.length === state.pending.length) return state;
      return {
        ...state,
        // The save error only describes the completed session, never a new run.
        status: state.status === 'error' && pending.length === 0 ? 'completed' : state.status,
        pending,
      };
    }
    case 'fail': {
      const index = state.pending.findIndex((item) => sessionKey(item.session) === event.key);
      if (index === -1) return state;
      const message = event.message?.trim();
      const pending = state.pending.map((item, itemIndex) =>
        itemIndex === index
          ? {
              ...item,
              failedAttempts: item.failedAttempts + 1,
              lastError: message ? message : null,
              lastErrorKind: event.errorKind ?? null,
            }
          : item,
      );
      return {
        ...state,
        // A failure while a new session runs must not stop that session.
        status: state.status === 'completed' ? 'error' : state.status,
        pending,
      };
    }
    case 'discard': {
      // Only an explicit user action reaches this branch. Removing one session
      // must never touch the others that are still waiting.
      const pending = state.pending.filter((item) => sessionKey(item.session) !== event.key);
      if (pending.length === state.pending.length) return state;
      return {
        ...state,
        status: state.status === 'error' && pending.length === 0 ? 'completed' : state.status,
        pending,
      };
    }
  }
}

function complete(state: TimerState, finishedAt: number): TimerState {
  return {
    ...state,
    status: 'completed',
    remainingMs: 0,
    deadlineAt: null,
    completedAt: finishedAt,
    pending: [
      ...state.pending,
      {
        session: toCompletedSession(state, finishedAt),
        failedAttempts: 0,
        requiresManualRetry: false,
        lastError: null,
        lastErrorKind: null,
      },
    ],
  };
}

function toCompletedSession(state: TimerState, finishedAt: number): CompletedSession {
  return {
    mode: state.mode,
    durationMinutes: Math.round(state.durationMs / 60_000),
    startedAt: state.startedAt ?? Math.max(0, finishedAt - state.durationMs),
    finishedAt,
    clientId: state.sessionClientId,
  };
}

function clampRemaining(value: number, durationMs: number): number {
  return Math.min(durationMs, Math.max(0, value));
}

/**
 * Decides whether a completion still needs to be handed to the network layer.
 * StrictMode re-runs effects, so the same session must be claimed only once.
 * An explicit retry bypasses the tracker on purpose: it is user-driven.
 */
export interface DeliveryTracker {
  claim(session: CompletedSession): boolean;
}

export function createDeliveryTracker(): DeliveryTracker {
  let claimedKey: string | null = null;
  return {
    claim(session) {
      const key = sessionKey(session);
      if (claimedKey === key) return false;
      claimedKey = key;
      return true;
    },
  };
}

/**
 * Only a fresh completion may be sent without a user action. Restored sessions
 * and sessions that already failed need an explicit retry (duplicate safety).
 */
export function isAutoDeliverable(item: PendingSession): boolean {
  return !item.requiresManualRetry && item.failedAttempts === 0;
}

/**
 * Sessions offered by "retry all". A permanently rejected payload is skipped so
 * it cannot block the rest of the queue; it stays visible and can still be
 * retried or discarded individually by the user.
 */
export function retryablePending(
  pending: readonly PendingSession[],
): PendingSession[] {
  return pending.filter((item) => item.lastErrorKind !== 'permanent');
}

/**
 * First session that "retry all" may send, optionally skipping the session the
 * user chose to bypass. This is what makes a poisoned queue head non-blocking.
 */
export function nextRetryablePending(
  pending: readonly PendingSession[],
  excludeKey?: string,
): PendingSession | undefined {
  return retryablePending(pending).find(
    (item) => sessionKey(item.session) !== excludeKey,
  );
}
