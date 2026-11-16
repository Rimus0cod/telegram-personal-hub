import type { CatalogFood } from './food-catalog';

export interface ScaledNutrition {
  calories: number;
  protein: number;
  carbs: number;
  fat: number;
}

const round1 = (value: number) => Math.round(value * 10) / 10;

/**
 * Scale per-100-g catalog values to a serving. Calories stay whole because the
 * API stores them as an integer; macros keep one decimal place.
 */
export function scaleFoodNutrition(
  food: Pick<CatalogFood, 'kcal' | 'p' | 'c' | 'f'>,
  grams: number,
): ScaledNutrition {
  const safeGrams = Number.isFinite(grams) && grams > 0 ? grams : 100;
  const factor = safeGrams / 100;
  return {
    calories: Math.round(food.kcal * factor),
    protein: round1(food.p * factor),
    carbs: round1(food.c * factor),
    fat: round1(food.f * factor),
  };
}
