/** Remember the last weather selection across Mini App reloads. */

export const WEATHER_LOCATION_STORAGE_KEY = 'weather.location.v1';

export type WeatherLocation =
  | { mode: 'auto'; latitude: number; longitude: number; city?: string }
  | { mode: 'manual'; city: string; latitude?: number; longitude?: number };

export interface WeatherStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

function defaultStorage(): WeatherStorage | null {
  try {
    return globalThis.localStorage ?? null;
  } catch {
    return null;
  }
}

function validCoordinates(latitude: unknown, longitude: unknown): boolean {
  return (
    typeof latitude === 'number' &&
    typeof longitude === 'number' &&
    Number.isFinite(latitude) &&
    Number.isFinite(longitude) &&
    latitude >= -90 &&
    latitude <= 90 &&
    longitude >= -180 &&
    longitude <= 180
  );
}

export function loadWeatherLocation(
  storage: WeatherStorage | null = defaultStorage(),
): WeatherLocation | null {
  if (!storage) return null;
  try {
    const raw = storage.getItem(WEATHER_LOCATION_STORAGE_KEY);
    if (!raw) return null;
    const value: unknown = JSON.parse(raw);
    if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
    const record = value as Record<string, unknown>;
    if (record.mode === 'auto') {
      if (!validCoordinates(record.latitude, record.longitude)) return null;
      return {
        mode: 'auto',
        latitude: record.latitude as number,
        longitude: record.longitude as number,
        ...(typeof record.city === 'string' && record.city.trim()
          ? { city: record.city.trim() }
          : {}),
      };
    }
    if (record.mode === 'manual' && typeof record.city === 'string' && record.city.trim()) {
      return {
        mode: 'manual',
        city: record.city.trim(),
        ...(validCoordinates(record.latitude, record.longitude)
          ? { latitude: record.latitude as number, longitude: record.longitude as number }
          : {}),
      };
    }
    return null;
  } catch {
    return null;
  }
}

export function saveWeatherLocation(
  location: WeatherLocation,
  storage: WeatherStorage | null = defaultStorage(),
): void {
  if (!storage) return;
  try {
    storage.setItem(WEATHER_LOCATION_STORAGE_KEY, JSON.stringify(location));
  } catch {
    // The weather screen still works if storage is blocked.
  }
}
