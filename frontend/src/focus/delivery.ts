import type { DeliveryErrorKind } from './timer';

export interface DeliveryFailure {
  kind: DeliveryErrorKind;
  message: string;
}

/**
 * HTTP statuses that are worth retrying even though they are client errors.
 * Everything else in the 4xx range means the payload itself is rejected and
 * resending it unchanged cannot succeed.
 */
const TRANSIENT_STATUSES = new Set([408, 425, 429]);

/**
 * Splits a failed POST into the two categories the queue needs:
 *
 * - ``transient``: network failure, timeout, 5xx (and 408/425/429).
 * - ``permanent``: 4xx such as 409/422; retrying the same payload will not help.
 *
 * The app's ``request`` helper attaches ``status`` to its errors, so no network
 * details are needed here.
 */
export function classifyDeliveryError(error: unknown): DeliveryFailure {
  const message =
    error instanceof Error && error.message.trim()
      ? error.message.trim()
      : 'Не удалось сохранить сессию';

  const status =
    typeof error === 'object' &&
    error !== null &&
    typeof (error as { status?: unknown }).status === 'number'
      ? (error as { status: number }).status
      : null;

  if (status === null) return { kind: 'transient', message };
  if (status >= 500 || TRANSIENT_STATUSES.has(status)) {
    return { kind: 'transient', message };
  }
  if (status >= 400) return { kind: 'permanent', message };
  return { kind: 'transient', message };
}
