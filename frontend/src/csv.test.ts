import { describe, expect, it } from 'vitest';
import { buildCsv, escapeCsvCell } from './csv';

describe('escapeCsvCell', () => {
  it('neutralizes formula prefixes', () => {
    expect(escapeCsvCell('=HYPERLINK("http://evil.example")')).toBe(
      '"\'=HYPERLINK(""http://evil.example"")"',
    );
    expect(escapeCsvCell('+1+1')).toBe(`"'+1+1"`);
    expect(escapeCsvCell('-2+3')).toBe(`"'-2+3"`);
    expect(escapeCsvCell('@SUM(A1)')).toBe(`"'@SUM(A1)"`);
  });

  it('neutralizes formulas hidden behind spaces and control characters', () => {
    expect(escapeCsvCell('   =SUM(A1)')).toBe(`"'   =SUM(A1)"`);
    expect(escapeCsvCell('\t=1+1')).toBe(`"'\t=1+1"`);
    expect(escapeCsvCell('\n@cmd')).toBe(`"'\n@cmd"`);
    expect(escapeCsvCell('\u0000-cmd')).toBe(`"'\u0000-cmd"`);
    expect(escapeCsvCell('\u200B=1+1')).toBe(`"'\u200B=1+1"`);
  });

  it('quotes quotes, separators and line breaks without changing the value', () => {
    expect(escapeCsvCell('say "hi", ok\nnext')).toBe(
      '"say ""hi"", ok\nnext"',
    );
    expect(escapeCsvCell('a;b')).toBe('"a;b"');
    expect(escapeCsvCell('обычный текст')).toBe('"обычный текст"');
  });

  it('leaves ordinary text and real numbers intact', () => {
    expect(escapeCsvCell('Куриная грудка')).toBe('"Куриная грудка"');
    expect(escapeCsvCell('2026-11-15')).toBe('"2026-11-15"');
    expect(escapeCsvCell(123)).toBe('"123"');
    expect(escapeCsvCell(-5)).toBe('"-5"');
    expect(escapeCsvCell(0)).toBe('"0"');
  });

  it('does not prefix a value that already starts with an apostrophe', () => {
    expect(escapeCsvCell("'=1+1")).toBe(`"'=1+1"`);
  });

  it('handles empty and missing values', () => {
    expect(escapeCsvCell('')).toBe('""');
    expect(escapeCsvCell(null)).toBe('""');
    expect(escapeCsvCell(undefined)).toBe('""');
  });
});

describe('buildCsv', () => {
  it('keeps the UTF-8 BOM, semicolon separator and CRLF rows', () => {
    const csv = buildCsv([
      ['Дата', 'Блюдо', 'Ккал'],
      ['2026-11-15', '=1+1', 320],
    ]);

    expect(csv.startsWith('\uFEFF')).toBe(true);
    expect(csv).toBe(
      '\uFEFF"Дата";"Блюдо";"Ккал"\r\n"2026-11-15";"\'=1+1";"320"',
    );
  });
});
