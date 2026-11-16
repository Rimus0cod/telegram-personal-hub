import { describe, expect, it } from 'vitest';
import {
  WEATHER_LOCATION_STORAGE_KEY,
  loadWeatherLocation,
  saveWeatherLocation,
  type WeatherStorage,
} from './weather-location';

function storageWith(initial: Record<string, string> = {}): WeatherStorage {
  const data = new Map(Object.entries(initial));
  return {
    getItem: (key) => data.get(key) ?? null,
    setItem: (key, value) => {
      data.set(key, value);
    },
  };
}

describe('weather location persistence', () => {
  it('round-trips an automatically detected location', () => {
    const storage = storageWith();
    saveWeatherLocation(
      { mode: 'auto', latitude: 50.45, longitude: 30.52, city: 'Test City' },
      storage,
    );
    expect(loadWeatherLocation(storage)).toEqual({
      mode: 'auto',
      latitude: 50.45,
      longitude: 30.52,
      city: 'Test City',
    });
  });

  it('round-trips a manually selected city', () => {
    const storage = storageWith();
    saveWeatherLocation(
      { mode: 'manual', city: 'London', latitude: 51.5, longitude: -0.12 },
      storage,
    );
    expect(loadWeatherLocation(storage)?.mode).toBe('manual');
    expect(loadWeatherLocation(storage)?.city).toBe('London');
  });

  it('rejects malformed or out-of-range data', () => {
    expect(
      loadWeatherLocation(
        storageWith({
          [WEATHER_LOCATION_STORAGE_KEY]: JSON.stringify({
            mode: 'auto',
            latitude: 95,
            longitude: 30,
          }),
        }),
      ),
    ).toBeNull();
    expect(
      loadWeatherLocation(
        storageWith({
          [WEATHER_LOCATION_STORAGE_KEY]: JSON.stringify({ mode: 'manual', city: '' }),
        }),
      ),
    ).toBeNull();
    expect(
      loadWeatherLocation(storageWith({ [WEATHER_LOCATION_STORAGE_KEY]: '{bad json' })),
    ).toBeNull();
  });

  it('does not throw when storage is unavailable', () => {
    expect(loadWeatherLocation(null)).toBeNull();
    expect(() =>
      saveWeatherLocation({ mode: 'manual', city: 'London' }, null),
    ).not.toThrow();
  });
});
