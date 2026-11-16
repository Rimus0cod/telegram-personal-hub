import { describe, expect, it } from 'vitest';
import {
  FOCUS_STORAGE_KEY,
  loadTimerState,
  parseTimerState,
  persistenceSignature,
  saveTimerState,
  serializeTimerState,
  type LoadResult,
  type RestoredInfo,
  type StorageLike,
} from './persistence';
import {
  createTimerState,
  isAutoDeliverable,
  retryablePending,
  sessionKey,
  timerReducer,
  type PendingSession,
  type TimerState,
} from './timer';

const T0 = 1_700_000_000_000;
const FOCUS_MS = 25 * 60_000;

function startFocus(now = T0): TimerState {
  return timerReducer(createTimerState('focus'), { type: 'start', now });
}

function completeFocus(now = T0): TimerState {
  return timerReducer(startFocus(now), { type: 'tick', now: now + FOCUS_MS });
}

function twoPending(): TimerState {
  const first = completeFocus();
  const secondStart = T0 + FOCUS_MS + 1_000;
  return timerReducer(
    timerReducer(first, { type: 'start', now: secondStart }),
    { type: 'tick', now: secondStart + FOCUS_MS },
  );
}

function expectRestored(result: LoadResult): RestoredInfo {
  if (result.kind !== 'restored') {
    throw new Error(`expected restored, got ${result.kind}`);
  }
  return result;
}

function expectError(result: LoadResult): string {
  if (result.kind !== 'error') {
    throw new Error(`expected error, got ${result.kind}`);
  }
  expect(result.message.length).toBeGreaterThan(0);
  return result.message;
}

function persistedOf(state: TimerState, savedAt = T0): Record<string, unknown> {
  return JSON.parse(serializeTimerState(state, savedAt)) as Record<string, unknown>;
}

class MemoryStorage implements StorageLike {
  private data = new Map<string, string>();

  getItem(key: string): string | null {
    return this.data.has(key) ? (this.data.get(key) as string) : null;
  }

  setItem(key: string, value: string): void {
    this.data.set(key, value);
  }

  removeItem(key: string): void {
    this.data.delete(key);
  }
}

describe('serialization', () => {
  it('saves a running timer with its absolute deadline', () => {
    const state = startFocus();
    const stored = persistedOf(state, T0 + 2_000);

    expect(stored).toMatchObject({
      version: 3,
      mode: 'focus',
      status: 'running',
      remainingMs: FOCUS_MS - 2_000,
      deadlineAt: T0 + FOCUS_MS,
      startedAt: T0,
      completedAt: null,
      sessionClientId: null,
      pending: [],
    });
  });

  it('saves a paused timer with its frozen remaining time', () => {
    const paused = timerReducer(startFocus(), { type: 'pause', now: T0 + 10_000 });
    const stored = persistedOf(paused, T0 + 60_000);

    expect(stored).toMatchObject({
      status: 'paused',
      remainingMs: FOCUS_MS - 10_000,
      deadlineAt: null,
      startedAt: T0,
    });
  });

  it('saves the pending queue with payloads and failure counters', () => {
    const withTwo = twoPending();
    const failed = timerReducer(withTwo, {
      type: 'fail',
      key: sessionKey(withTwo.pending[0].session),
    });
    const stored = persistedOf(failed, T0 + 2 * FOCUS_MS);

    const pending = stored.pending as Array<Record<string, unknown>>;
    expect(pending).toHaveLength(2);
    expect(pending[0]).toMatchObject({
      mode: 'focus',
      durationMinutes: 25,
      startedAt: T0,
      finishedAt: T0 + FOCUS_MS,
      failedAttempts: 1,
      requiresManualRetry: false,
      clientId: null,
    });
    expect(pending[1]).toMatchObject({
      startedAt: T0 + FOCUS_MS + 1_000,
      finishedAt: T0 + 2 * FOCUS_MS + 1_000,
      failedAttempts: 0,
    });
  });

  it('does not change the signature for display ticks while running', () => {
    const running = startFocus();
    const ticked = timerReducer(running, { type: 'tick', now: T0 + 3_000 });

    expect(persistenceSignature(ticked)).toBe(persistenceSignature(running));
  });

  it('changes the signature on pause, completion and pending changes', () => {
    const running = startFocus();
    const paused = timerReducer(running, { type: 'pause', now: T0 + 2_000 });
    const done = completeFocus();

    expect(persistenceSignature(paused)).not.toBe(persistenceSignature(running));
    expect(persistenceSignature(done)).not.toBe(persistenceSignature(running));
    expect(persistenceSignature(done)).not.toBe(
      persistenceSignature(timerReducer(done, { type: 'reset', now: T0 })),
    );
  });
});

describe('restoring a timer', () => {
  it('restores a running timer from its deadline', () => {
    const raw = serializeTimerState(startFocus(), T0);
    const restored = expectRestored(parseTimerState(raw, T0 + 60_000));

    expect(restored.timerRestored).toBe(true);
    expect(restored.expiredCompletion).toBe(false);
    expect(restored.state.status).toBe('running');
    expect(restored.state.deadlineAt).toBe(T0 + FOCUS_MS);
    expect(restored.state.startedAt).toBe(T0);
    expect(restored.state.remainingMs).toBe(FOCUS_MS - 60_000);
    expect(restored.state.completedAt).toBeNull();
  });

  it('restores a paused timer with the frozen remainder', () => {
    const paused = timerReducer(startFocus(), { type: 'pause', now: T0 + 10_000 });
    const restored = expectRestored(
      parseTimerState(serializeTimerState(paused, T0 + 10_500), T0 + 9_000_000),
    );

    expect(restored.timerRestored).toBe(true);
    expect(restored.state.status).toBe('paused');
    expect(restored.state.remainingMs).toBe(FOCUS_MS - 10_000);
    expect(restored.state.deadlineAt).toBeNull();
    expect(restored.state.startedAt).toBe(T0);
  });

  it('completes a running timer whose deadline already passed', () => {
    const raw = serializeTimerState(startFocus(), T0);
    const restored = expectRestored(parseTimerState(raw, T0 + FOCUS_MS + 5_000));

    expect(restored.expiredCompletion).toBe(true);
    expect(restored.state.status).toBe('completed');
    expect(restored.state.remainingMs).toBe(0);
    expect(restored.state.completedAt).toBe(T0 + FOCUS_MS);
    expect(restored.state.pending).toHaveLength(1);

    const pending = restored.state.pending[0];
    expect(pending.session).toEqual({
      mode: 'focus',
      durationMinutes: 25,
      startedAt: T0,
      finishedAt: T0 + FOCUS_MS,
      clientId: expect.any(String),
    });
    // A session that finished while the app was closed was never sent.
    expect(pending.requiresManualRetry).toBe(false);
    expect(isAutoDeliverable(pending)).toBe(true);
  });

  it('restores several pending sessions in FIFO order', () => {
    const withTwo = twoPending();
    const failed = timerReducer(withTwo, {
      type: 'fail',
      key: sessionKey(withTwo.pending[0].session),
    });
    const restored = expectRestored(
      parseTimerState(serializeTimerState(failed, T0 + 2 * FOCUS_MS), T0 + 2 * FOCUS_MS + 1_000),
    );

    expect(restored.restoredPending).toBe(2);
    expect(restored.manualRetryRequired).toBe(true);
    expect(restored.state.pending.map((item) => item.session.finishedAt)).toEqual([
      T0 + FOCUS_MS,
      T0 + 2 * FOCUS_MS + 1_000,
    ]);
    expect(restored.state.pending[0].failedAttempts).toBe(1);
    expect(restored.state.pending.every((item) => item.requiresManualRetry)).toBe(true);
  });

  it('keeps pending sessions when the timer was reset before reload', () => {
    const done = completeFocus();
    const reset = timerReducer(done, { type: 'reset', now: T0 + FOCUS_MS + 1_000 });
    const restored = expectRestored(
      parseTimerState(serializeTimerState(reset, T0 + FOCUS_MS + 2_000), T0 + FOCUS_MS + 3_000),
    );

    expect(restored.timerRestored).toBe(false);
    expect(restored.state.status).toBe('idle');
    expect(restored.state.remainingMs).toBe(FOCUS_MS);
    expect(restored.state.pending).toHaveLength(1);
    expect(restored.restoredPending).toBe(1);
  });

  it('restores a completed timer without a pending session', () => {
    const done = completeFocus();
    const saved = timerReducer(done, {
      type: 'saved',
      key: sessionKey(done.pending[0].session),
    });
    const restored = expectRestored(parseTimerState(serializeTimerState(saved, T0 + FOCUS_MS + 1), T0 + FOCUS_MS + 2));

    expect(restored.state.status).toBe('completed');
    expect(restored.state.completedAt).toBe(T0 + FOCUS_MS);
    expect(restored.state.pending).toHaveLength(0);
    expect(restored.restoredPending).toBe(0);
    expect(restored.timerRestored).toBe(false);
  });
});

describe('safe failure handling', () => {
  it('reports corrupted JSON without throwing', () => {
    expect(expectError(parseTimerState('{not json', T0))).toContain('повреждены');
    expectError(parseTimerState('null', T0));
    expectError(parseTimerState('[]', T0));
  });

  it('rejects an unknown schema version', () => {
    const stored = persistedOf(startFocus());
    stored.version = 99;
    expect(expectError(parseTimerState(JSON.stringify(stored), T0))).toContain('версия');
  });

  it('rejects an invalid mode', () => {
    const stored = persistedOf(startFocus());
    stored.mode = 'pomodoro';
    expect(expectError(parseTimerState(JSON.stringify(stored), T0))).toContain('режим');
  });

  it('rejects invalid timestamps and status-specific gaps', () => {
    const negative = persistedOf(startFocus());
    negative.startedAt = -5;
    expectError(parseTimerState(JSON.stringify(negative), T0));

    const runningWithoutDeadline = persistedOf(startFocus());
    runningWithoutDeadline.deadlineAt = null;
    expectError(parseTimerState(JSON.stringify(runningWithoutDeadline), T0));

    const completed = persistedOf(completeFocus());
    completed.startedAt = T0 + FOCUS_MS + 1_000;
    expectError(parseTimerState(JSON.stringify(completed), T0 + FOCUS_MS));

    const done = completeFocus();
    const badPending = persistedOf(done);
    const pending = badPending.pending as Array<Record<string, unknown>>;
    pending[0].finishedAt = (pending[0].startedAt as number) - 1;
    expectError(parseTimerState(JSON.stringify(badPending), T0 + FOCUS_MS));
  });

  it('rejects malformed pending sessions', () => {
    const done = completeFocus();
    const stored = persistedOf(done);
    const pending = stored.pending as Array<Record<string, unknown>>;
    pending[0].durationMinutes = 0;
    expectError(parseTimerState(JSON.stringify(stored), T0 + FOCUS_MS));

    const wrongTypes = persistedOf(done);
    const wrongPending = wrongTypes.pending as Array<Record<string, unknown>>;
    wrongPending[0].failedAttempts = 'many';
    expectError(parseTimerState(JSON.stringify(wrongTypes), T0 + FOCUS_MS));
  });
});

describe('storage access', () => {
  it('handles a missing localStorage gracefully', () => {
    expect(loadTimerState(T0, null)).toEqual({
      kind: 'unavailable',
      message: 'Локальное хранилище недоступно.',
    });
    expect(saveTimerState(startFocus(), null, T0)).toEqual({
      ok: false,
      message: 'Локальное хранилище недоступно.',
    });
  });

  it('handles read and write errors gracefully', () => {
    const brokenRead: StorageLike = {
      getItem: () => {
        throw new Error('denied');
      },
      setItem: () => {},
    };
    const brokenWrite: StorageLike = {
      getItem: () => null,
      setItem: () => {
        throw new Error('quota');
      },
    };

    expect(loadTimerState(T0, brokenRead).kind).toBe('error');
    const save = saveTimerState(startFocus(), brokenWrite, T0);
    expect(save.ok).toBe(false);
    if (!save.ok) expect(save.message.length).toBeGreaterThan(0);
  });

  it('returns empty when nothing is stored', () => {
    expect(loadTimerState(T0, new MemoryStorage())).toEqual({ kind: 'empty' });
  });
});

describe('duplicate safety after reload', () => {
  it('never auto-sends a pending session restored from storage', () => {
    const done = completeFocus();
    const original = done.pending[0];

    // The page may have closed while this exact POST was in flight.
    const restored = expectRestored(
      parseTimerState(serializeTimerState(done, T0 + FOCUS_MS + 500), T0 + FOCUS_MS + 1_000),
    );
    const pending: PendingSession | undefined = restored.state.pending[0];

    expect(pending).toBeDefined();
    if (!pending) throw new Error('pending session missing');
    expect(pending.requiresManualRetry).toBe(true);
    expect(isAutoDeliverable(pending)).toBe(false);
    // Payload must stay byte-for-byte identical.
    expect(pending.session).toEqual(original.session);
    expect(restored.manualRetryRequired).toBe(true);
  });

  it('persists a successful save and removes the pending session', () => {
    const storage = new MemoryStorage();
    const done = completeFocus();
    const key = sessionKey(done.pending[0].session);

    expect(saveTimerState(done, storage, T0 + FOCUS_MS).ok).toBe(true);
    const before = expectRestored(loadTimerState(T0 + FOCUS_MS + 1_000, storage));
    expect(before.state.pending).toHaveLength(1);
    expect(before.state.pending[0].requiresManualRetry).toBe(true);

    const savedState = timerReducer(before.state, { type: 'saved', key });
    expect(saveTimerState(savedState, storage, T0 + FOCUS_MS + 2_000).ok).toBe(true);

    const after = expectRestored(loadTimerState(T0 + FOCUS_MS + 3_000, storage));
    expect(after.state.pending).toHaveLength(0);
    expect(after.state.status).toBe('completed');
    expect(JSON.parse(storage.getItem(FOCUS_STORAGE_KEY) as string).pending).toEqual([]);
  });

  it('persists a failed retry without touching the payload', () => {
    const storage = new MemoryStorage();
    const done = completeFocus();
    saveTimerState(done, storage, T0 + FOCUS_MS);

    const reloaded = expectRestored(loadTimerState(T0 + FOCUS_MS + 1_000, storage));
    const original = reloaded.state.pending[0].session;
    const failed = timerReducer(reloaded.state, {
      type: 'fail',
      key: sessionKey(original),
    });
    saveTimerState(failed, storage, T0 + FOCUS_MS + 2_000);

    const after = expectRestored(loadTimerState(T0 + FOCUS_MS + 3_000, storage));
    expect(after.state.pending[0].failedAttempts).toBe(1);
    expect(after.state.pending[0].requiresManualRetry).toBe(true);
    expect(after.state.pending[0].session).toEqual(original);
  });
});

describe('delivery failure persistence', () => {
  it('stores and restores the last error of a pending session', () => {
    const done = completeFocus();
    const failed = timerReducer(done, {
      type: 'fail',
      key: sessionKey(done.pending[0].session),
      errorKind: 'permanent',
      message: 'client_id was already used for a different session',
    });

    const stored = persistedOf(failed, T0 + FOCUS_MS);
    const pending = (stored.pending as Array<Record<string, unknown>>)[0];
    expect(pending).toMatchObject({
      lastError: 'client_id was already used for a different session',
      lastErrorKind: 'permanent',
      failedAttempts: 1,
    });

    const restored = expectRestored(
      parseTimerState(JSON.stringify(stored), T0 + FOCUS_MS + 1_000),
    );
    expect(restored.state.pending[0].lastError).toBe(
      'client_id was already used for a different session',
    );
    expect(restored.state.pending[0].lastErrorKind).toBe('permanent');
  });

  it('keeps a permanent rejection out of "retry all" after a reload', () => {
    const done = completeFocus();
    const failed = timerReducer(done, {
      type: 'fail',
      key: sessionKey(done.pending[0].session),
      errorKind: 'permanent',
      message: 'HTTP 409',
    });
    const restored = expectRestored(
      parseTimerState(
        serializeTimerState(failed, T0 + FOCUS_MS),
        T0 + FOCUS_MS + 1_000,
      ),
    );

    expect(retryablePending(restored.state.pending)).toHaveLength(0);
    expect(restored.state.pending[0].failedAttempts).toBe(1);
  });

  it('restores a transient failure as retryable', () => {
    const done = completeFocus();
    const failed = timerReducer(done, {
      type: 'fail',
      key: sessionKey(done.pending[0].session),
      errorKind: 'transient',
      message: 'Failed to fetch',
    });
    const restored = expectRestored(
      parseTimerState(
        serializeTimerState(failed, T0 + FOCUS_MS),
        T0 + FOCUS_MS + 1_000,
      ),
    );

    expect(restored.state.pending[0].lastErrorKind).toBe('transient');
    expect(retryablePending(restored.state.pending)).toHaveLength(1);
    // Still manual-only after a reload, regardless of the error category.
    expect(restored.state.pending[0].requiresManualRetry).toBe(true);
  });

  it('treats an unknown stored error kind as absent metadata', () => {
    const done = completeFocus();
    const failed = timerReducer(done, {
      type: 'fail',
      key: sessionKey(done.pending[0].session),
      errorKind: 'transient',
      message: 'timeout',
    });
    const stored = JSON.parse(serializeTimerState(failed, T0 + FOCUS_MS));
    stored.pending[0].lastErrorKind = 'weird';
    stored.pending[0].lastError = 42;

    const restored = expectRestored(
      parseTimerState(JSON.stringify(stored), T0 + FOCUS_MS + 1_000),
    );

    expect(restored.state.pending[0].lastErrorKind).toBeNull();
    expect(restored.state.pending[0].lastError).toBeNull();
    expect(restored.state.pending[0].session).toEqual(failed.pending[0].session);
  });

  it('defaults the error fields for legacy pending records', () => {
    const legacy = {
      version: 1,
      savedAt: T0 + FOCUS_MS,
      mode: 'focus',
      status: 'completed',
      remainingMs: 0,
      deadlineAt: null,
      startedAt: T0,
      completedAt: T0 + FOCUS_MS,
      pending: [
        {
          mode: 'focus',
          durationMinutes: 25,
          startedAt: T0,
          finishedAt: T0 + FOCUS_MS,
          failedAttempts: 0,
          requiresManualRetry: false,
        },
      ],
    };

    const restored = expectRestored(
      parseTimerState(JSON.stringify(legacy), T0 + FOCUS_MS + 1_000),
    );

    expect(restored.state.pending[0].lastError).toBeNull();
    expect(restored.state.pending[0].lastErrorKind).toBeNull();
    expect(retryablePending(restored.state.pending)).toHaveLength(1);
  });
});

describe('client id persistence', () => {
  const CLIENT_A = '11111111-1111-4111-8111-111111111111';
  const CLIENT_B = '22222222-2222-4222-8222-222222222222';
  const UUID_PATTERN =
    /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

  function startWithId(clientId: string, now = T0): TimerState {
    return timerReducer(createTimerState('focus'), {
      type: 'start',
      now,
      sessionId: clientId,
    });
  }

  function completeWithId(clientId: string, now = T0): TimerState {
    return timerReducer(startWithId(clientId, now), {
      type: 'tick',
      now: now + FOCUS_MS,
    });
  }

  it('stores the active UUID and restores it unchanged', () => {
    const raw = serializeTimerState(startWithId(CLIENT_A), T0);

    expect(JSON.parse(raw).sessionClientId).toBe(CLIENT_A);
    const restored = expectRestored(parseTimerState(raw, T0 + 1_000));
    expect(restored.state.sessionClientId).toBe(CLIENT_A);
  });

  it('keeps the pending UUID stable across retries and reloads', () => {
    const done = completeWithId(CLIENT_A);
    const raw = serializeTimerState(done, T0 + FOCUS_MS);

    expect(JSON.parse(raw).pending[0].clientId).toBe(CLIENT_A);

    const first = expectRestored(parseTimerState(raw, T0 + FOCUS_MS + 1_000));
    const second = expectRestored(parseTimerState(raw, T0 + FOCUS_MS + 2_000));

    expect(first.state.pending[0].session.clientId).toBe(CLIENT_A);
    expect(second.state.pending[0].session.clientId).toBe(CLIENT_A);
    expect(first.state.pending[0].requiresManualRetry).toBe(true);
    expect(second.state.pending[0].requiresManualRetry).toBe(true);
  });

  it('does not give legacy v1 pending sessions a new UUID', () => {
    const legacy = {
      version: 1,
      savedAt: T0 + FOCUS_MS,
      mode: 'focus',
      status: 'completed',
      remainingMs: 0,
      deadlineAt: null,
      startedAt: T0,
      completedAt: T0 + FOCUS_MS,
      pending: [
        {
          mode: 'focus',
          durationMinutes: 25,
          startedAt: T0,
          finishedAt: T0 + FOCUS_MS,
          failedAttempts: 0,
          requiresManualRetry: false,
        },
      ],
    };

    const restored = expectRestored(
      parseTimerState(JSON.stringify(legacy), T0 + FOCUS_MS + 1_000),
    );
    const pending = restored.state.pending[0];

    expect(pending.session.clientId).toBeNull();
    expect(pending.requiresManualRetry).toBe(true);
    expect(isAutoDeliverable(pending)).toBe(false);

    // Saving the restored state upgrades the record to v3, still without
    // a key.
    const migrated = JSON.parse(serializeTimerState(restored.state, T0 + FOCUS_MS + 2_000));
    expect(migrated.version).toBe(3);
    expect(migrated.pending[0].clientId).toBeNull();
  });

  it('gives a legacy running timer one UUID for its future completion', () => {
    const legacy = {
      version: 1,
      savedAt: T0,
      mode: 'focus',
      status: 'running',
      remainingMs: FOCUS_MS,
      deadlineAt: T0 + FOCUS_MS,
      startedAt: T0,
      completedAt: null,
      pending: [],
    };

    const restored = expectRestored(parseTimerState(JSON.stringify(legacy), T0 + 1_000));
    const clientId = restored.state.sessionClientId;

    expect(clientId).toMatch(UUID_PATTERN);
    const done = timerReducer(restored.state, {
      type: 'tick',
      now: T0 + FOCUS_MS,
    });
    expect(done.pending[0].session.clientId).toBe(clientId);
  });

  it('drops a malformed stored UUID and keeps the session manual', () => {
    const stored = JSON.parse(
      serializeTimerState(completeWithId(CLIENT_A), T0 + FOCUS_MS),
    );
    stored.pending[0].clientId = 'not-a-uuid';

    const restored = expectRestored(
      parseTimerState(JSON.stringify(stored), T0 + FOCUS_MS + 1_000),
    );

    expect(restored.state.pending[0].session.clientId).toBeNull();
    expect(restored.state.pending[0].requiresManualRetry).toBe(true);
  });

  it('removes only the acknowledged session when IDs are present', () => {
    const first = completeWithId(CLIENT_A);
    const secondStart = T0 + FOCUS_MS + 1_000;
    const second = timerReducer(
      timerReducer(first, { type: 'start', now: secondStart, sessionId: CLIENT_B }),
      { type: 'tick', now: secondStart + FOCUS_MS },
    );

    const saved = timerReducer(second, {
      type: 'saved',
      key: sessionKey(second.pending[0].session),
    });

    expect(saved.pending).toHaveLength(1);
    expect(saved.pending[0].session.clientId).toBe(CLIENT_B);
  });
});

describe('custom duration persistence', () => {
  it('restores a v3 custom duration', () => {
    const raw = serializeTimerState(createTimerState('focus', 40 * 60_000), T0);
    const restored = expectRestored(parseTimerState(raw, T0 + 1_000));
    expect(restored.state.durationMs).toBe(40 * 60_000);
    expect(restored.state.remainingMs).toBe(40 * 60_000);
  });

  it('keeps v2 records readable with the standard mode duration', () => {
    const stored = persistedOf(startFocus());
    stored.version = 2;
    delete stored.durationMs;
    const restored = expectRestored(parseTimerState(JSON.stringify(stored), T0 + 1_000));
    expect(restored.state.durationMs).toBe(25 * 60_000);
  });

  it('rejects an invalid duration in a v3 record', () => {
    const stored = persistedOf(createTimerState('focus', 40 * 60_000));
    stored.durationMs = 12.5;
    expectError(parseTimerState(JSON.stringify(stored), T0));
    stored.durationMs = 181 * 60_000;
    expectError(parseTimerState(JSON.stringify(stored), T0));
  });
});
