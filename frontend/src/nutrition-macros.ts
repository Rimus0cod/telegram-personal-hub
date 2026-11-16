/** Client-side macro calculator for the existing Nutrition targets. */

export interface MacroPercentages {
  protein: number;
  carbs: number;
  fat: number;
}

export interface MacroGrams {
  protein: number;
  carbs: number;
  fat: number;
}

export const MACRO_CALORIES_PER_GRAM = {
  protein: 4,
  carbs: 4,
  fat: 9,
} as const;

/** A deliberately simple starting point, not a medical recommendation. */
export const DEFAULT_MACRO_PERCENTAGES: MacroPercentages = {
  protein: 30,
  carbs: 40,
  fat: 30,
};

export const MIN_CALORIE_TARGET = 500;
export const MAX_CALORIE_TARGET = 10_000;
export const MAX_PROTEIN_TARGET = 1_000;
export const MAX_CARBS_TARGET = 1_500;
export const MAX_FAT_TARGET = 500;

export interface NutritionTargets {
  calories: number;
  protein: number;
  carbs: number;
  fat: number;
}

export function isValidCalorieTarget(value: number): boolean {
  return (
    Number.isInteger(value) && value >= MIN_CALORIE_TARGET && value <= MAX_CALORIE_TARGET
  );
}

/** Mirrors the backend preference constraints before a PATCH is attempted. */
export function validateNutritionTargets(targets: NutritionTargets): string | null {
  if (!isValidCalorieTarget(targets.calories)) {
    return 'Цель по калориям: целое число от 500 до 10 000.';
  }
  const macroLimits: [keyof Omit<NutritionTargets, 'calories'>, string, number][] = [
    ['protein', 'белкам', MAX_PROTEIN_TARGET],
    ['carbs', 'углеводам', MAX_CARBS_TARGET],
    ['fat', 'жирам', MAX_FAT_TARGET],
  ];
  for (const [key, label, maximum] of macroLimits) {
    const value = targets[key];
    if (!Number.isInteger(value) || value < 0 || value > maximum) {
      return `Цель по ${label}: целое число от 0 до ${maximum.toLocaleString('ru-RU')}.`;
    }
  }
  return null;
}

export function validateMacroPercentages(percentages: MacroPercentages): string | null {
  const values = [percentages.protein, percentages.carbs, percentages.fat];
  if (values.some((value) => !Number.isFinite(value) || value < 0 || value > 100)) {
    return 'Каждый процент должен быть числом от 0 до 100.';
  }
  const total = values.reduce((sum, value) => sum + value, 0);
  if (Math.abs(total - 100) > 0.01) {
    return `Сумма процентов должна быть 100. Сейчас: ${Math.round(total * 10) / 10}.`;
  }
  return null;
}

/** Calories represented by the current grams. */
export function macroCalories(grams: MacroGrams): number {
  return (
    grams.protein * MACRO_CALORIES_PER_GRAM.protein +
    grams.carbs * MACRO_CALORIES_PER_GRAM.carbs +
    grams.fat * MACRO_CALORIES_PER_GRAM.fat
  );
}

/**
 * Convert calorie percentages into whole grams. Rounding is done once on each
 * macro; the returned calorie total reflects the rounded values and can differ
 * slightly from the target.
 */
export function calculateMacroGrams(
  calories: number,
  percentages: MacroPercentages,
): (MacroGrams & { caloriesFromMacros: number }) | null {
  if (!isValidCalorieTarget(calories) || validateMacroPercentages(percentages) !== null) {
    return null;
  }

  const protein = Math.round(
    (calories * percentages.protein) / 100 / MACRO_CALORIES_PER_GRAM.protein,
  );
  const carbs = Math.round(
    (calories * percentages.carbs) / 100 / MACRO_CALORIES_PER_GRAM.carbs,
  );
  const fat = Math.round(
    (calories * percentages.fat) / 100 / MACRO_CALORIES_PER_GRAM.fat,
  );

  return {
    protein,
    carbs,
    fat,
    caloriesFromMacros: macroCalories({ protein, carbs, fat }),
  };
}
