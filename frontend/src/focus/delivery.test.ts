import { describe, expect, it } from 'vitest';
import { classifyDeliveryError } from './delivery';

function httpError(status: number, message = 'Request failed'): Error {
  return Object.assign(new Error(message), { status });
}

describe('classifyDeliveryError', () => {
  it('treats a network failure without a status as transient', () => {
    const failure = classifyDeliveryError(new TypeError('Failed to fetch'));

    expect(failure.kind).toBe('transient');
    expect(failure.message).toBe('Failed to fetch');
  });

  it('treats server errors as transient', () => {
    for (const status of [500, 502, 503, 504]) {
      expect(classifyDeliveryError(httpError(status)).kind).toBe('transient');
    }
  });

  it('treats timeout and throttling statuses as transient', () => {
    for (const status of [408, 425, 429]) {
      expect(classifyDeliveryError(httpError(status)).kind).toBe('transient');
    }
  });

  it('treats the conflict and validation statuses as permanent', () => {
    for (const status of [400, 401, 403, 404, 409, 422]) {
      expect(classifyDeliveryError(httpError(status)).kind).toBe('permanent');
    }
  });

  it('keeps the server-provided message', () => {
    const failure = classifyDeliveryError(
      httpError(409, 'client_id was already used for a different session'),
    );

    expect(failure.kind).toBe('permanent');
    expect(failure.message).toBe(
      'client_id was already used for a different session',
    );
  });

  it('falls back to a readable message for unknown error values', () => {
    expect(classifyDeliveryError(null).message).toBe('Не удалось сохранить сессию');
    expect(classifyDeliveryError('boom').kind).toBe('transient');

    const blank = new Error('   ');
    expect(classifyDeliveryError(blank).message).toBe('Не удалось сохранить сессию');
  });

  it('does not treat a non-numeric status as an HTTP response', () => {
    const error = Object.assign(new Error('weird'), { status: '500' });

    expect(classifyDeliveryError(error).kind).toBe('transient');
  });

  it('does not mark an unexpected non-4xx status as permanent', () => {
    expect(classifyDeliveryError(httpError(302)).kind).toBe('transient');
  });
});
