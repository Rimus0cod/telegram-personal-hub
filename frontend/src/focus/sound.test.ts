import { describe, expect, it } from 'vitest';
import {
  FOCUS_SOUND_STORAGE_KEY,
  newCompletionKeys,
  playCompletionSound,
  readSoundEnabled,
  writeSoundEnabled,
  type SoundStorage,
} from './sound';
import type { PendingSession } from './timer';

function pending(clientId: string | null, startedAt = 1): PendingSession {
  return {
    session: {
      mode: 'focus',
      durationMinutes: 25,
      startedAt,
      finishedAt: startedAt + 1,
      clientId,
    },
    failedAttempts: 0,
    requiresManualRetry: false,
    lastError: null,
    lastErrorKind: null,
  };
}

function storageWith(initial: Record<string, string> = {}): SoundStorage {
  const data = new Map(Object.entries(initial));
  return {
    getItem: (key) => data.get(key) ?? null,
    setItem: (key, value) => {
      data.set(key, value);
    },
  };
}

describe('focus completion sound', () => {
  it('defaults to enabled when storage is unavailable', () => {
    expect(readSoundEnabled(null)).toBe(true);
  });

  it('persists the user setting', () => {
    const storage = storageWith();
    writeSoundEnabled(false, storage);
    expect(storage.getItem(FOCUS_SOUND_STORAGE_KEY)).toBe('0');
    expect(readSoundEnabled(storage)).toBe(false);
    writeSoundEnabled(true, storage);
    expect(storage.getItem(FOCUS_SOUND_STORAGE_KEY)).toBe('1');
    expect(readSoundEnabled(storage)).toBe(true);
  });

  it('detects a completion only once', () => {
    const known = new Set<string>();
    const first = pending('11111111-1111-4111-8111-111111111111');
    expect(newCompletionKeys(known, [first])).toEqual([
      'client:11111111-1111-4111-8111-111111111111',
    ]);
    for (const key of newCompletionKeys(known, [first])) known.add(key);
    expect(newCompletionKeys(known, [first])).toEqual([]);
  });

  it('ignores completions already known at mount', () => {
    const item = pending('22222222-2222-4222-8222-222222222222');
    const known = new Set(['client:22222222-2222-4222-8222-222222222222']);
    expect(newCompletionKeys(known, [item])).toEqual([]);
  });

  it('returns one key for duplicated pending entries', () => {
    const item = pending(null, 10);
    expect(newCompletionKeys(new Set(), [item, item])).toEqual(['legacy:10:11']);
  });

  it('does not throw when Web Audio is unavailable', () => {
    expect(playCompletionSound()).toBe(false);
  });
});
