export type FoodType = 'ingredient' | 'prepared_meal';
export type ServingUnit = 'g' | 'ml' | 'piece';
export type PreparationState =
  | 'raw'
  | 'dry'
  | 'boiled'
  | 'baked'
  | 'fried'
  | 'stewed'
  | 'steamed'
  | 'ready'
  | 'canned';

export type CatalogFood = {
  name: string;
  category: string;
  kcal: number;
  p: number;
  c: number;
  f: number;
  id?: string;
  type?: FoodType;
  servingSize?: number;
  servingUnit?: ServingUnit;
  preparationState?: PreparationState;
  approximate?: boolean;
  source?: string;
};

export type CatalogFoodItem = CatalogFood & {
  id: string;
  type: FoodType;
  servingSize: number;
  servingUnit: ServingUnit;
};

const normalizeCatalogName = (value: string) =>
  value
    .toLocaleLowerCase('ru-RU')
    .replace(/ё/g, 'е')
    .replace(/[^a-zа-я0-9]+/gi, ' ')
    .trim();

function catalogSlug(value: string): string {
  return normalizeCatalogName(value).replace(/\s+/g, '-');
}

export function inferFoodType(
  food: Pick<CatalogFood, 'name' | 'category' | 'type'>,
): FoodType {
  if (food.type) return food.type;
  const category = food.category.toLocaleLowerCase('ru-RU');
  const name = food.name.toLocaleLowerCase('ru-RU');
  if (
    category.includes('готов') ||
    category.includes('суп') ||
    category.includes('приготовлен')
  ) {
    return 'prepared_meal';
  }
  // Sausages are processed products sold as ingredients, even when the name
  // contains "варёная"; keep them in the ingredient filter.
  if (category.includes('колбас')) return 'ingredient';
  if (/(варён|отварн|запечён|жарен|тушён|готов|на пару|приготовлен)/.test(name)) {
    return 'prepared_meal';
  }
  return 'ingredient';
}

export function inferPreparationState(name: string): PreparationState | undefined {
  const value = name.toLocaleLowerCase('ru-RU');
  if (/сух(ой|ая|ие|их)/.test(value)) return 'dry';
  if (/сыр(ой|ая|ые|ое)/.test(value)) return 'raw';
  if (/варён|отварн/.test(value)) return 'boiled';
  if (/запечён/.test(value)) return 'baked';
  if (/жарен/.test(value)) return 'fried';
  if (/тушён/.test(value)) return 'stewed';
  if (/на пару/.test(value)) return 'steamed';
  if (/консерв|тунец.*воде/.test(value)) return 'canned';
  if (/готов/.test(value)) return 'ready';
  return undefined;
}

export function enrichFood(food: CatalogFood): CatalogFoodItem {
  const type = inferFoodType(food);
  return {
    ...food,
    id: food.id ?? `${type}:${catalogSlug(food.category)}:${catalogSlug(food.name)}`,
    type,
    servingSize: food.servingSize ?? 100,
    servingUnit: food.servingUnit ?? 'g',
    preparationState: food.preparationState ?? inferPreparationState(food.name),
  };
}

/** Keep the first entry for every normalized name; no silent data rewriting. */
export function uniqueFoods<T extends CatalogFood>(foods: T[]): T[] {
  const seen = new Set<string>();
  return foods.filter((food) => {
    const key = normalizeCatalogName(food.name);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

export function enrichFoods(foods: CatalogFood[]): CatalogFoodItem[] {
  return uniqueFoods(foods).map(enrichFood);
}

const meatCuts:[string,number,number,number][]=[
 ['Куриное бедро без кожи',177,24,8],['Куриное бедро с кожей',215,18,15],['Куриная голень',172,28,6],['Куриное крыло',203,30,8],['Куриное филе',110,23,2],['Куриная печень',119,17,5],['Куриные сердечки',153,16,9],['Куриные желудки',94,18,2],['Утка грудка без кожи',140,25,4],['Утка с кожей',337,19,28],['Индейка грудка',114,24,2],['Индейка бедро',144,20,7],['Говяжья вырезка',150,22,7],['Говяжий стейк рибай',250,24,17],['Говяжий стриплойн',210,25,12],['Говяжья лопатка',190,20,12],['Говяжья грудинка',280,18,23],['Говяжья голяшка',170,22,8],['Говяжий фарш 5%',137,21,5],['Говяжий фарш 15%',215,18,15],['Говяжий язык',224,16,17],['Говяжье сердце',112,17,4],['Говяжья печень',135,20,4],['Свиной карбонад',145,22,6],['Свиная вырезка',143,21,6],['Свиная корейка',196,20,12],['Свиная шея',290,17,25],['Свиная лопатка',235,18,17],['Свиная грудинка',518,9,53],['Свиные рёбра',277,17,23],['Свиной фарш',263,17,21],['Свиной язык',208,16,15],['Баранина корейка',294,25,21],['Баранина лопатка',238,20,17],['Фарш бараний',282,17,23],['Кролик филе',136,21,6],['Оленина',158,30,3],['Конина',167,28,6],
];
const methods=[
 {name:'отварное',k:0.92,p:1.06,c:1,f:0.78},
 {name:'запечённое',k:1.08,p:1.08,c:1,f:1.05},
 {name:'на гриле',k:1.06,p:1.08,c:1,f:0.94},
 {name:'тушёное',k:1.02,p:1.02,c:1.08,f:0.96},
 {name:'жареное без панировки',k:1.23,p:1,c:1,f:1.42},
];
const meatFoods=meatCuts.flatMap(([cut,kcal,p,f])=>methods.map(m=>({name:`${cut} — ${m.name}`,category:'Мясо',kcal:Math.round(kcal*m.k),p:Math.round(p*m.p),c:0,f:Math.round(f*m.f)})));

const fishCuts:[string,number,number,number,number][]=[
 ['Треска',82,18,0,1],['Хек',86,18,0,2],['Минтай',72,16,0,1],['Судак',84,19,0,1],['Окунь',96,19,0,2],['Дорадо',96,19,0,2],['Сибас',97,18,0,3],['Форель',148,20,0,7],['Скумбрия',205,19,0,14],['Сельдь',158,18,0,9],['Сардины',208,25,0,11],['Камбала',86,16,0,2],['Креветки очищенные',99,24,0,0],['Кальмар',92,16,3,1],['Мидии',86,12,4,2],['Осьминог',82,15,2,1],['Морской гребешок',88,17,3,1],['Кета',127,20,0,5],['Горбуша',140,20,0,6],['Палтус',140,18,0,7],
];
const fishMethods=[{name:'отварная',k:.94,p:1.05,f:.8},{name:'запечённая',k:1.08,p:1.06,f:1.05},{name:'на гриле',k:1.06,p:1.06,f:.95},{name:'жареная без панировки',k:1.28,p:1,f:1.5}];
const fishFoods=fishCuts.flatMap(([name,k,p,c,f])=>fishMethods.map(m=>({name:`${name} — ${m.name}`,category:'Рыба и морепродукты',kcal:Math.round(k*m.k),p:Math.round(p*m.p),c:Math.round(c),f:Math.round(f*m.f)})));

const extras:CatalogFood[]=[
 ...[['Цветная капуста',25,2,5,0],['Кабачок',17,1,3,0],['Баклажан',25,1,6,0],['Стручковая фасоль',31,2,7,0],['Свёкла',43,2,10,0],['Тыква',26,1,7,0],['Кукуруза',96,3,21,1],['Зелёный горошек',81,5,14,0],['Спаржа',20,2,4,0],['Шпинат',23,3,4,0],['Капуста белокочанная',25,1,6,0],['Брюссельская капуста',43,3,9,0]].map(([name,kcal,p,c,f])=>({name:String(name),category:'Овощи',kcal:Number(kcal),p:Number(p),c:Number(c),f:Number(f)})),
 ...[['Цветная капуста — отварная',23,2,4,0],['Цветная капуста — запечённая',40,3,7,1],['Брокколи — отварная',35,2,7,0],['Брокколи — на пару',35,3,7,0],['Кабачок — запечённый',25,1,5,0],['Баклажан — запечённый',35,1,8,0],['Свёкла — варёная',44,2,10,0],['Кукуруза — варёная',96,3,21,1],['Фасоль стручковая — варёная',35,2,8,0],['Картофель — варёный',87,2,20,0],['Картофель — запечённый',93,2,21,0],['Картофель — жареный',196,3,27,9],['Морковь — варёная',35,1,8,0],['Тыква — запечённая',40,1,10,0]].map(([name,kcal,p,c,f])=>({name:String(name),category:'Овощи · приготовленные',kcal:Number(kcal),p:Number(p),c:Number(c),f:Number(f)})),
 ...[['Чечевица сухая',352,25,60,1],['Чечевица варёная',116,9,20,0],['Нут сухой',364,19,61,6],['Нут варёный',164,9,27,3],['Фасоль красная сухая',333,24,60,1],['Фасоль красная варёная',127,9,23,1],['Фасоль белая варёная',139,9,25,1],['Горох колотый сухой',341,25,60,1],['Горох варёный',118,8,21,0],['Булгур сухой',342,12,76,1],['Булгур варёный',83,3,19,0],['Киноа сухая',368,14,64,6],['Киноа варёная',120,4,21,2],['Кускус сухой',376,13,77,1],['Кускус варёный',112,4,23,0],['Перловка сухая',352,10,74,1],['Перловка варёная',123,3,28,1],['Пшено сухое',378,11,73,4],['Пшено варёное',119,4,23,1],['Ячневая крупа сухая',324,10, 71,2]].map(([name,kcal,p,c,f])=>({name:String(name),category:'Крупы и бобовые',kcal:Number(kcal),p:Number(p),c:Number(c),f:Number(f)})),
];

const soups:CatalogFood[]=[
 ...[['Борщ постный',48,2,8,1],['Борщ с говядиной',62,4,6,2],['Борщ со свининой',78,4,6,4],['Борщ с курицей',55,5,6,1],['Борщ с фасолью',66,4,10,1],['Борщ зелёный со щавелем',47,3,5,2],['Борщ московский',68,4,7,3],['Борщ украинский с салом',95,4,7,6],['Щи из свежей капусты',42,2,6,1],['Щи с говядиной',56,4,4,2],['Щи из квашеной капусты',48,3,5,2],['Щи постные',35,1,6,1],['Рассольник с говядиной',50,3,6,1],['Рассольник с курицей',44,4,5,1],['Рассольник постный',36,1,6,1],['Солянка мясная',82,6,4,5],['Солянка сборная',95,7,4,6],['Солянка рыбная',63,6,3,3],['Суп гороховый постный',62,3,10,1],['Гороховый суп с копчёностями',91,6,8,4],['Гороховый суп с курицей',72,7,8,2],['Чечевичный суп',67,4,10,1],['Чечевичный суп с курицей',83,8,9,2],['Фасолевый суп',60,4,9,1],['Грибной суп',38,2,5,1],['Грибной суп с перловкой',54,2,9,1],['Грибной крем-суп',78,3,6,5],['Куриный бульон',15,2,0,1],['Говяжий бульон',24,3,0,1],['Костный бульон',32,5,0,1],['Куриный суп с лапшой',48,4,6,1],['Куриный суп с вермишелью',52,4,7,1],['Куриный суп с рисом',43,4,5,1],['Куриный суп с фрикадельками',61,5,5,2],['Куриный суп с картофелем',46,4,6,1],['Суп с клёцками',65,3,8,2],['Суп с фрикадельками из говядины',58,4,5,2],['Суп с фрикадельками из индейки',49,5,4,1],['Уха из судака',39,5,3,1],['Уха из лосося',54,5,2,3],['Уха по-фински со сливками',78,5,4,4],['Рыбный суп с овощами',43,5,3,1],['Томатный суп',42,2,7,1],['Томатный крем-суп',65,2,7,3],['Томатный суп с чечевицей',70,4,10,1],['Тыквенный крем-суп',58,2,8,2],['Тыквенный суп со сливками',82,2,8,5],['Суп-пюре из брокколи',49,3,6,1],['Крем-суп из брокколи со сливками',76,3,5,5],['Суп-пюре из цветной капусты',45,3,6,1],['Сырный крем-суп',96,4,5,7],['Картофельный крем-суп',72,2,10,3],['Суп-пюре из шпината',48,3,5,2],['Овощной суп',32,1,5,1],['Минестроне',48,2,7,1],['Овощной суп с курицей',45,5,4,1],['Суп с фрикадельками и овощами',53,4,5,2],['Гаспачо',35,1,6,1],['Окрошка на кефире с курицей',58,5,5,2],['Окрошка на квасе с колбасой',72,4,7,3],['Свекольник холодный',45,2,7,1],['Холодник на кефире',52,3,6,2],['Таратор с огурцом',48,3,4,2],['Мисо-суп с тофу',45,3,4,2],['Мисо-суп с лососем',62,5,3,3],['Том ям с креветками',68,5,5,3],['Том кха с курицей',105,6,5,7],['Фо бо с говядиной',75,5,9,2],['Рамен с курицей',92,6,10,3],['Рамен со свининой',128,7,12,6],['Суп с удоном и курицей',83,6,10,2],['Суп с вонтонами',66,4,8,2],['Харчо с говядиной',73,4,7,3],['Харчо с бараниной',89,5,6,5],['Лагман',82,5,9,3],['Шурпа с бараниной',76,5,4,4],['Шурпа с говядиной',62,5,4,2],['Гуляш-суп',74,5,6,3],['Суп-пюре из картофеля с беконом',96,3,9,6],['Суп из батата',61,2,10,1],['Кукурузный крем-суп',76,2,11,3],['Суп из морепродуктов',67,7,4,2],['Крем-суп из шампиньонов',83,3,5,6],['Суп с фрикадельками и рисом',58,4,7,1]].map(([name,kcal,p,c,f])=>({name:String(name),category:'Супы',kcal:Number(kcal),p:Number(p),c:Number(c),f:Number(f)})),
];

const homemadeSoups:CatalogFood[]=[
 ...[['Обычный куриный суп с картошкой и морковью',38,4,5,1],['Домашний суп с куриной грудкой, картофелем и морковью',42,6,5,1],['Куриный суп с картошкой, морковью и луком',36,3,5,1],['Куриный суп с вермишелью, картофелем и морковью',48,4,7,1],['Куриный суп с домашней лапшой и овощами',52,5,7,1],['Куриный суп с рисом, картошкой и морковью',45,4,7,1],['Куриный суп с гречкой и овощами',43,4,6,1],['Куриный суп с перловкой, картошкой и морковью',44,4,7,1],['Лёгкий суп на курином бульоне с овощами',29,2,4,1],['Суп на курином бульоне с капустой и картофелем',34,3,5,1],['Домашний суп с куриными фрикадельками и картофелем',51,5,5,2],['Суп с курицей, брокколи и картофелем',37,4,4,1],['Суп с курицей, цветной капустой и морковью',33,4,3,1],['Куриный суп с фасолью, морковью и картофелем',48,4,7,1],['Куриный суп с зелёным горошком и картофелем',42,4,6,1],['Куриный суп с тыквой, картофелем и морковью',37,3,6,1],['Куриный суп с кабачком, картофелем и морковью',32,3,5,1],['Куриный суп с яйцом и овощами',43,4,3,2],['Куриный суп с рисом и яйцом',51,4,7,2],['Куриный бульон с картофелем и зеленью',31,2,5,1],['Простой овощной суп с картофелем и морковью',28,1,5,0],['Домашний овощной суп с капустой, картошкой и морковью',30,1,6,0],['Овощной суп с рисом, картофелем и морковью',37,1,7,0],['Овощной суп с вермишелью и картофелем',41,2,7,1],['Овощной суп с гречкой и картофелем',39,2,7,1],['Суп с фрикадельками, картофелем и морковью',54,4,5,2],['Суп с говядиной, картошкой и морковью',49,4,5,2],['Домашний суп на говяжьем бульоне с овощами',36,3,4,1],['Суп со свининой, картофелем и морковью',59,4,5,3],['Суп с индейкой, картофелем и морковью',39,5,5,1],['Суп с куриными сердечками, картошкой и морковью',44,4,5,1],['Суп с куриной печенью, картофелем и овощами',46,4,5,1],['Суп с домашними клёцками на курином бульоне',55,3,8,1],['Суп с макаронами, курицей и картофелем',49,4,7,1],['Куриный суп с овсяными хлопьями и овощами',37,4,4,1],['Суп-пюре из картофеля и моркови на курином бульоне',40,2,7,1],['Домашний куриный суп со сливочной заправкой',63,4,5,3]].map(([name,kcal,p,c,f])=>({name:String(name),category:'Домашние супы',kcal:Number(kcal),p:Number(p),c:Number(c),f:Number(f)})),
];

const fruitFoods:CatalogFood[]=[
 ['Мандарин',53,0.8,13.3,0.3],['Клементин',47,0.9,12,0.2],['Киви зелёный',61,1.1,14.7,0.5],['Киви золотой',60,1.1,14.7,0.5],
 ['Персик',39,0.9,9.5,0.3],['Нектарин',44,1.1,10.6,0.3],['Абрикос',48,1.4,11.1,0.4],['Слива',46,0.7,11.4,0.3],
 ['Черешня',63,1.1,16,0.2],['Вишня',50,1,12,0.3],['Клубника',32,0.7,7.7,0.3],['Малина',52,1.2,11.9,0.7],
 ['Черника',57,0.7,14.5,0.3],['Голубика',57,0.7,14.5,0.3],['Чёрная смородина',63,1.4,15.4,0.4],['Арбуз',30,0.6,7.6,0.2],
 ['Дыня',34,0.8,8.2,0.2],['Хурма',70,0.6,18.6,0.2],['Гранат',83,1.7,18.7,1.2],['Грейпфрут',42,0.8,10.7,0.1],
 ['Помело',38,0.8,9.6,0],['Ананас',50,0.5,13.1,0.1],['Манго',60,0.8,15,0.4],['Папайя',43,0.5,10.8,0.3],
 ['Лимон',29,1.1,9.3,0.3],['Лайм',30,0.7,10.5,0.2],['Яблоко зелёное',52,0.3,14,0.2],['Финики свежие',142,1.8,37,0.2],
].map(([name,kcal,p,c,f])=>({name:String(name),category:'Фрукты',kcal:Number(kcal),p:Number(p),c:Number(c),f:Number(f)}));

const driedFruitFoods:CatalogFood[]=[
 ['Курага',241,3.4,63,0.5],['Чернослив',240,2.2,64,0.4],['Изюм',299,3.1,79,0.5],['Финики сушёные',282,2.5,75,0.4],['Инжир сушёный',249,3.3,63,0.9],
].map(([name,kcal,p,c,f])=>({name:String(name),category:'Сухофрукты',kcal:Number(kcal),p:Number(p),c:Number(c),f:Number(f)}));

// Everyday foods that are easy to miss when the list is sorted by category.
// Values are rounded USDA FoodData Central reference values per 100 g, and the
// name states whether the food is raw, cooked, canned or ready to eat. They are
// approximate, not a medical recommendation.
export const commonFoods:CatalogFood[]=[
 {name:'Овсянка на воде — готовая',category:'Крупы и хлеб',kcal:71,p:2.5,c:12,f:1.5},
 {name:'Макароны — отварные',category:'Крупы и хлеб',kcal:131,p:5,c:25,f:1.1},
 {name:'Хлеб ржаной',category:'Крупы и хлеб',kcal:259,p:8.5,c:48,f:3.3},
 {name:'Куриная грудка — отварная',category:'Мясо и рыба',kcal:151,p:29,c:0,f:3},
 {name:'Индейка — запечённая',category:'Мясо и рыба',kcal:147,p:30,c:0,f:2.1},
 {name:'Говядина — тушёная',category:'Мясо и рыба',kcal:250,p:26,c:0,f:15},
 {name:'Свинина — запечённая',category:'Мясо и рыба',kcal:248,p:27,c:0,f:14.7},
 {name:'Лосось — запечённый',category:'Мясо и рыба',kcal:206,p:22,c:0,f:12},
 {name:'Тунец в воде',category:'Мясо и рыба',kcal:116,p:26,c:0,f:0.8},
 {name:'Йогурт натуральный 2%',category:'Молочное',kcal:73,p:3.5,c:4.7,f:2},
 {name:'Грецкие орехи',category:'Орехи',kcal:654,p:15,c:14,f:65},
];

export const expandedFoods:CatalogFood[]=[...meatFoods,...fishFoods,...extras,...soups,...homemadeSoups,...fruitFoods,...driedFruitFoods];



export const baseFoods:CatalogFood[]=[
 {name:'Куриная грудка',category:'Мясо и рыба',kcal:120,p:23,c:0,f:3},{name:'Индейка',category:'Мясо и рыба',kcal:135,p:29,c:0,f:2},{name:'Говядина постная',category:'Мясо и рыба',kcal:187,p:26,c:0,f:9},{name:'Лосось',category:'Мясо и рыба',kcal:208,p:20,c:0,f:13},{name:'Тунец',category:'Мясо и рыба',kcal:132,p:29,c:0,f:1},{name:'Яйцо',category:'Мясо и рыба',kcal:143,p:13,c:1,f:10},{name:'Творог 5%',category:'Молочное',kcal:121,p:17,c:3,f:5},{name:'Греческий йогурт',category:'Молочное',kcal:73,p:10,c:4,f:2},{name:'Молоко 2.5%',category:'Молочное',kcal:52,p:3,c:5,f:3},{name:'Сыр твёрдый',category:'Молочное',kcal:350,p:25,c:2,f:27},{name:'Рис сухой',category:'Крупы и хлеб',kcal:360,p:7,c:80,f:1},{name:'Гречка сухая',category:'Крупы и хлеб',kcal:343,p:13,c:72,f:3},{name:'Овсянка сухая',category:'Крупы и хлеб',kcal:370,p:13,c:62,f:7},{name:'Макароны сухие',category:'Крупы и хлеб',kcal:350,p:12,c:72,f:2},{name:'Хлеб цельнозерновой',category:'Крупы и хлеб',kcal:247,p:13,c:41,f:4},{name:'Картофель',category:'Овощи',kcal:77,p:2,c:17,f:0},{name:'Помидор',category:'Овощи',kcal:18,p:1,c:4,f:0},{name:'Огурец',category:'Овощи',kcal:15,p:1,c:4,f:0},{name:'Брокколи',category:'Овощи',kcal:34,p:3,c:7,f:0},{name:'Авокадо',category:'Овощи',kcal:160,p:2,c:9,f:15},{name:'Банан',category:'Фрукты',kcal:89,p:1,c:23,f:0},{name:'Яблоко',category:'Фрукты',kcal:52,p:0,c:14,f:0},{name:'Ягоды',category:'Фрукты',kcal:50,p:1,c:12,f:0},{name:'Оливковое масло',category:'Другое',kcal:884,p:0,c:0,f:100},{name:'Арахисовая паста',category:'Другое',kcal:588,p:25,c:20,f:50},{name:'Миндаль',category:'Другое',kcal:579,p:21,c:22,f:50},{name:'Мёд',category:'Другое',kcal:304,p:0,c:82,f:0},{name:'Шоколад тёмный',category:'Другое',kcal:546,p:5,c:61,f:31},
 {name:'Колбаса варёная',category:'Колбасы',kcal:260,p:12,c:2,f:23},{name:'Колбаса докторская',category:'Колбасы',kcal:257,p:13,c:1,f:22},{name:'Колбаса копчёная',category:'Колбасы',kcal:420,p:17,c:2,f:39},{name:'Салями',category:'Колбасы',kcal:420,p:22,c:1,f:36},{name:'Сосиски молочные',category:'Колбасы',kcal:266,p:11,c:2,f:24},{name:'Сардельки',category:'Колбасы',kcal:270,p:11,c:2,f:24},{name:'Ветчина',category:'Колбасы',kcal:145,p:16,c:2,f:6},{name:'Бекон',category:'Колбасы',kcal:450,p:15,c:1,f:43},{name:'Кефир 2.5%',category:'Молочное',kcal:53,p:3,c:4,f:3},{name:'Сметана 15%',category:'Молочное',kcal:162,p:3,c:4,f:15},{name:'Масло сливочное',category:'Молочное',kcal:748,p:1,c:1,f:82},{name:'Моцарелла',category:'Молочное',kcal:280,p:28,c:3,f:17},{name:'Ряженка 2.5%',category:'Молочное',kcal:54,p:3,c:4,f:3},{name:'Пельмени',category:'Готовые блюда',kcal:250,p:10,c:30,f:10},{name:'Вареники с картофелем',category:'Готовые блюда',kcal:180,p:5,c:35,f:3},{name:'Пицца',category:'Готовые блюда',kcal:266,p:11,c:33,f:10},{name:'Шаурма с курицей',category:'Готовые блюда',kcal:215,p:12,c:22,f:9},{name:'Суши с лососем',category:'Готовые блюда',kcal:170,p:8,c:25,f:4},{name:'Гранола',category:'Крупы и хлеб',kcal:450,p:10,c:64,f:16},{name:'Рис варёный',category:'Крупы и хлеб',kcal:130,p:3,c:28,f:0},{name:'Гречка варёная',category:'Крупы и хлеб',kcal:110,p:4,c:21,f:1},{name:'Лаваш',category:'Крупы и хлеб',kcal:275,p:9,c:56,f:1},{name:'Морковь',category:'Овощи',kcal:41,p:1,c:10,f:0},{name:'Перец сладкий',category:'Овощи',kcal:31,p:1,c:6,f:0},{name:'Лук репчатый',category:'Овощи',kcal:40,p:1,c:9,f:0},{name:'Грибы шампиньоны',category:'Овощи',kcal:22,p:3,c:3,f:0},{name:'Апельсин',category:'Фрукты',kcal:47,p:1,c:12,f:0},{name:'Груша',category:'Фрукты',kcal:57,p:0,c:15,f:0},{name:'Виноград',category:'Фрукты',kcal:69,p:1,c:18,f:0},{name:'Сок апельсиновый',category:'Напитки',kcal:45,p:1,c:10,f:0},{name:'Кола',category:'Напитки',kcal:42,p:0,c:11,f:0},{name:'Майонез',category:'Соусы',kcal:680,p:1,c:2,f:75},{name:'Кетчуп',category:'Соусы',kcal:100,p:1,c:24,f:0},{name:'Соевый соус',category:'Соусы',kcal:53,p:8,c:5,f:0},
];

// Prepared meals use documented composite recipes and rounded reference values.
// Every value is per 100 g of the ready dish; oil, milk and other ingredients are
// included in the estimate. These are deliberately approximate because recipes
// vary by household, cut of meat and portion size.
export const preparedMeals: CatalogFoodItem[] = enrichFoods([
  { name: 'Картофельное пюре на молоке', category: 'Гарниры', kcal: 90, p: 2, c: 14, f: 3, type: 'prepared_meal', preparationState: 'ready', approximate: true, source: 'recipe estimate' },
  { name: 'Картофельное пюре с маслом', category: 'Гарниры', kcal: 110, p: 2, c: 14, f: 5, type: 'prepared_meal', preparationState: 'ready', approximate: true, source: 'recipe estimate' },
  { name: 'Картофель по-деревенски', category: 'Гарниры', kcal: 160, p: 2, c: 25, f: 6, type: 'prepared_meal', preparationState: 'baked', approximate: true, source: 'recipe estimate' },
  { name: 'Овсяная каша на молоке', category: 'Завтраки', kcal: 110, p: 3, c: 17, f: 3, type: 'prepared_meal', preparationState: 'boiled', approximate: true, source: 'recipe estimate' },
  { name: 'Гречневая каша с молоком', category: 'Завтраки', kcal: 120, p: 4, c: 20, f: 3, type: 'prepared_meal', preparationState: 'boiled', approximate: true, source: 'recipe estimate' },
  { name: 'Овсянка с бананом', category: 'Завтраки', kcal: 110, p: 3, c: 20, f: 2, type: 'prepared_meal', preparationState: 'boiled', approximate: true, source: 'recipe estimate' },
  { name: 'Куриная грудка запечённая', category: 'Мясные блюда', kcal: 165, p: 31, c: 0, f: 3.6, type: 'prepared_meal', preparationState: 'baked', approximate: false, source: 'USDA FoodData Central' },
  { name: 'Куриное бедро запечённое', category: 'Мясные блюда', kcal: 209, p: 26, c: 0, f: 11, type: 'prepared_meal', preparationState: 'baked', approximate: false, source: 'USDA FoodData Central' },
  { name: 'Курица тушёная', category: 'Мясные блюда', kcal: 180, p: 24, c: 2, f: 8, type: 'prepared_meal', preparationState: 'stewed', approximate: true, source: 'recipe estimate' },
  { name: 'Котлеты куриные', category: 'Мясные блюда', kcal: 220, p: 18, c: 8, f: 12, type: 'prepared_meal', preparationState: 'fried', approximate: true, source: 'recipe estimate' },
  { name: 'Котлеты свиные', category: 'Мясные блюда', kcal: 280, p: 17, c: 6, f: 22, type: 'prepared_meal', preparationState: 'fried', approximate: true, source: 'recipe estimate' },
  { name: 'Котлеты говяжьи', category: 'Мясные блюда', kcal: 260, p: 18, c: 8, f: 20, type: 'prepared_meal', preparationState: 'fried', approximate: true, source: 'recipe estimate' },
  { name: 'Тефтели', category: 'Мясные блюда', kcal: 240, p: 16, c: 14, f: 12, type: 'prepared_meal', preparationState: 'stewed', approximate: true, source: 'recipe estimate' },
  { name: 'Гуляш', category: 'Мясные блюда', kcal: 190, p: 16, c: 8, f: 12, type: 'prepared_meal', preparationState: 'stewed', approximate: true, source: 'recipe estimate' },
  { name: 'Плов', category: 'Мясные блюда', kcal: 190, p: 7, c: 25, f: 7, type: 'prepared_meal', preparationState: 'ready', approximate: true, source: 'recipe estimate' },
  { name: 'Рыба запечённая', category: 'Рыбные блюда', kcal: 130, p: 20, c: 0, f: 5, type: 'prepared_meal', preparationState: 'baked', approximate: true, source: 'recipe estimate' },
  { name: 'Рыба жареная', category: 'Рыбные блюда', kcal: 200, p: 19, c: 5, f: 12, type: 'prepared_meal', preparationState: 'fried', approximate: true, source: 'recipe estimate' },
  { name: 'Рыба тушёная', category: 'Рыбные блюда', kcal: 130, p: 18, c: 3, f: 5, type: 'prepared_meal', preparationState: 'stewed', approximate: true, source: 'recipe estimate' },
  { name: 'Рыбные котлеты', category: 'Рыбные блюда', kcal: 190, p: 14, c: 8, f: 11, type: 'prepared_meal', preparationState: 'fried', approximate: true, source: 'recipe estimate' },
  { name: 'Рыба на пару', category: 'Рыбные блюда', kcal: 110, p: 21, c: 0, f: 2, type: 'prepared_meal', preparationState: 'steamed', approximate: true, source: 'recipe estimate' },
  { name: 'Борщ', category: 'Супы', kcal: 60, p: 3, c: 7, f: 2, type: 'prepared_meal', preparationState: 'ready', approximate: true, source: 'recipe estimate' },
  { name: 'Куриный суп', category: 'Супы', kcal: 50, p: 4, c: 5, f: 2, type: 'prepared_meal', preparationState: 'ready', approximate: true, source: 'recipe estimate' },
  { name: 'Суп с фрикадельками', category: 'Супы', kcal: 60, p: 4, c: 5, f: 2, type: 'prepared_meal', preparationState: 'ready', approximate: true, source: 'recipe estimate' },
  { name: 'Гороховый суп', category: 'Супы', kcal: 70, p: 4, c: 10, f: 2, type: 'prepared_meal', preparationState: 'ready', approximate: true, source: 'recipe estimate' },
  { name: 'Рассольник', category: 'Супы', kcal: 50, p: 3, c: 6, f: 2, type: 'prepared_meal', preparationState: 'ready', approximate: true, source: 'recipe estimate' },
  { name: 'Суп с лапшой', category: 'Супы', kcal: 55, p: 3, c: 7, f: 2, type: 'prepared_meal', preparationState: 'ready', approximate: true, source: 'recipe estimate' },
  { name: 'Солянка', category: 'Супы', kcal: 75, p: 5, c: 5, f: 4, type: 'prepared_meal', preparationState: 'ready', approximate: true, source: 'recipe estimate' },
  { name: 'Омлет', category: 'Завтраки', kcal: 154, p: 11, c: 1, f: 11, type: 'prepared_meal', preparationState: 'fried', approximate: true, source: 'USDA / recipe estimate' },
  { name: 'Яичница', category: 'Завтраки', kcal: 196, p: 14, c: 1, f: 15, type: 'prepared_meal', preparationState: 'fried', approximate: true, source: 'USDA / recipe estimate' },
  { name: 'Варёные яйца', category: 'Завтраки', kcal: 155, p: 13, c: 1, f: 11, type: 'prepared_meal', preparationState: 'boiled', approximate: false, source: 'USDA FoodData Central' },
  { name: 'Сырники', category: 'Завтраки', kcal: 220, p: 14, c: 24, f: 8, type: 'prepared_meal', preparationState: 'fried', approximate: true, source: 'recipe estimate' },
  { name: 'Блины', category: 'Завтраки', kcal: 230, p: 6, c: 30, f: 9, type: 'prepared_meal', preparationState: 'fried', approximate: true, source: 'recipe estimate' },
  { name: 'Оладьи', category: 'Завтраки', kcal: 250, p: 6, c: 34, f: 10, type: 'prepared_meal', preparationState: 'fried', approximate: true, source: 'recipe estimate' },
  { name: 'Творожная запеканка', category: 'Завтраки', kcal: 180, p: 12, c: 18, f: 7, type: 'prepared_meal', preparationState: 'baked', approximate: true, source: 'recipe estimate' },
  { name: 'Винегрет', category: 'Салаты', kcal: 90, p: 2, c: 12, f: 4, type: 'prepared_meal', preparationState: 'ready', approximate: true, source: 'recipe estimate' },
  { name: 'Оливье', category: 'Салаты', kcal: 200, p: 4, c: 12, f: 15, type: 'prepared_meal', preparationState: 'ready', approximate: true, source: 'recipe estimate' },
  { name: 'Греческий салат', category: 'Салаты', kcal: 130, p: 3, c: 5, f: 11, type: 'prepared_meal', preparationState: 'ready', approximate: true, source: 'recipe estimate' },
  { name: 'Салат из овощей', category: 'Салаты', kcal: 70, p: 1, c: 7, f: 4, type: 'prepared_meal', preparationState: 'ready', approximate: true, source: 'recipe estimate' },
  { name: 'Салат из капусты', category: 'Салаты', kcal: 60, p: 1, c: 7, f: 3, type: 'prepared_meal', preparationState: 'ready', approximate: true, source: 'recipe estimate' },
  { name: 'Тушёные овощи', category: 'Домашние блюда', kcal: 80, p: 2, c: 9, f: 4, type: 'prepared_meal', preparationState: 'stewed', approximate: true, source: 'recipe estimate' },
  { name: 'Овощное рагу', category: 'Домашние блюда', kcal: 75, p: 2, c: 10, f: 3, type: 'prepared_meal', preparationState: 'stewed', approximate: true, source: 'recipe estimate' },
  { name: 'Кабачковые оладьи', category: 'Домашние блюда', kcal: 150, p: 4, c: 15, f: 9, type: 'prepared_meal', preparationState: 'fried', approximate: true, source: 'recipe estimate' },
  { name: 'Голубцы', category: 'Домашние блюда', kcal: 120, p: 7, c: 10, f: 6, type: 'prepared_meal', preparationState: 'stewed', approximate: true, source: 'recipe estimate' },
  { name: 'Вареники с творогом', category: 'Домашние блюда', kcal: 220, p: 10, c: 28, f: 7, type: 'prepared_meal', preparationState: 'boiled', approximate: true, source: 'recipe estimate' },
  { name: 'Макароны с фаршем', category: 'Домашние блюда', kcal: 190, p: 9, c: 22, f: 8, type: 'prepared_meal', preparationState: 'ready', approximate: true, source: 'recipe estimate' },
  { name: 'Запеканка картофельная', category: 'Домашние блюда', kcal: 150, p: 5, c: 18, f: 6, type: 'prepared_meal', preparationState: 'baked', approximate: true, source: 'recipe estimate' },
  { name: 'Фаршированный перец', category: 'Домашние блюда', kcal: 110, p: 6, c: 10, f: 5, type: 'prepared_meal', preparationState: 'baked', approximate: true, source: 'recipe estimate' },
  { name: 'Гречка с мясом', category: 'Домашние блюда', kcal: 180, p: 10, c: 20, f: 7, type: 'prepared_meal', preparationState: 'ready', approximate: true, source: 'recipe estimate' },
  { name: 'Рис с курицей', category: 'Домашние блюда', kcal: 170, p: 9, c: 21, f: 5, type: 'prepared_meal', preparationState: 'ready', approximate: true, source: 'recipe estimate' },
  { name: 'Творог с фруктами', category: 'Домашние блюда', kcal: 130, p: 9, c: 15, f: 4, type: 'prepared_meal', preparationState: 'ready', approximate: true, source: 'recipe estimate' },
]);

export const allFoods: CatalogFoodItem[] = enrichFoods([
  ...preparedMeals,
  ...commonFoods,
  ...baseFoods,
  ...expandedFoods,
]);
