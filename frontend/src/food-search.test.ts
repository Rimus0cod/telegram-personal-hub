import { describe, expect, it } from 'vitest';
import { foodSearchMatches } from './food-search';

describe('catalog search', () => {
  it('matches potato synonyms and cooked preparations', () => {
    expect(foodSearchMatches('Картофель — варёный', 'Овощи · приготовленные', 'отварной картофель')).toBe(true);
    expect(foodSearchMatches('Картофель — жареный', 'Овощи · приготовленные', 'жареная картошка')).toBe(true);
    expect(foodSearchMatches('Картофельное пюре на молоке', 'Гарниры', 'картошка пюре')).toBe(true);
  });

  it('matches baked, stewed and ready dishes through stems', () => {
    expect(foodSearchMatches('Курица — запечённое', 'Мясо', 'запечённая курица')).toBe(true);
    expect(foodSearchMatches('Овощи — тушёные', 'Овощи · приготовленные', 'тушёные овощи')).toBe(true);
    expect(foodSearchMatches('Овсянка на воде — готовая', 'Крупы и хлеб', 'готовая овсянка')).toBe(true);
    expect(foodSearchMatches('Рыба — жареная', 'Рыба', 'жареная рыба')).toBe(true);
  });

  it('keeps English aliases working', () => {
    expect(foodSearchMatches('Куриная грудка', 'Мясо и рыба', 'chicken breast')).toBe(true);
    expect(foodSearchMatches('Картофель — варёный', 'Овощи · приготовленные', 'boiled potato')).toBe(true);
  });

  it('does not match an unrelated dish', () => {
    expect(foodSearchMatches('Борщ', 'Супы', 'шоколад')).toBe(false);
  });
});
