export type FocusMode = 'focus' | 'short_break' | 'long_break';

export interface FocusModeConfig {
  id: FocusMode;
  label: string;
  minutes: number;
}

/** Single source of truth for timer lengths. */
export const FOCUS_MODES: readonly FocusModeConfig[] = [
  { id: 'focus', label: 'Focus', minutes: 25 },
  { id: 'short_break', label: 'Short break', minutes: 5 },
  { id: 'long_break', label: 'Long break', minutes: 15 },
];

export const DEFAULT_MODE: FocusMode = 'focus';

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
