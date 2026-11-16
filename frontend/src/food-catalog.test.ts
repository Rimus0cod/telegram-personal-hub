import { describe, expect, it } from 'vitest';
import {
  allFoods,
  baseFoods,
  commonFoods,
  enrichFood,
  expandedFoods,
  inferFoodType,
  inferPreparationState,
  preparedMeals,
  uniqueFoods,
} from './food-catalog';

const normalizeName = (value: string) =>
  value
    .toLocaleLowerCase('ru-RU')
    .replace(/ё/g, 'е')
    .replace(/[^a-zа-я0-9]+/gi, ' ')
    .trim();

describe('common food catalog entries', () => {
  it('contains everyday foods with unique names', () => {
    const names = commonFoods.map((food) => food.name);
    expect(new Set(names).size).toBe(names.length);
    expect(names).toEqual(
      expect.arrayContaining([
        'Овсянка на воде — готовая',
        'Макароны — отварные',
        'Куриная грудка — отварная',
        'Индейка — запечённая',
        'Говядина — тушёная',
        'Свинина — запечённая',
        'Лосось — запечённый',
        'Тунец в воде',
        'Йогурт натуральный 2%',
        'Грецкие орехи',
        'Хлеб ржаной',
      ]),
    );
  });

  it('does not duplicate an existing expanded-catalog name', () => {
    const existing = new Set(expandedFoods.map((food) => normalizeName(food.name)));
    for (const food of commonFoods) {
      expect(existing.has(normalizeName(food.name))).toBe(false);
    }
  });

  it('keeps cooked values consistent with their labels', () => {
    const byName = new Map(commonFoods.map((food) => [food.name, food]));
    expect(byName.get('Куриная грудка — отварная')).toMatchObject({ kcal: 151, p: 29, f: 3 });
    expect(byName.get('Индейка — запечённая')).toMatchObject({ kcal: 147, p: 30, f: 2.1 });
    expect(byName.get('Свинина — запечённая')).toMatchObject({ kcal: 248, p: 27, f: 14.7 });
    expect(byName.get('Лосось — запечённый')).toMatchObject({ kcal: 206, p: 22, f: 12 });
  });

  it('keeps nutrition values sane per 100 g', () => {
    for (const food of commonFoods) {
      expect(food.kcal).toBeGreaterThanOrEqual(0);
      expect(food.kcal).toBeLessThanOrEqual(900);
      expect(food.p).toBeGreaterThanOrEqual(0);
      expect(food.c).toBeGreaterThanOrEqual(0);
      expect(food.f).toBeGreaterThanOrEqual(0);
      expect(food.category.length).toBeGreaterThan(0);
      expect(food.name).toMatch(/готовая|отварн|запеч|тушён|воде|натуральный|орехи|ржаной/);
    }
  });
});

describe('prepared meal catalog', () => {
  it('contains the everyday prepared dishes from the brief', () => {
    const names = new Set(allFoods.map((food) => normalizeName(food.name)));
    const required = [
      'картофельное пюре на молоке',
      'картофельное пюре с маслом',
      'картофель по деревенски',
      'овсяная каша на молоке',
      'гречневая каша с молоком',
      'овсянка с бананом',
      'куриная грудка запечённая',
      'куриное бедро запечённое',
      'курица тушёная',
      'котлеты куриные',
      'котлеты свиные',
      'котлеты говяжьи',
      'тефтели',
      'гуляш',
      'плов',
      'рыба запечённая',
      'рыба жареная',
      'рыба тушёная',
      'рыбные котлеты',
      'рыба на пару',
      'борщ',
      'куриный суп',
      'суп с фрикадельками',
      'гороховый суп',
      'рассольник',
      'суп с лапшой',
      'солянка',
      'омлет',
      'яичница',
      'варёные яйца',
      'сырники',
      'блины',
      'оладьи',
      'творожная запеканка',
      'винегрет',
      'оливье',
      'греческий салат',
      'салат из овощей',
      'салат из капусты',
      'тушёные овощи',
      'овощное рагу',
      'кабачковые оладьи',
      'голубцы',
      'вареники с творогом',
      'макароны с фаршем',
      'запеканка картофельная',
      'фаршированный перец',
      'гречка с мясом',
      'рис с курицей',
      'творог с фруктами',
      // Existing entries that already satisfy the brief without duplication.
      'картофель варёный',
      'картофель запечённый',
      'картофель жареный',
      'гречка варёная',
      'рис варёный',
      'булгур варёный',
      'макароны отварные',
      'овсянка на воде готовая',
      'говядина тушёная',
      'индейка запечённая',
      'лосось запечённый',
      'овощной суп',
      'грибной суп',
      'пельмени',
      'вареники с картофелем',
    ];
    for (const name of required) expect(names.has(normalizeName(name))).toBe(true);
  });

  it('marks every prepared item as a ready dish with metadata', () => {
    for (const food of preparedMeals) {
      expect(food.type).toBe('prepared_meal');
      expect(food.category.length).toBeGreaterThan(0);
      expect(food.servingSize).toBe(100);
      expect(food.servingUnit).toBe('g');
      expect(food.preparationState).toBeTruthy();
      expect(typeof food.approximate).toBe('boolean');
      expect(food.kcal).toBeGreaterThanOrEqual(0);
      expect(food.kcal).toBeLessThanOrEqual(900);
      expect(food.p).toBeGreaterThanOrEqual(0);
      expect(food.c).toBeGreaterThanOrEqual(0);
      expect(food.f).toBeGreaterThanOrEqual(0);
    }
  });

  it('does not duplicate any existing catalog name', () => {
    const existing = new Set(
      [...baseFoods, ...commonFoods, ...expandedFoods].map((food) =>
        normalizeName(food.name),
      ),
    );
    for (const food of preparedMeals) {
      expect(existing.has(normalizeName(food.name))).toBe(false);
    }
  });

  it('enriches every catalog item with id, type and serving metadata', () => {
    for (const food of allFoods) {
      expect(food.id.length).toBeGreaterThan(0);
      expect(['ingredient', 'prepared_meal']).toContain(food.type);
      expect(food.servingSize).toBeGreaterThan(0);
      expect(['g', 'ml', 'piece']).toContain(food.servingUnit);
    }
    const ids = allFoods.map((food) => food.id);
    expect(new Set(ids).size).toBe(ids.length);
    const names = allFoods.map((food) => normalizeName(food.name));
    expect(new Set(names).size).toBe(names.length);
  });

  it('infers type and preparation state from existing names', () => {
    expect(inferFoodType({ name: 'Картофель', category: 'Овощи' })).toBe('ingredient');
    expect(inferFoodType({ name: 'Картофель — варёный', category: 'Овощи · приготовленные' })).toBe('prepared_meal');
    expect(inferFoodType({ name: 'Колбаса варёная', category: 'Колбасы' })).toBe('ingredient');
    expect(inferPreparationState('Куриная грудка — отварная')).toBe('boiled');
    expect(inferPreparationState('Лосось — запечённый')).toBe('baked');
    expect(inferPreparationState('Говядина — тушёная')).toBe('stewed');
    expect(inferPreparationState('Овсянка сухая')).toBe('dry');
  });

  it('keeps uniqueFoods stable and deduplicates normalized names', () => {
    const foods = [
      enrichFood({ name: 'Борщ', category: 'Супы', kcal: 60, p: 3, c: 7, f: 2 }),
      enrichFood({ name: 'борщ', category: 'Супы', kcal: 61, p: 3, c: 7, f: 2 }),
    ];
    expect(uniqueFoods(foods)).toHaveLength(1);
  });
});
