const translations: [string, string[]][] = [
 ['мандарин', ['mandarin', 'mandarins', 'tangerine', 'tangerines', 'clementine', 'clementines', 'мандарины', 'мандарини']],
 ['яблоко', ['apple', 'apples', 'яблуко', 'яблука']], ['банан', ['banana', 'bananas', 'банан', 'банани']],
 ['апельсин', ['orange', 'oranges', 'апельсин', 'апельсини']], ['груша', ['pear', 'pears', 'груша', 'груші']],
 ['виноград', ['grape', 'grapes', 'виноград', 'виноград']], ['киви', ['kiwi', 'kiwifruit', 'ківі', 'киви']],
 ['персик', ['peach', 'peaches', 'персик', 'персики']], ['нектарин', ['nectarine', 'nectarines', 'нектарин', 'нектарини']],
 ['абрикос', ['apricot', 'apricots', 'абрикос', 'абрикоси']], ['слива', ['plum', 'plums', 'слива', 'сливи']],
 ['черешня', ['sweet cherry', 'sweet cherries', 'cherry', 'cherries', 'черешня', 'черешні']],
 ['вишня', ['sour cherry', 'sour cherries', 'вишня', 'вишні']], ['клубника', ['strawberry', 'strawberries', 'полуниця', 'полуниці']],
 ['малина', ['raspberry', 'raspberries', 'малина', 'малини']], ['черника', ['bilberry', 'bilberries', 'черника', 'чорниця']],
 ['голубика', ['blueberry', 'blueberries', 'голубика', 'лохина']], ['смородина', ['currant', 'blackcurrant', 'black currant', 'смородина', 'смородина чорна']],
 ['арбуз', ['watermelon', 'watermelons', 'кавун', 'кавуни']], ['дыня', ['melon', 'melons', 'cantaloupe', 'диня', 'дині']],
 ['хурма', ['persimmon', 'persimmons', 'хурма', 'хурму']], ['гранат', ['pomegranate', 'pomegranates', 'гранат', 'гранат']],
 ['грейпфрут', ['grapefruit', 'grapefruits', 'грейпфрут', 'грейпфрути']], ['помело', ['pomelo', 'pomelos', 'помело']],
 ['ананас', ['pineapple', 'pineapples', 'ананас', 'ананаси']], ['манго', ['mango', 'mangoes', 'манго']],
 ['лимон', ['lemon', 'lemons', 'лимон', 'лимони']], ['лайм', ['lime', 'limes', 'лайм', 'лайми']],
 ['папайя', ['papaya', 'papayas', 'папая']], ['финики', ['date', 'dates', 'фінік', 'фініки']],
 ['куриная грудка', ['chicken breast', 'chicken fillet', 'куряча грудка', 'куряче філе']],
 ['курица', ['chicken', 'курка', 'курятина']], ['индейка', ['turkey', 'індичка']],
 ['говядина', ['beef', 'яловичина']], ['свинина', ['pork', 'свинина']], ['лосось', ['salmon', 'сьомга', 'лосось']],
 ['тунец', ['tuna', 'тунець']], ['яйцо', ['egg', 'eggs', 'яйце', 'яйця']], ['творог', ['cottage cheese', 'quark', 'сир кисломолочний']],
 ['йогурт', ['yogurt', 'yoghurt', 'йогурт']], ['молоко', ['milk', 'молоко']], ['сыр', ['cheese', 'сир']],
 ['рис', ['rice', 'рис']], ['гречка', ['buckwheat', 'гре́чка', 'гречка']], ['овсянка', ['oatmeal', 'porridge oats', 'вівсянка']],
 ['макароны', ['pasta', 'макарони', 'паста']], ['хлеб', ['bread', 'хліб']], ['картофель', ['potato', 'potatoes', 'картопля', 'картошка', 'картошку', 'картошки']],
 ['варёный', ['отварной', 'отварная', 'отварные', 'варёная', 'варёные', 'варёное', 'boiled']],
 ['запечённый', ['запечённая', 'запечённое', 'запечённые', 'запеченный', 'запеченная', 'baked']],
 ['жареный', ['жареная', 'жареное', 'жареные', 'fried']],
 ['тушёный', ['тушёная', 'тушёное', 'тушёные', 'тушеный', 'stewed']],
 ['готовый', ['готовая', 'готовое', 'готовые', 'ready']],
 ['помидор', ['tomato', 'tomatoes', 'томат', 'помідор', 'помідори']], ['огурец', ['cucumber', 'cucumbers', 'огірок', 'огірки']],
 ['морковь', ['carrot', 'carrots', 'морква']], ['лук', ['onion', 'onions', 'цибуля']], ['колбаса', ['sausage', 'sausages', 'ковбаса', 'ковбаса']],
 ['сосиски', ['frankfurter', 'frankfurters', 'sausages', 'сосиски']], ['суп', ['soup', 'суп']],
 ['апельсиновый сок', ['orange juice', 'апельсиновий сік']], ['ккал', ['calories', 'calorie', 'kcal', 'калорії']],
];

const normalize = (value: string) => value.toLocaleLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/[’']/g, '').replace(/ё/g, 'е').trim();
const aliasPairs = translations.flatMap(([canonical, aliases]) => aliases.map(alias => [normalize(alias), normalize(canonical)] as const)).sort((a, b) => b[0].length - a[0].length);
const tokenStems: Record<string, string[]> = {
 'курица': ['кур'], 'говядина': ['говяж', 'говядин'], 'свинина': ['свин'], 'картофель': ['картоф'],
 'варёный': ['вар'], 'вареный': ['вар'], 'вареныи': ['вар'], 'отварной': ['вар'], 'отварнои': ['вар'], 'запечённый': ['запеч'], 'запеченный': ['запеч'], 'запеченныи': ['запеч'], 'жареный': ['жарен'], 'жареныи': ['жарен'], 'тушёный': ['тушен'], 'тушеный': ['тушен'], 'тушеныи': ['тушен'], 'готовый': ['готов'], 'готовыи': ['готов'],
 'морковь': ['морков'], 'огурец': ['огур'], 'вишня': ['виш'], 'черешня': ['череш'], 'гречка': ['греч'],
 'овсянка': ['овсян'], 'яйцо': ['яйц'], 'клубника': ['клубник', 'полуниц'], 'голубика': ['голубик', 'лохин'],
 'смородина': ['смородин'], 'малина': ['малин'], 'персик': ['персик'], 'абрикос': ['абрикос'], 'слива': ['слив'],
 'арбуз': ['арбуз', 'кавун'], 'дыня': ['дын', 'дыня'], 'мандарины': ['мандарин', 'мандарин'],
 'мандарин': ['мандарин'], 'апельсин': ['апельсин'], 'яблоко': ['яблок'], 'банан': ['банан'], 'груша': ['груш'],
};

export function foodSearchMatches(name: string, category: string, rawQuery: string) {
 const query = normalize(rawQuery);
 if (!query) return true;
 const normalizedName = normalize(name), normalizedCategory = normalize(category);
 if (normalizedName.includes(query) || normalizedCategory.includes(query)) return true;
 let translated = ` ${query} `;
 for (const [alias, canonical] of aliasPairs) {
  if (translated.includes(` ${alias} `)) translated = translated.replaceAll(` ${alias} `, ` ${canonical} `);
 }
 const tokens = translated.split(/[^\p{L}\p{N}]+/u).filter(token => token.length > 1 && !['the', 'and', 'with', 'fresh', 'raw', 'of', 'a', 'с', 'и', 'на', 'для'].includes(token));
 return tokens.length > 0 && tokens.every(token => [token,...(tokenStems[token]||[])].some(stem => normalizedName.includes(stem) || normalizedCategory.includes(stem)));
}
