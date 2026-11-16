import { describe, expect, it } from 'vitest';
import {
  MAX_CUSTOM_MINUTES,
  MIN_CUSTOM_MINUTES,
  durationMsFromMinutes,
  isValidDurationMinutes,
} from './modes';
import { createTimerState, timerReducer } from './timer';

const T0 = 1_700_000_000_000;
const CUSTOM_MS = 40 * 60_000;

describe('custom focus duration', () => {
  it('validates the supported range', () => {
    expect(isValidDurationMinutes(MIN_CUSTOM_MINUTES)).toBe(true);
    expect(isValidDurationMinutes(MAX_CUSTOM_MINUTES)).toBe(true);
    expect(isValidDurationMinutes(MIN_CUSTOM_MINUTES - 1)).toBe(false);
    expect(isValidDurationMinutes(MAX_CUSTOM_MINUTES + 1)).toBe(false);
    expect(isValidDurationMinutes(12.5)).toBe(false);
  });

  it('creates an idle timer with a custom duration', () => {
    const state = createTimerState('focus', CUSTOM_MS);
    expect(state.durationMs).toBe(CUSTOM_MS);
    expect(state.remainingMs).toBe(CUSTOM_MS);
    expect(state.status).toBe('idle');
  });

  it('changes duration only before a session starts', () => {
    const idle = createTimerState('focus');
    const changed = timerReducer(idle, {
      type: 'set_duration',
      durationMs: CUSTOM_MS,
      now: T0,
    });
    expect(changed.durationMs).toBe(CUSTOM_MS);
    expect(changed.remainingMs).toBe(CUSTOM_MS);

    const running = timerReducer(changed, { type: 'start', now: T0 });
    const ignored = timerReducer(running, {
      type: 'set_duration',
      durationMs: durationMsFromMinutes(10),
      now: T0 + 1_000,
    });
    expect(ignored).toBe(running);
  });

  it('records a completed session with the custom duration', () => {
    const started = timerReducer(createTimerState('focus', CUSTOM_MS), {
      type: 'start',
      now: T0,
    });
    const completed = timerReducer(started, { type: 'tick', now: T0 + CUSTOM_MS });
    expect(completed.pending[0]?.session.durationMinutes).toBe(40);
  });

  it('returns to the standard duration when the mode changes', () => {
    const changed = timerReducer(createTimerState('focus'), {
      type: 'set_duration',
      durationMs: CUSTOM_MS,
      now: T0,
    });
    const switched = timerReducer(changed, {
      type: 'set_mode',
      mode: 'short_break',
      now: T0 + 1_000,
    });
    expect(switched.durationMs).toBe(5 * 60_000);
  });

  it('keeps unsaved sessions when changing duration', () => {
    const started = timerReducer(createTimerState('focus'), { type: 'start', now: T0 });
    const completed = timerReducer(started, { type: 'tick', now: T0 + 25 * 60_000 });
    const changed = timerReducer(completed, {
      type: 'set_duration',
      durationMs: CUSTOM_MS,
      now: T0 + 25 * 60_000 + 1_000,
    });
    expect(changed.pending).toHaveLength(1);
    expect(changed.durationMs).toBe(CUSTOM_MS);
  });
});
