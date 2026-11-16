import { describe, expect, it } from 'vitest';
import { DEFAULT_MODE, FOCUS_MODES, getMode, modeDurationMs } from './modes';
import {
  createClientId,
  createDeliveryTracker,
  createTimerState,
  displaySeconds,
  formatClock,
  nextRetryablePending,
  remainingAt,
  retryablePending,
  sessionKey,
  timerReducer,
  type TimerState,
} from './timer';

const T0 = 1_700_000_000_000;
const FOCUS_MS = 25 * 60_000;
const SHORT_MS = 5 * 60_000;
const LONG_MS = 15 * 60_000;

function startFocus(now = T0): TimerState {
  return timerReducer(createTimerState('focus'), { type: 'start', now });
}

function completeFocus(now = T0): TimerState {
  return timerReducer(startFocus(now), { type: 'tick', now: now + FOCUS_MS });
}

function failLatest(state: TimerState): TimerState {
  const latest = state.pending[state.pending.length - 1];
  return latest ? timerReducer(state, { type: 'fail', key: sessionKey(latest.session) }) : state;
}

function saveLatest(state: TimerState): TimerState {
  const latest = state.pending[state.pending.length - 1];
  return latest ? timerReducer(state, { type: 'saved', key: sessionKey(latest.session) }) : state;
}

describe('focus modes', () => {
  it('keeps the existing focus, short break and long break lengths', () => {
    expect(FOCUS_MODES.map((mode) => mode.id)).toEqual(['focus', 'short_break', 'long_break']);
    expect(modeDurationMs('focus')).toBe(FOCUS_MS);
    expect(modeDurationMs('short_break')).toBe(SHORT_MS);
    expect(modeDurationMs('long_break')).toBe(LONG_MS);
    expect(DEFAULT_MODE).toBe('focus');
    expect(getMode('short_break').label).toBe('Короткий перерыв');
  });
});

describe('createTimerState', () => {
  it('starts idle with the full duration of the default mode', () => {
    const state = createTimerState();
    expect(state).toEqual({
      mode: 'focus',
      status: 'idle',
      durationMs: FOCUS_MS,
      remainingMs: FOCUS_MS,
      deadlineAt: null,
      startedAt: null,
      completedAt: null,
      sessionClientId: null,
      pending: [],
    });
    expect(displaySeconds(state.remainingMs)).toBe(1500);
  });

  it('uses the requested mode duration', () => {
    expect(createTimerState('short_break').remainingMs).toBe(SHORT_MS);
    expect(createTimerState('long_break').remainingMs).toBe(LONG_MS);
  });
});

describe('start', () => {
  it('creates an absolute deadline and records the real start', () => {
    const state = startFocus();
    expect(state.status).toBe('running');
    expect(state.deadlineAt).toBe(T0 + FOCUS_MS);
    expect(state.remainingMs).toBe(FOCUS_MS);
    expect(state.startedAt).toBe(T0);
    expect(state.completedAt).toBeNull();
  });

  it('is a no-op while already running', () => {
    const running = startFocus();
    const repeated = timerReducer(running, { type: 'start', now: T0 + 5_000 });
    expect(repeated).toBe(running);
  });

  it('keeps the original start time when started from pause', () => {
    const started = startFocus();
    const paused = timerReducer(started, { type: 'pause', now: T0 + 10_000 });
    const restarted = timerReducer(paused, { type: 'start', now: T0 + 60_000 });

    expect(restarted.status).toBe('running');
    expect(restarted.startedAt).toBe(T0);
    expect(restarted.deadlineAt).toBe(T0 + 60_000 + remainingAt(paused, T0 + 60_000));
  });

  it('begins a fresh session from completed with a new start time', () => {
    const done = completeFocus();
    const freshAt = T0 + FOCUS_MS + 1_000;
    const fresh = timerReducer(done, { type: 'start', now: freshAt });

    expect(fresh.status).toBe('running');
    expect(fresh.remainingMs).toBe(FOCUS_MS);
    expect(fresh.deadlineAt).toBe(freshAt + FOCUS_MS);
    expect(fresh.startedAt).toBe(freshAt);
    expect(fresh.completedAt).toBeNull();
  });
});

describe('pause and resume', () => {
  it('stores the exact remaining time and creates a new deadline on resume', () => {
    const started = startFocus();
    const ticked = timerReducer(started, { type: 'tick', now: T0 + 10_000 });
    const paused = timerReducer(ticked, { type: 'pause', now: T0 + 10_500 });

    expect(paused.status).toBe('paused');
    expect(paused.deadlineAt).toBeNull();
    expect(paused.remainingMs).toBe(1_489_500);
    expect(paused.startedAt).toBe(T0);

    const resumed = timerReducer(paused, { type: 'resume', now: T0 + 30_500 });
    expect(resumed.status).toBe('running');
    expect(resumed.deadlineAt).toBe(T0 + 30_500 + 1_489_500);
    expect(remainingAt(resumed, T0 + 30_500)).toBe(1_489_500);
  });

  it('completes instead of pausing exactly at the deadline', () => {
    const started = startFocus();
    const paused = timerReducer(started, { type: 'pause', now: T0 + FOCUS_MS });

    expect(paused.status).toBe('completed');
    expect(paused.remainingMs).toBe(0);
    expect(paused.completedAt).toBe(T0 + FOCUS_MS);
    expect(paused.deadlineAt).toBeNull();
    expect(paused.pending).toHaveLength(1);
  });

  it('ignores pause before start and resume while running', () => {
    const idle = createTimerState();
    expect(timerReducer(idle, { type: 'pause', now: T0 })).toBe(idle);

    const running = startFocus();
    expect(timerReducer(running, { type: 'resume', now: T0 })).toBe(running);
  });

  it('ignores ticks while paused', () => {
    const paused = timerReducer(startFocus(), { type: 'pause', now: T0 + 1_000 });
    expect(timerReducer(paused, { type: 'tick', now: T0 + 5_000 })).toBe(paused);
  });
});

describe('reset', () => {
  it('restores the full duration of the current mode and clears the session', () => {
    const paused = timerReducer(startFocus(), { type: 'pause', now: T0 + 2_000 });
    const reset = timerReducer(paused, { type: 'reset', now: T0 + 3_000 });

    expect(reset).toEqual(createTimerState('focus'));
    expect(reset.status).toBe('idle');
    expect(reset.remainingMs).toBe(FOCUS_MS);
    expect(reset.startedAt).toBeNull();
    expect(reset.completedAt).toBeNull();
  });
});

describe('mode switching', () => {
  it('stops the timer and uses the new mode length', () => {
    const switched = timerReducer(startFocus(), {
      type: 'set_mode',
      mode: 'short_break',
      now: T0 + 1_000,
    });

    expect(switched).toEqual(createTimerState('short_break'));
    expect(switched.durationMs).toBe(SHORT_MS);
    expect(switched.remainingMs).toBe(SHORT_MS);
    expect(switched.status).toBe('idle');
    expect(switched.startedAt).toBeNull();
  });

  it('resets even when the same mode is selected again', () => {
    const running = timerReducer(startFocus(), { type: 'tick', now: T0 + 5_000 });
    const switched = timerReducer(running, { type: 'set_mode', mode: 'focus', now: T0 + 6_000 });

    expect(switched.status).toBe('idle');
    expect(switched.remainingMs).toBe(FOCUS_MS);
  });
});

describe('remaining time', () => {
  it('is derived from the deadline, not from the number of ticks', () => {
    const started = startFocus();
    // One late tick after a 3s event-loop stall must still lose 3s.
    const ticked = timerReducer(started, { type: 'tick', now: T0 + 3_000 });

    expect(ticked.remainingMs).toBe(FOCUS_MS - 3_000);
    expect(displaySeconds(ticked.remainingMs)).toBe(1497);
  });

  it('reports the exact remainder for any timestamp', () => {
    const started = startFocus();
    expect(remainingAt(started, T0)).toBe(FOCUS_MS);
    expect(remainingAt(started, T0 + 123_456)).toBe(FOCUS_MS - 123_456);
    expect(remainingAt(started, T0 + FOCUS_MS)).toBe(0);
  });

  it('never exceeds the mode length when the clock jumps backwards', () => {
    const started = startFocus();
    const jumped = timerReducer(started, { type: 'tick', now: T0 - 5_000 });

    expect(jumped.remainingMs).toBe(FOCUS_MS);
  });
});

describe('completion', () => {
  it('completes exactly at the deadline', () => {
    const started = startFocus();
    const almost = timerReducer(started, { type: 'tick', now: T0 + FOCUS_MS - 1 });
    expect(almost.status).toBe('running');
    expect(almost.remainingMs).toBe(1);
    expect(displaySeconds(almost.remainingMs)).toBe(1);

    const done = timerReducer(almost, { type: 'tick', now: T0 + FOCUS_MS });
    expect(done.status).toBe('completed');
    expect(done.remainingMs).toBe(0);
    expect(done.completedAt).toBe(T0 + FOCUS_MS);
    expect(done.deadlineAt).toBeNull();
    expect(done.startedAt).toBe(T0);
  });

  it('finishes at the real deadline even when the tick arrives much later', () => {
    const started = startFocus();
    const done = timerReducer(started, { type: 'tick', now: T0 + FOCUS_MS + 45_000 });

    expect(done.status).toBe('completed');
    expect(done.completedAt).toBe(T0 + FOCUS_MS);
    expect(done.remainingMs).toBe(0);
    expect(done.pending[0].session.finishedAt).toBe(T0 + FOCUS_MS);
  });

  it('never reports negative remaining time', () => {
    const started = startFocus();
    const done = timerReducer(started, { type: 'tick', now: T0 + FOCUS_MS * 10 });

    expect(done.remainingMs).toBe(0);
    expect(remainingAt(started, T0 + FOCUS_MS * 10)).toBe(0);
    expect(displaySeconds(-5)).toBe(0);
  });

  it('does not complete twice', () => {
    const done = completeFocus();
    const after = timerReducer(done, { type: 'tick', now: T0 + FOCUS_MS + 5_000 });

    expect(after).toBe(done);
    expect(after.completedAt).toBe(T0 + FOCUS_MS);
    expect(after.pending).toHaveLength(1);
  });
});

describe('error state', () => {
  it('marks a completed session as failed without losing its timestamps', () => {
    const done = completeFocus();
    const failed = failLatest(done);

    expect(failed.status).toBe('error');
    expect(failed.startedAt).toBe(T0);
    expect(failed.completedAt).toBe(T0 + FOCUS_MS);
    expect(failed.remainingMs).toBe(0);
    expect(failed.pending).toHaveLength(1);
    expect(failed.pending[0].failedAttempts).toBe(1);
  });

  it('only allows a failure for a known pending session', () => {
    const running = startFocus();
    expect(timerReducer(running, { type: 'fail', key: 'unknown' })).toBe(running);

    const paused = timerReducer(startFocus(), { type: 'pause', now: T0 + 1_000 });
    expect(timerReducer(paused, { type: 'fail', key: 'unknown' })).toBe(paused);
  });

  it('can start a new session after a failure', () => {
    const failed = failLatest(completeFocus());
    const nextAt = T0 + FOCUS_MS + 10_000;
    const next = timerReducer(failed, { type: 'start', now: nextAt });

    expect(next.status).toBe('running');
    expect(next.completedAt).toBeNull();
    expect(next.startedAt).toBe(nextAt);
    expect(next.remainingMs).toBe(FOCUS_MS);
  });
});

describe('pending sessions after a failed save', () => {
  it('keeps the completed session with its original payload', () => {
    const done = completeFocus();

    expect(done.pending).toHaveLength(1);
    expect(done.pending[0]).toEqual({
      session: {
        mode: 'focus',
        durationMinutes: 25,
        startedAt: T0,
        finishedAt: T0 + FOCUS_MS,
        clientId: null,
      },
      failedAttempts: 0,
      requiresManualRetry: false,
      lastError: null,
      lastErrorKind: null,
    });
  });

  it('does not lose the pending session when a new session starts', () => {
    const failed = failLatest(completeFocus());
    const nextAt = T0 + FOCUS_MS + 5_000;
    const next = timerReducer(failed, { type: 'start', now: nextAt });

    expect(next.status).toBe('running');
    expect(next.startedAt).toBe(nextAt);
    expect(next.pending).toEqual(failed.pending);
    expect(next.pending[0].session.startedAt).toBe(T0);
    expect(next.pending[0].session.finishedAt).toBe(T0 + FOCUS_MS);
  });

  it('keeps the original data when a retry fails again', () => {
    const firstFailure = failLatest(completeFocus());
    const secondFailure = failLatest(firstFailure);

    expect(secondFailure.pending).toHaveLength(1);
    expect(secondFailure.pending[0].session).toEqual(firstFailure.pending[0].session);
    expect(secondFailure.pending[0].failedAttempts).toBe(2);
    expect(secondFailure.pending[0].session).toEqual({
      mode: 'focus',
      durationMinutes: 25,
      startedAt: T0,
      finishedAt: T0 + FOCUS_MS,
      clientId: null,
    });
  });

  it('clears the pending session after a successful retry', () => {
    const failed = failLatest(completeFocus());
    const saved = saveLatest(failed);

    expect(saved.pending).toHaveLength(0);
    expect(saved.status).toBe('completed');
    expect(saved.completedAt).toBe(T0 + FOCUS_MS);
  });

  it('allows a new session after the pending one was saved', () => {
    const saved = saveLatest(failLatest(completeFocus()));
    const nextAt = T0 + FOCUS_MS + 7_000;
    const next = timerReducer(saved, { type: 'start', now: nextAt });

    expect(next.status).toBe('running');
    expect(next.pending).toHaveLength(0);
    expect(next.startedAt).toBe(nextAt);
    expect(next.remainingMs).toBe(FOCUS_MS);
  });

  it('delivers a completion once even if the effect runs twice (StrictMode)', () => {
    const tracker = createDeliveryTracker();
    const done = completeFocus();
    const session = done.pending[0].session;

    const delivered = [tracker.claim(session), tracker.claim(session)];

    expect(delivered).toEqual([true, false]);
    expect(tracker.claim({ ...session, finishedAt: session.finishedAt + 1 })).toBe(true);
  });

  it('preserves pending sessions across reset', () => {
    const failed = failLatest(completeFocus());
    const reset = timerReducer(failed, { type: 'reset', now: T0 + FOCUS_MS + 2_000 });

    expect(reset.status).toBe('idle');
    expect(reset.remainingMs).toBe(FOCUS_MS);
    expect(reset.startedAt).toBeNull();
    expect(reset.pending).toEqual(failed.pending);
  });

  it('preserves pending sessions across a mode switch', () => {
    const failed = failLatest(completeFocus());
    const switched = timerReducer(failed, {
      type: 'set_mode',
      mode: 'short_break',
      now: T0 + FOCUS_MS + 2_000,
    });

    expect(switched.mode).toBe('short_break');
    expect(switched.status).toBe('idle');
    expect(switched.remainingMs).toBe(SHORT_MS);
    expect(switched.pending).toEqual(failed.pending);
  });

  it('keeps both sessions in a queue and removes them one at a time', () => {
    const first = completeFocus();
    const secondStart = T0 + FOCUS_MS + 1_000;
    const second = timerReducer(
      timerReducer(first, { type: 'start', now: secondStart }),
      { type: 'tick', now: secondStart + FOCUS_MS },
    );

    expect(second.pending).toHaveLength(2);
    const [older, newer] = second.pending;

    const savedOlder = timerReducer(second, { type: 'saved', key: sessionKey(older.session) });
    expect(savedOlder.pending).toHaveLength(1);
    expect(savedOlder.pending[0].session).toEqual(newer.session);

    const savedNewer = timerReducer(savedOlder, { type: 'saved', key: sessionKey(newer.session) });
    expect(savedNewer.pending).toHaveLength(0);
  });

  it('does not remove a different session when the same save is acknowledged twice', () => {
    const first = completeFocus();
    const secondStart = T0 + FOCUS_MS + 1_000;
    const second = timerReducer(
      timerReducer(first, { type: 'start', now: secondStart }),
      { type: 'tick', now: secondStart + FOCUS_MS },
    );
    const key = sessionKey(second.pending[0].session);

    const savedOnce = timerReducer(second, { type: 'saved', key });
    const savedTwice = timerReducer(savedOnce, { type: 'saved', key });

    expect(savedTwice).toBe(savedOnce);
    expect(savedTwice.pending).toHaveLength(1);
  });

  it('ignores unknown save and failure acknowledgements', () => {
    const done = completeFocus();

    expect(timerReducer(done, { type: 'saved', key: 'unknown' })).toBe(done);
    expect(timerReducer(done, { type: 'fail', key: 'unknown' })).toBe(done);
  });

  it('does not stop a running timer when an older save fails', () => {
    const failed = failLatest(completeFocus());
    const running = timerReducer(failed, { type: 'start', now: T0 + FOCUS_MS + 3_000 });
    const stillRunning = timerReducer(running, {
      type: 'fail',
      key: sessionKey(failed.pending[0].session),
    });

    expect(stillRunning.status).toBe('running');
    expect(stillRunning.deadlineAt).toBe(running.deadlineAt);
    expect(stillRunning.pending[0].failedAttempts).toBe(2);
  });
});

describe('client id', () => {
  const CLIENT_A = '11111111-1111-4111-8111-111111111111';
  const CLIENT_B = '22222222-2222-4222-8222-222222222222';

  it('creates UUID v4 values', () => {
    const first = createClientId();
    const second = createClientId();

    expect(first).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
    );
    expect(second).not.toBe(first);
  });

  it('keeps one id through start, pause, resume and completion', () => {
    const started = timerReducer(createTimerState('focus'), {
      type: 'start',
      now: T0,
      sessionId: CLIENT_A,
    });
    expect(started.sessionClientId).toBe(CLIENT_A);

    const paused = timerReducer(started, { type: 'pause', now: T0 + 1_000 });
    expect(paused.sessionClientId).toBe(CLIENT_A);

    const resumed = timerReducer(paused, { type: 'resume', now: T0 + 2_000 });
    expect(resumed.sessionClientId).toBe(CLIENT_A);

    const done = timerReducer(resumed, {
      type: 'tick',
      now: resumed.deadlineAt as number,
    });
    expect(done.pending[0].session.clientId).toBe(CLIENT_A);
  });

  it('uses a new id for a new session and clears it on reset', () => {
    const done = timerReducer(startFocus(), {
      type: 'tick',
      now: T0 + FOCUS_MS,
    });
    const doneWithId = timerReducer(
      timerReducer(createTimerState('focus'), {
        type: 'start',
        now: T0,
        sessionId: CLIENT_A,
      }),
      { type: 'tick', now: T0 + FOCUS_MS },
    );
    expect(done.pending[0].session.clientId).toBeNull();

    const next = timerReducer(doneWithId, {
      type: 'start',
      now: T0 + FOCUS_MS + 1_000,
      sessionId: CLIENT_B,
    });
    expect(next.sessionClientId).toBe(CLIENT_B);

    const reset = timerReducer(next, { type: 'reset', now: T0 + FOCUS_MS + 2_000 });
    expect(reset.sessionClientId).toBeNull();
  });
});

describe('delivery queue isolation', () => {
  const CLIENT_A = '11111111-1111-4111-8111-111111111111';
  const CLIENT_B = '22222222-2222-4222-8222-222222222222';

  function completeWithId(clientId: string, now = T0): TimerState {
    return timerReducer(
      timerReducer(createTimerState('focus'), {
        type: 'start',
        now,
        sessionId: clientId,
      }),
      { type: 'tick', now: now + FOCUS_MS },
    );
  }

  function twoPending(): TimerState {
    const secondStart = T0 + FOCUS_MS + 1_000;
    return timerReducer(
      timerReducer(completeWithId(CLIENT_A), {
        type: 'start',
        now: secondStart,
        sessionId: CLIENT_B,
      }),
      { type: 'tick', now: secondStart + FOCUS_MS },
    );
  }

  function failSession(
    state: TimerState,
    clientId: string | null,
    errorKind: 'transient' | 'permanent',
    message = 'delivery failed',
  ): TimerState {
    const target = state.pending.find(
      (item) => item.session.clientId === clientId,
    );
    if (!target) throw new Error('pending session missing');
    return timerReducer(state, {
      type: 'fail',
      key: sessionKey(target.session),
      errorKind,
      message,
    });
  }

  it('uses the client id as the primary session key', () => {
    const withId = completeWithId(CLIENT_A);
    expect(sessionKey(withId.pending[0].session)).toBe(`client:${CLIENT_A}`);

    const legacy = completeFocus();
    expect(sessionKey(legacy.pending[0].session)).toBe(
      `legacy:${T0}:${T0 + FOCUS_MS}`,
    );
  });

  it('does not collide two sessions that share timestamps but have different ids', () => {
    const base = {
      mode: 'focus' as const,
      durationMinutes: 25,
      startedAt: T0,
      finishedAt: T0 + FOCUS_MS,
    };
    expect(sessionKey({ ...base, clientId: CLIENT_A })).not.toBe(
      sessionKey({ ...base, clientId: CLIENT_B }),
    );
  });

  it('keeps a permanent failure with its reason and untouched payload', () => {
    const done = completeFocus();
    const original = done.pending[0].session;
    const failed = timerReducer(done, {
      type: 'fail',
      key: sessionKey(original),
      errorKind: 'permanent',
      message: 'client_id was already used for a different session',
    });

    expect(failed.pending[0].lastErrorKind).toBe('permanent');
    expect(failed.pending[0].lastError).toBe(
      'client_id was already used for a different session',
    );
    expect(failed.pending[0].failedAttempts).toBe(1);
    expect(failed.pending[0].session).toEqual(original);
  });

  it('does not let a permanently rejected head block the rest of the queue', () => {
    const queue = twoPending();
    const failed = failSession(queue, CLIENT_A, 'permanent', 'HTTP 409');

    expect(retryablePending(failed.pending).map((item) => item.session.clientId)).toEqual([
      CLIENT_B,
    ]);
    expect(nextRetryablePending(failed.pending)?.session.clientId).toBe(CLIENT_B);
  });

  it('keeps a transient failure retryable', () => {
    const queue = twoPending();
    const failed = failSession(queue, CLIENT_A, 'transient', 'Network error');

    expect(retryablePending(failed.pending)).toHaveLength(2);
    expect(nextRetryablePending(failed.pending)?.session.clientId).toBe(CLIENT_A);
    expect(failed.pending[0].lastErrorKind).toBe('transient');
  });

  it('skips exactly the session the user bypassed', () => {
    const queue = twoPending();
    const [first, second] = queue.pending;

    expect(
      nextRetryablePending(queue.pending, sessionKey(first.session))?.session.clientId,
    ).toBe(CLIENT_B);
    expect(
      nextRetryablePending(queue.pending, sessionKey(second.session))?.session.clientId,
    ).toBe(CLIENT_A);
  });

  it('discards exactly one pending session and keeps the others in order', () => {
    const queue = twoPending();
    const discarded = timerReducer(queue, {
      type: 'discard',
      key: sessionKey(queue.pending[0].session),
    });

    expect(discarded.pending).toHaveLength(1);
    expect(discarded.pending[0].session.clientId).toBe(CLIENT_B);
    expect(discarded.pending[0].session.startedAt).toBe(T0 + FOCUS_MS + 1_000);
  });

  it('returns to completed after the last failed session is discarded', () => {
    const done = completeFocus();
    const key = sessionKey(done.pending[0].session);
    const failed = timerReducer(done, {
      type: 'fail',
      key,
      errorKind: 'permanent',
      message: 'HTTP 422',
    });

    const discarded = timerReducer(failed, { type: 'discard', key });

    expect(discarded.pending).toHaveLength(0);
    expect(discarded.status).toBe('completed');
    expect(discarded.completedAt).toBe(T0 + FOCUS_MS);
  });

  it('ignores a discard for an unknown session', () => {
    const done = completeFocus();
    expect(timerReducer(done, { type: 'discard', key: 'unknown' })).toBe(done);
  });

  it('never removes another session when the new one is acknowledged', () => {
    const queue = twoPending();
    const failed = failSession(queue, CLIENT_A, 'permanent', 'HTTP 409');
    const saved = timerReducer(failed, {
      type: 'saved',
      key: sessionKey(failed.pending[1].session),
    });

    expect(saved.pending).toHaveLength(1);
    expect(saved.pending[0].session.clientId).toBe(CLIENT_A);
    expect(saved.pending[0].failedAttempts).toBe(1);
    expect(saved.pending[0].lastErrorKind).toBe('permanent');
  });
});

describe('display helpers', () => {
  it('rounds seconds up and never below zero', () => {
    expect(displaySeconds(0)).toBe(0);
    expect(displaySeconds(1)).toBe(1);
    expect(displaySeconds(999)).toBe(1);
    expect(displaySeconds(1_000)).toBe(1);
    expect(displaySeconds(1_001)).toBe(2);
    expect(displaySeconds(1_489_500)).toBe(1490);
    expect(displaySeconds(FOCUS_MS)).toBe(1500);
    expect(displaySeconds(-1)).toBe(0);
  });

  it('formats the clock as mm:ss', () => {
    expect(formatClock(0)).toBe('00:00');
    expect(formatClock(5)).toBe('00:05');
    expect(formatClock(59)).toBe('00:59');
    expect(formatClock(60)).toBe('01:00');
    expect(formatClock(1_499)).toBe('24:59');
    expect(formatClock(1_500)).toBe('25:00');
    expect(formatClock(-3)).toBe('00:00');
  });
});
