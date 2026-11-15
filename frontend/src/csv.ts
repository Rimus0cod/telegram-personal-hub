/**
 * CSV helpers for the nutrition export.
 *
 * Spreadsheet applications treat a cell starting with ``=``, ``+``, ``-`` or
 * ``@`` as a formula, even when the file was generated from trusted data. A
 * leading apostrophe forces the value to be interpreted as text; Excel,
 * LibreOffice and Google Sheets hide that marker in the rendered cell.
 *
 * Only textual values are prefixed: a real number like ``-5`` must stay a
 * number. Leading whitespace and control characters are ignored when looking
 * for a dangerous prefix, because some editors trim them before evaluation.
 */

const DANGEROUS_PREFIX = /^[\s\u0000-\u001F\u200B-\u200D\u2060\uFEFF]*[=+\-@]/;

export function escapeCsvCell(value: unknown): string {
  let text = value == null ? '' : String(value);
  if (typeof value === 'string' && DANGEROUS_PREFIX.test(text)) {
    text = `'${text}`;
  }
  return `"${text.replaceAll('"', '""')}"`;
}

/** Builds a UTF-8 CSV with the existing semicolon separator and CRLF rows. */
export function buildCsv(rows: readonly (readonly unknown[])[]): string {
  return `\uFEFF${rows.map((row) => row.map(escapeCsvCell).join(';')).join('\r\n')}`;
}
