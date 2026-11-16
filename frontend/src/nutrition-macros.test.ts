import { describe, expect, it } from 'vitest';
import {
  DEFAULT_MACRO_PERCENTAGES,
  MAX_CALORIE_TARGET,
  MIN_CALORIE_TARGET,
  calculateMacroGrams,
  isValidCalorieTarget,
  macroCalories,
  validateMacroPercentages,
  validateNutritionTargets,
} from './nutrition-macros';

describe('nutrition macro calculator', () => {
  it('uses a valid default ratio', () => {
    expect(validateMacroPercentages(DEFAULT_MACRO_PERCENTAGES)).toBeNull();
    expect(
      DEFAULT_MACRO_PERCENTAGES.protein +
        DEFAULT_MACRO_PERCENTAGES.carbs +
        DEFAULT_MACRO_PERCENTAGES.fat,
    ).toBe(100);
  });

  it('calculates grams from calories and percentages', () => {
    expect(calculateMacroGrams(2000, { protein: 30, carbs: 40, fat: 30 })).toEqual({
      protein: 150,
      carbs: 200,
      fat: 67,
      caloriesFromMacros: 2003,
    });
  });

  it('rounds each macro once and reports calories from the rounded grams', () => {
    const result = calculateMacroGrams(1800, { protein: 25, carbs: 45, fat: 30 });
    expect(result).toEqual({
      protein: 113,
      carbs: 203,
      fat: 60,
      caloriesFromMacros: 1804,
    });
    expect(result && macroCalories(result)).toBe(1804);
  });

  it('rejects invalid calorie targets', () => {
    expect(isValidCalorieTarget(MIN_CALORIE_TARGET)).toBe(true);
    expect(isValidCalorieTarget(MAX_CALORIE_TARGET)).toBe(true);
    expect(isValidCalorieTarget(MIN_CALORIE_TARGET - 1)).toBe(false);
    expect(isValidCalorieTarget(MAX_CALORIE_TARGET + 1)).toBe(false);
    expect(isValidCalorieTarget(Number.NaN)).toBe(false);
    expect(isValidCalorieTarget(2000.5)).toBe(false);
    expect(calculateMacroGrams(100, DEFAULT_MACRO_PERCENTAGES)).toBeNull();
  });

  it('validates the full target payload against backend limits', () => {
    expect(
      validateNutritionTargets({ calories: 2000, protein: 150, carbs: 200, fat: 67 }),
    ).toBeNull();
    expect(
      validateNutritionTargets({ calories: 2000.5, protein: 150, carbs: 200, fat: 67 }),
    ).toContain('калориям');
    expect(
      validateNutritionTargets({ calories: 2000, protein: -1, carbs: 200, fat: 67 }),
    ).toContain('белкам');
    expect(
      validateNutritionTargets({ calories: 2000, protein: 150, carbs: 1501, fat: 67 }),
    ).toContain('углеводам');
    expect(
      validateNutritionTargets({ calories: 2000, protein: 150, carbs: 200, fat: 501 }),
    ).toContain('жирам');
  });

  it('rejects percentages that do not add up to 100', () => {
    expect(validateMacroPercentages({ protein: 30, carbs: 40, fat: 20 })).toContain('100');
    expect(calculateMacroGrams(2000, { protein: 30, carbs: 40, fat: 20 })).toBeNull();
  });

  it('rejects percentages outside 0–100 and non-finite values', () => {
    expect(validateMacroPercentages({ protein: -1, carbs: 51, fat: 50 })).not.toBeNull();
    expect(validateMacroPercentages({ protein: 101, carbs: 0, fat: -1 })).not.toBeNull();
    expect(validateMacroPercentages({ protein: Number.NaN, carbs: 50, fat: 50 })).not.toBeNull();
  });

  it('accepts zero grams for a macro', () => {
    expect(calculateMacroGrams(2000, { protein: 0, carbs: 50, fat: 50 })).toEqual({
      protein: 0,
      carbs: 250,
      fat: 111,
      caloriesFromMacros: 1999,
    });
  });
});
