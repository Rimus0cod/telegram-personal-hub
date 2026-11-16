/** Small, dependency-free completion signal for the Focus Timer. */

import { sessionKey, type PendingSession } from './timer';

export const FOCUS_SOUND_STORAGE_KEY = 'focus.sound.enabled';

export interface SoundStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

function defaultStorage(): SoundStorage | null {
  try {
    return globalThis.localStorage ?? null;
  } catch {
    return null;
  }
}

export function readSoundEnabled(storage: SoundStorage | null = defaultStorage()): boolean {
  if (!storage) return true;
  try {
    return storage.getItem(FOCUS_SOUND_STORAGE_KEY) !== '0';
  } catch {
    return true;
  }
}

export function writeSoundEnabled(
  enabled: boolean,
  storage: SoundStorage | null = defaultStorage(),
): void {
  if (!storage) return;
  try {
    storage.setItem(FOCUS_SOUND_STORAGE_KEY, enabled ? '1' : '0');
  } catch {
    // A blocked storage must not make the timer unusable.
  }
}

/**
 * Return only completion keys the UI has not seen yet. Callers add the result
 * to their known set, so re-renders and retries cannot play the sound again.
 */
export function newCompletionKeys(
  known: ReadonlySet<string>,
  pending: readonly PendingSession[],
): string[] {
  const seen = new Set(known);
  const fresh: string[] = [];
  for (const item of pending) {
    const key = sessionKey(item.session);
    if (!seen.has(key)) {
      seen.add(key);
      fresh.push(key);
    }
  }
  return fresh;
}

/**
 * Play a short two-tone signal. The Web Audio API is available in modern
 * Telegram WebViews and browsers; if it is missing, suspended, or blocked, the
 * timer keeps working and the completion state remains visible.
 */
export function playCompletionSound(): boolean {
  if (typeof window === 'undefined') return false;
  const AudioCtor =
    window.AudioContext ??
    (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
  if (!AudioCtor) return false;

  try {
    const context = new AudioCtor();
    const play = () => {
      const start = context.currentTime;
      for (const [frequency, offset, duration] of [
        [880, 0, 0.14],
        [660, 0.18, 0.18],
      ] as const) {
        const oscillator = context.createOscillator();
        const gain = context.createGain();
        oscillator.type = 'sine';
        oscillator.frequency.setValueAtTime(frequency, start + offset);
        gain.gain.setValueAtTime(0.0001, start + offset);
        gain.gain.exponentialRampToValueAtTime(0.18, start + offset + 0.015);
        gain.gain.exponentialRampToValueAtTime(0.0001, start + offset + duration);
        oscillator.connect(gain);
        gain.connect(context.destination);
        oscillator.start(start + offset);
        oscillator.stop(start + offset + duration + 0.02);
      }
      window.setTimeout(() => {
        void context.close().catch(() => undefined);
      }, 500);
    };

    if (context.state === 'suspended') {
      void context.resume().then(play).catch(() => context.close().catch(() => undefined));
    } else {
      play();
    }
    return true;
  } catch {
    return false;
  }
}
