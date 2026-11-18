import { describe, expect, it } from 'vitest';
import { foodSearchMatches, mergeSearchFoods } from './food-search';
import type { CatalogFoodItem } from './food-catalog';

const item = (name: string, kcal = 100, id = ''): CatalogFoodItem => ({
  name,
  category: 'Тест',
  kcal,
  p: 5,
  c: 10,
  f: 3,
  type: 'prepared_meal',
  servingSize: 100,
  servingUnit: 'g',
  id,
});

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

  it('matches multilingual aliases supplied by the catalog', () => {
    expect(foodSearchMatches('Potato', 'Vegetables', 'картошка', ['картошка', 'potato'])).toBe(true);
    expect(foodSearchMatches('Potato', 'Vegetables', 'kartoffel', ['картошка', 'potato'])).toBe(false);
  });

  it('normalizes case, diacritics, dashes and apostrophes', () => {
    expect(foodSearchMatches('Café latte', 'Напитки', 'CAFE')).toBe(true);
    expect(foodSearchMatches('Café latte', 'Напитки', 'café')).toBe(true);
    expect(foodSearchMatches('Картофель по-деревенски', 'Гарниры', 'картофель по деревенски')).toBe(true);
    expect(foodSearchMatches("М'ясо", 'Мясо и рыба', 'мясо')).toBe(true);
    expect(foodSearchMatches('Ёлка', 'Другое', 'елка')).toBe(true);
  });

  it('does not match an unrelated dish', () => {
    expect(foodSearchMatches('Борщ', 'Супы', 'шоколад')).toBe(false);
  });

  it('merges search sources without duplicate dishes', () => {
    const server = [item('Борщ с говядиной', 65, 'curated:1')];
    const remote = [item('Borscht', 60, 'off:1'), item('Борщ с говядиной', 65, 'curated:1')];
    const local = [item('Борщ с говядиной', 65, 'curated:1'), item('Old local dish', 120)];

    const active = mergeSearchFoods(true, server, remote, local);
    expect(active.map((food) => food.name)).toEqual(['Борщ с говядиной', 'Borscht']);

    const fallback = mergeSearchFoods(false, server, remote, local);
    expect(fallback.map((food) => food.name)).toEqual([
      'Borscht',
      'Борщ с говядиной',
      'Old local dish',
    ]);
  });

  it('keeps one entry and the same macros when the display language changes', () => {
    const english = item('Potato', 77, 'curated:9');
    const russian = item('Картофель', 77, 'curated:9');
    const merged = mergeSearchFoods(true, [english, russian], [], []);
    expect(merged).toHaveLength(1);
    expect(merged[0].name).toBe('Potato');
    expect(merged[0].kcal).toBe(77);
    expect(merged[0].p).toBe(5);
  });

  it('falls back to local results when the server returns nothing', () => {
    const local = [item('Картофельное пюре', 85)];
    const merged = mergeSearchFoods(false, [], [], local);
    expect(merged).toHaveLength(1);
    expect(merged[0].kcal).toBe(85);
  });
});
