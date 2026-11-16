import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from 'react';
import type { FocusMode } from './modes';
import {
  durationMsFromMinutes,
  getMode,
  isValidDurationMinutes,
  modeDurationMs,
} from './modes';
import {
  loadTimerState,
  persistenceSignature,
  saveTimerState,
  type LoadResult,
} from './persistence';
import {
  createClientId,
  createDeliveryTracker,
  createTimerState,
  displaySeconds,
  isAutoDeliverable,
  nextRetryablePending,
  retryablePending,
  sessionKey,
  timerReducer,
  type CompletedSession,
  type DeliveryErrorKind,
  type DeliveryTracker,
  type PendingSession,
  type TimerStatus,
} from './timer';

export interface UseFocusTimerOptions {
  /** Delivers a completed session to the network layer. Never called from the reducer. */
  onComplete?: (session: CompletedSession) => void;
}

export interface DeliveryFailureInfo {
  kind: DeliveryErrorKind;
  message: string;
}

export interface RestoreNotice {
  kind: 'restored' | 'error';
  message?: string;
  timerRestored: boolean;
  hadPending: boolean;
  manualRetryRequired: boolean;
}

export interface FocusTimerController {
  mode: FocusMode;
  status: TimerStatus;
  remainingMs: number;
  remainingSeconds: number;
  durationMs: number;
  durationMinutes: number;
  recommendedMinutes: number;
  isCustomDuration: boolean;
  setDuration: (minutes: number) => boolean;
  isRunning: boolean;
  isPaused: boolean;
  /** Finished sessions that still need to be saved, oldest first. */
  pending: readonly PendingSession[];
  pendingCount: number;
  hasPending: boolean;
  /** True when at least one save attempt failed and a retry is possible. */
  hasSaveError: boolean;
  /** True when a restored session may already have reached the server. */
  hasManualPending: boolean;
  /** True when at least one pending session has no client_id (legacy). */
  hasUnprotectedPending: boolean;
  /** Pending sessions that "retry all" may send (permanent errors excluded). */
  retryableCount: number;
  restoreNotice: RestoreNotice | null;
  dismissRestoreNotice: () => void;
  start: () => void;
  pause: () => void;
  resume: () => void;
  reset: () => void;
  setMode: (mode: FocusMode) => void;
  /**
   * Explicit, user-triggered resend. Pass a session to retry exactly that one;
   * omit it to retry the first session that is still worth retrying.
   */
  retryPending: (session?: CompletedSession) => void;
  /** Explicit resend of every pending session that is not permanently rejected. */
  retryAllPending: () => void;
  /** Retry the first retryable session other than ``exclude`` (skip a bad one). */
  retryNextPending: (exclude?: CompletedSession) => void;
  /** Remove exactly one pending session; the UI must ask the user first. */
  discardPending: (session: CompletedSession) => void;
  /** Acknowledge a successful POST; removes exactly that session from the queue. */
  markSaved: (session: CompletedSession) => void;
  /** Acknowledge a failed POST; keeps the original session data untouched. */
  markFailed: (session: CompletedSession, failure?: DeliveryFailureInfo) => void;
}

const TICK_INTERVAL_MS = 1000;

export function useFocusTimer({ onComplete }: UseFocusTimerOptions = {}): FocusTimerController {
  const [restoreResult] = useState<LoadResult>(() => loadTimerState(Date.now()));
  const initialState = restoreResult.kind === 'restored' ? restoreResult.state : createTimerState();
  const [state, dispatch] = useReducer(timerReducer, initialState);

  // Keep the latest callback without re-creating the effects on every render.
  const onCompleteRef = useRef(onComplete);
  useEffect(() => {
    onCompleteRef.current = onComplete;
  }, [onComplete]);

  // A completion is delivered exactly once, even if the effect runs twice in StrictMode.
  const trackerRef = useRef<DeliveryTracker | null>(null);
  if (trackerRef.current === null) trackerRef.current = createDeliveryTracker();

  // One POST per session at a time: a double click or "retry all" must not send
  // the same immutable payload twice. The backend stays idempotent regardless.
  const inFlightRef = useRef<Set<string>>(new Set());
  const deliver = useCallback((session: CompletedSession) => {
    const deliverer = onCompleteRef.current;
    if (!deliverer) return;
    const key = sessionKey(session);
    if (inFlightRef.current.has(key)) return;
    inFlightRef.current.add(key);
    try {
      deliverer(session);
    } catch {
      inFlightRef.current.delete(key);
    }
  }, []);

  // Restoring may produce a completion whose deadline expired while the app was
  // closed; that one is safe to deliver. Everything else starts already saved,
  // so mounting does not rewrite storage or clobber unreadable data.
  const [initialSignature] = useState(() =>
    restoreResult.kind === 'restored' && restoreResult.expiredCompletion
      ? null
      : persistenceSignature(initialState),
  );
  const lastSavedSignatureRef = useRef<string | null>(initialSignature);

  const [restoreNotice, setRestoreNotice] = useState<RestoreNotice | null>(() => {
    if (restoreResult.kind === 'error') {
      return {
        kind: 'error',
        message: restoreResult.message,
        timerRestored: false,
        hadPending: false,
        manualRetryRequired: false,
      };
    }
    if (restoreResult.kind !== 'restored') return null;
    if (!restoreResult.timerRestored && restoreResult.restoredPending === 0) return null;
    return {
      kind: 'restored',
      timerRestored: restoreResult.timerRestored,
      hadPending: restoreResult.restoredPending > 0,
      manualRetryRequired: restoreResult.manualRetryRequired,
    };
  });
  const dismissRestoreNotice = useCallback(() => setRestoreNotice(null), []);

  useEffect(() => {
    if (state.status !== 'running') return;
    // The interval only refreshes the display; elapsed time comes from the deadline.
    const id = window.setInterval(() => {
      dispatch({ type: 'tick', now: Date.now() });
    }, TICK_INTERVAL_MS);
    return () => window.clearInterval(id);
  }, [state.status]);

  useEffect(() => {
    const sync = () => dispatch({ type: 'tick', now: Date.now() });
    document.addEventListener('visibilitychange', sync);
    window.addEventListener('focus', sync);
    window.addEventListener('pageshow', sync);
    return () => {
      document.removeEventListener('visibilitychange', sync);
      window.removeEventListener('focus', sync);
      window.removeEventListener('pageshow', sync);
    };
  }, []);

  // Re-sync the restored countdown right after mount.
  useEffect(() => {
    dispatch({ type: 'tick', now: Date.now() });
  }, []);

  useEffect(() => {
    // Ticks change remainingMs every second; the signature ignores it while
    // running, so storage is written only on meaningful transitions.
    const signature = persistenceSignature(state);
    if (lastSavedSignatureRef.current === signature) return;
    lastSavedSignatureRef.current = signature;
    saveTimerState(state);
  }, [state]);

  useEffect(() => {
    const latest = state.pending[state.pending.length - 1];
    // Restored and previously failed sessions wait for an explicit retry.
    if (!latest || !isAutoDeliverable(latest)) return;
    const tracker = trackerRef.current;
    if (!tracker || !tracker.claim(latest.session)) return;
    deliver(latest.session);
  }, [state.pending, deliver]);

  const start = useCallback(
    () => dispatch({ type: 'start', now: Date.now(), sessionId: createClientId() }),
    [],
  );
  const pause = useCallback(() => dispatch({ type: 'pause', now: Date.now() }), []);
  const resume = useCallback(() => dispatch({ type: 'resume', now: Date.now() }), []);
  const reset = useCallback(() => dispatch({ type: 'reset', now: Date.now() }), []);
  const setMode = useCallback(
    (mode: FocusMode) => dispatch({ type: 'set_mode', mode, now: Date.now() }),
    [],
  );
  const setDuration = useCallback((minutes: number) => {
    if (state.status === 'running' || state.status === 'paused') return false;
    if (!isValidDurationMinutes(minutes)) return false;
    dispatch({
      type: 'set_duration',
      durationMs: durationMsFromMinutes(minutes),
      now: Date.now(),
    });
    return true;
  }, [state.status]);
  const retryPending = useCallback(
    (session?: CompletedSession) => {
      // One POST per user action: no timers, no automatic retry loops.
      const target = session
        ? session
        : nextRetryablePending(state.pending)?.session;
      if (target) deliver(target);
    },
    [state.pending, deliver],
  );
  const retryAllPending = useCallback(() => {
    // Permanently rejected sessions are skipped so one bad payload cannot block
    // the rest of the queue.
    retryablePending(state.pending).forEach((item) => deliver(item.session));
  }, [state.pending, deliver]);
  const retryNextPending = useCallback(
    (exclude?: CompletedSession) => {
      const target = nextRetryablePending(
        state.pending,
        exclude ? sessionKey(exclude) : undefined,
      );
      if (target) deliver(target.session);
    },
    [state.pending, deliver],
  );
  const discardPending = useCallback((session: CompletedSession) => {
    const key = sessionKey(session);
    inFlightRef.current.delete(key);
    dispatch({ type: 'discard', key });
  }, []);
  const markSaved = useCallback((session: CompletedSession) => {
    const key = sessionKey(session);
    inFlightRef.current.delete(key);
    dispatch({ type: 'saved', key });
  }, []);
  const markFailed = useCallback(
    (session: CompletedSession, failure?: DeliveryFailureInfo) => {
      const key = sessionKey(session);
      inFlightRef.current.delete(key);
      dispatch({
        type: 'fail',
        key,
        errorKind: failure?.kind,
        message: failure?.message,
      });
    },
    [],
  );

  const hasSaveError = state.pending.some((item) => item.failedAttempts > 0);
  const hasManualPending = state.pending.some((item) => item.requiresManualRetry);
  const hasUnprotectedPending = state.pending.some(
    (item) => item.session.clientId === null,
  );
  const retryableCount = retryablePending(state.pending).length;

  return useMemo(
    () => ({
      mode: state.mode,
      status: state.status,
      remainingMs: state.remainingMs,
      remainingSeconds: displaySeconds(state.remainingMs),
      durationMs: state.durationMs,
      durationMinutes: Math.round(state.durationMs / 60_000),
      recommendedMinutes: getMode(state.mode).minutes,
      isCustomDuration: state.durationMs !== modeDurationMs(state.mode),
      setDuration,
      isRunning: state.status === 'running',
      isPaused: state.status === 'paused',
      pending: state.pending,
      pendingCount: state.pending.length,
      hasPending: state.pending.length > 0,
      hasSaveError,
      hasManualPending,
      hasUnprotectedPending,
      retryableCount,
      restoreNotice,
      dismissRestoreNotice,
      start,
      pause,
      resume,
      reset,
      setMode,
      retryPending,
      retryAllPending,
      retryNextPending,
      discardPending,
      markSaved,
      markFailed,
    }),
    [
      state,
      hasSaveError,
      hasManualPending,
      hasUnprotectedPending,
      retryableCount,
      restoreNotice,
      dismissRestoreNotice,
      start,
      pause,
      resume,
      reset,
      setMode,
      setDuration,
      retryPending,
      retryAllPending,
      retryNextPending,
      discardPending,
      markSaved,
      markFailed,
    ],
  );
}
