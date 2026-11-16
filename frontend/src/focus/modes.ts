export type FocusMode = 'focus' | 'short_break' | 'long_break';

export interface FocusModeConfig {
  id: FocusMode;
  label: string;
  minutes: number;
}

/** Single source of truth for timer lengths. */
export const FOCUS_MODES: readonly FocusModeConfig[] = [
  { id: 'focus', label: 'Фокус', minutes: 25 },
  { id: 'short_break', label: 'Короткий перерыв', minutes: 5 },
  { id: 'long_break', label: 'Длинный перерыв', minutes: 15 },
];

export const DEFAULT_MODE: FocusMode = 'focus';

export const MIN_CUSTOM_MINUTES = 1;
export const MAX_CUSTOM_MINUTES = 180;

export function isValidDurationMinutes(minutes: number): boolean {
  return Number.isInteger(minutes) && minutes >= MIN_CUSTOM_MINUTES && minutes <= MAX_CUSTOM_MINUTES;
}

export function durationMsFromMinutes(minutes: number): number {
  return minutes * 60_000;
}

const MODE_BY_ID = new Map<FocusMode, FocusModeConfig>(
  FOCUS_MODES.map((mode) => [mode.id, mode]),
);

export function getMode(id: FocusMode): FocusModeConfig {
  const mode = MODE_BY_ID.get(id);
  if (!mode) throw new Error(`Unknown focus mode: ${id}`);
  return mode;
}

export function modeDurationMs(id: FocusMode): number {
  return getMode(id).minutes * 60_000;
}

export function modeLabel(id: FocusMode): string {
  return getMode(id).label;
}
