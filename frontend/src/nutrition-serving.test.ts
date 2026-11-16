import { describe, expect, it } from 'vitest';
import { scaleFoodNutrition } from './nutrition-serving';

const chicken = { kcal: 165, p: 31, c: 0, f: 3.6 };

describe('nutrition serving recalculation', () => {
  it('uses the catalog values for 100 g', () => {
    expect(scaleFoodNutrition(chicken, 100)).toEqual({
      calories: 165,
      protein: 31,
      carbs: 0,
      fat: 3.6,
    });
  });

  it('scales calories and macros to 50 g', () => {
    expect(scaleFoodNutrition(chicken, 50)).toEqual({
      calories: 83,
      protein: 15.5,
      carbs: 0,
      fat: 1.8,
    });
  });

  it('scales calories and macros to 150 g', () => {
    expect(scaleFoodNutrition(chicken, 150)).toEqual({
      calories: 248,
      protein: 46.5,
      carbs: 0,
      fat: 5.4,
    });
  });

  it('rounds one decimal place for macros and whole calories', () => {
    expect(scaleFoodNutrition({ kcal: 71, p: 2.5, c: 12, f: 1.5 }, 250)).toEqual({
      calories: 178,
      protein: 6.3,
      carbs: 30,
      fat: 3.8,
    });
  });

  it('falls back to 100 g for invalid servings', () => {
    expect(scaleFoodNutrition(chicken, 0)).toEqual(scaleFoodNutrition(chicken, 100));
    expect(scaleFoodNutrition(chicken, Number.NaN)).toEqual(scaleFoodNutrition(chicken, 100));
  });
});
