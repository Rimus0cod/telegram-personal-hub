import { useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ChevronLeft, ChevronRight, Plus, Search, Trash2, Utensils, X, ChefHat, Flame, Bookmark, Camera, Barcode, LoaderCircle, Download, Copy, Settings2 } from 'lucide-react';
import {
  allFoods,
  enrichFood,
  uniqueFoods,
  type CatalogFoodItem,
} from './food-catalog';
import { foodSearchMatches, mergeSearchFoods } from './food-search';
import { scaleFoodNutrition } from './nutrition-serving';
import PromptDialog from './PromptDialog';
import { buildCsv } from './csv';
import {
  DEFAULT_MACRO_PERCENTAGES,
  calculateMacroGrams,
  macroCalories,
  validateMacroPercentages,
  validateNutritionTargets,
  type MacroPercentages,
} from './nutrition-macros';
import './nutrition.css';

type Food=CatalogFoodItem;
type Ingredient=Food&{grams:number};
type Meal={id:number,day:string,meal_type:string,name:string,calories:number,protein:number,carbs:number,fat:number,ingredients:string,serving_grams:number};
type SavedRecipe={id:number,name:string,meal_type:string,ingredients:Ingredient[]};

const mealTypes=[['breakfast','Завтрак'],['lunch','Обед'],['dinner','Ужин'],['snack','Перекус']];
const monday=(d:Date)=>{const x=new Date(d);x.setHours(0,0,0,0);x.setDate(x.getDate()-((x.getDay()+6)%7));return x};
const iso=(d:Date)=>`${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
const fmt=(d:Date,options:Intl.DateTimeFormatOptions)=>d.toLocaleDateString('ru-RU',options);
function extractProductCode(raw:string){
 const text=raw.trim();
 const gs1=text.match(/(?:\(\s*01\s*\)|01)(\d{14})/);if(gs1?.[1])return gs1[1];
 try{const url=new URL(text);const match=url.pathname.match(/(?:product|item|gtin|ean)[/=: -]*(\d{8,14})/i)||url.search.match(/(?:gtin|ean|barcode|code)=(\d{8,14})/i);if(match?.[1])return match[1]}catch{}
 const labelled=text.match(/(?:gtin|ean|barcode|product)[^\d]{0,12}(\d{8,14})/i);if(labelled?.[1])return labelled[1];
 if(/^\d{8,14}$/.test(text))return text;
 const candidates=text.match(/\d{8,14}/g)||[];
 return candidates.sort((a,b)=>Math.abs(a.length-13)-Math.abs(b.length-13))[0]||'';
}
function macroProduct(product:any,source='каталоге'):Food|null{
 const n=product?.nutriments||{},kcal=Number(n['energy-kcal_100g']??(Number(n.energy_100g||0)/4.184));
 if(!product||!Number.isFinite(kcal)||kcal<0)return null;
 const name=[product.product_name_ru||product.product_name||product.name,product.brands].filter(Boolean).join(' · ');
 return enrichFood({name:name||product.code||'Продукт по штрихкоду',category:source,kcal:Math.round(kcal),p:Number(n.proteins_100g??product.protein??0),c:Number(n.carbohydrates_100g??product.carbs??0),f:Number(n.fat_100g??product.fat??0),type:'ingredient',source:source==='Каталог пользователей'||source==='Общий каталог'?'community':source==='Каталог'?'catalog':'openfoodfacts',fromCatalog:source==='Каталог',sourceId:product.code?String(product.code):undefined,barcode:product.code?String(product.code):undefined,brand:product.brands||undefined})
}
function mapCatalogItem(item:any):Food|null{
 if(!item)return null;
 return enrichFood({id:item.id,name:item.name||item.name_ru||item.name_uk||item.name_original,category:item.category||'Каталог',type:item.type==='prepared_meal'?'prepared_meal':'ingredient',kcal:Number(item.kcal)||0,p:Number(item.protein)||0,c:Number(item.carbs)||0,f:Number(item.fat)||0,servingSize:Number(item.serving_size)||100,servingUnit:item.serving_unit==='ml'?'ml':item.serving_unit==='piece'?'piece':'g',preparationState:item.preparation_state||undefined,approximate:Boolean(item.approximate),source:item.source||'catalog',sourceId:item.source_id||undefined,barcode:item.barcode||undefined,brand:item.brand||undefined,aliases:item.aliases||[],searchTerms:item.search_terms||[],servings:item.servings||[],fromCatalog:true});
}
async function recognizeNutritionLabel(file:File){ const w=window as any;
 if(!w.Tesseract){await new Promise<void>((resolve,reject)=>{const script=document.createElement('script');script.src='https://cdn.jsdelivr.net/npm/tesseract.js@5/dist/tesseract.min.js';script.onload=()=>resolve();script.onerror=()=>reject(new Error('Не удалось загрузить OCR. Проверьте интернет и введите цифры вручную.'));document.head.appendChild(script)})}
 const result=await w.Tesseract.recognize(file,'ukr+eng');return String(result.data.text||'');
}
function parseNutritionText(text:string){
 const lines=text.toLowerCase().replace(/,/g,'.').split(/\n+/);
 const find=(patterns:RegExp[])=>{for(const line of lines){if(patterns.some(p=>p.test(line))){const match=line.match(/(\d{1,4}(?:\.\d{1,2})?)/);if(match)return Number(match[1])}}return null};
 return {kcal:find([/ккал|kcal|калор|energy|енерг/]),protein:find([/белк|білк|protein|proteins/]),carbs:find([/углев|вуглев|carbohyd|carb/]),fat:find([/жир|fat|lipid/])};
}
export default function Nutrition({request,initData,setNotice}:{request:(path:string,options?:RequestInit)=>Promise<any>,initData:string,setNotice:(v:string)=>void}){
 const qc=useQueryClient(),today=new Date(),[weekOffset,setWeekOffset]=useState(0),[selected,setSelected]=useState(iso(today)),[target,setTarget]=useState(2000),[proteinTarget,setProteinTarget]=useState(120),[carbsTarget,setCarbsTarget]=useState(250),[fatTarget,setFatTarget]=useState(70),[search,setSearch]=useState(''),[remoteSearch,setRemoteSearch]=useState(''),[foodCategory,setFoodCategory]=useState('Все'),[mealType,setMealType]=useState('lunch'),[ingredients,setIngredients]=useState<Ingredient[]>([]),[grams,setGrams]=useState(100),[composer,setComposer]=useState(false),[mealName,setMealName]=useState(''),[customName,setCustomName]=useState(''),[customKcal,setCustomKcal]=useState(''),[portionDraft,setPortionDraft]=useState<Record<number,string>>({}),[recipePortions,setRecipePortions]=useState<Record<number,string>>({}),[copyDayOpen,setCopyDayOpen]=useState(false),[copyDayError,setCopyDayError]=useState('');
 const [macroPercents,setMacroPercents]=useState<MacroPercentages>(DEFAULT_MACRO_PERCENTAGES);
 const [autoMacros,setAutoMacros]=useState(true);
 const [addOpen,setAddOpen]=useState(false);
 const [foodTypeFilter,setFoodTypeFilter]=useState<'all'|'prepared_meal'|'ingredient'>('all');
 const [quickFood,setQuickFood]=useState<Food|null>(null);
 useEffect(()=>{localStorage.removeItem('calorie-target')},[]);
 const videoRef=useRef<HTMLVideoElement>(null),[scannerOpen,setScannerOpen]=useState(false),[barcode,setBarcode]=useState(''),[barcodeBusy,setBarcodeBusy]=useState(false),[barcodeMessage,setBarcodeMessage]=useState(''),[extraFoods,setExtraFoods]=useState<Food[]>([]),[quickGrams,setQuickGrams]=useState(100);
 const [addProductOpen,setAddProductOpen]=useState(false),[productName,setProductName]=useState(''),[productBrand,setProductBrand]=useState(''),[productMacros,setProductMacros]=useState({kcal:'',protein:'',carbs:'',fat:''}),[nutritionPhoto,setNutritionPhoto]=useState<File|null>(null),[frontPhoto,setFrontPhoto]=useState<File|null>(null),[ocrText,setOcrText]=useState(''),[ocrBusy,setOcrBusy]=useState(false);
 const start=monday(today);start.setDate(start.getDate()+weekOffset*7);const end=new Date(start);end.setDate(end.getDate()+6);const days=Array.from({length:7},(_,i)=>{const d=new Date(start);d.setDate(d.getDate()+i);return d});
 const data=useQuery({queryKey:['nutrition',iso(start)],queryFn:()=>request(`/nutrition?from=${iso(start)}&to=${iso(end)}`) as Promise<Meal[]>,enabled:!!initData});
 const profile=useQuery({queryKey:['me'],queryFn:()=>request('/auth/me'),enabled:!!initData});
 useEffect(()=>{if(profile.data){setTarget(profile.data.calorie_target||2000);setProteinTarget(profile.data.protein_target||120);setCarbsTarget(profile.data.carbs_target||250);setFatTarget(profile.data.fat_target||70)}},[profile.data]);
 const saveTarget=useMutation({mutationFn:(payload:any)=>request('/auth/preferences',{method:'PATCH',body:JSON.stringify(payload)}),onSuccess:()=>qc.invalidateQueries({queryKey:['me']}),onError:(e:any)=>setNotice(`Не удалось сохранить цели: ${e.message}`)});
 const saved=useQuery({queryKey:['nutrition-recipes'],queryFn:()=>request('/nutrition/recipes') as Promise<SavedRecipe[]>,enabled:!!initData});
 useEffect(()=>{const timer=setTimeout(()=>setRemoteSearch(search.trim()),450);return()=>clearTimeout(timer)},[search]);
 const catalogSearch=useQuery({queryKey:['food-catalog-search',remoteSearch],queryFn:()=>request(`/nutrition/catalog-search?q=${encodeURIComponent(remoteSearch)}`) as Promise<any[]>,enabled:!!initData&&remoteSearch.length>=2,staleTime:60000,retry:false});
 const rawLanguage=((navigator.language||'ru').split('-')[0]||'ru');
 const uiLanguage=rawLanguage.length>=2?rawLanguage:'ru';
 const catalogItems=useQuery({queryKey:['catalog-items',uiLanguage],queryFn:()=>request(`/nutrition/catalog-items?lang=${encodeURIComponent(uiLanguage)}`) as Promise<{items:any[]}>,enabled:!!initData,staleTime:60000,retry:false});
 const serverSearch=useQuery({queryKey:['catalog-search-db',remoteSearch,uiLanguage],queryFn:()=>request(`/nutrition/catalog/search?q=${encodeURIComponent(remoteSearch)}&lang=${encodeURIComponent(uiLanguage)}&limit=50`) as Promise<{items:any[]}>,enabled:!!initData&&remoteSearch.length>=2,staleTime:30000,retry:false});
 const serverFoods=useMemo(()=>((serverSearch.data?.items||[]).map(mapCatalogItem).filter(Boolean) as Food[]),[serverSearch.data]);
 const add=useMutation({mutationFn:(meal:any)=>request('/nutrition',{method:'POST',body:JSON.stringify(meal)}),onSuccess:()=>{qc.invalidateQueries({queryKey:['nutrition']});setComposer(false);setAddOpen(false);setQuickFood(null);setIngredients([]);setMealName('');setSearch('');setNotice('Добавлено в дневник')},onError:(e:any)=>setNotice(`Не удалось сохранить блюдо: ${e.message}`)});
 const remove=useMutation({mutationFn:(id:number)=>request(`/nutrition/${id}`,{method:'DELETE'}),onSuccess:()=>qc.invalidateQueries({queryKey:['nutrition']})});
 const saveSavedRecipe=useMutation({mutationFn:(payload:any)=>request('/nutrition/recipes',{method:'POST',body:JSON.stringify(payload)}),onSuccess:()=>{qc.invalidateQueries({queryKey:['nutrition-recipes']});setNotice('Рецепт сохранён — он появится в «Мои блюда»')},onError:(e:any)=>setNotice(`Не удалось сохранить рецепт: ${e.message}`)});
 const deleteSavedRecipe=useMutation({mutationFn:(id:number)=>request(`/nutrition/recipes/${id}`,{method:'DELETE'}),onSuccess:()=>qc.invalidateQueries({queryKey:['nutrition-recipes']})});
 const updatePortion=useMutation({mutationFn:({id,serving_grams}:any)=>request(`/nutrition/${id}`,{method:'PATCH',body:JSON.stringify({serving_grams})}),onSuccess:()=>qc.invalidateQueries({queryKey:['nutrition']})});
 const copyDay=useMutation({mutationFn:(target_day:string)=>request('/nutrition/copy-day',{method:'POST',body:JSON.stringify({source_day:selected,target_day})}),onSuccess:(result:any)=>{qc.invalidateQueries({queryKey:['nutrition']});setNotice(`Скопировано блюд: ${result.copied}`);setCopyDayOpen(false);setCopyDayError('')},onError:(e:any)=>{setCopyDayError(e.message);setNotice(`Не удалось скопировать день: ${e.message}`)}});
 const dayMeals=(data.data||[]).filter(m=>m.day===selected),savedRecipes=saved.data||[],totals=dayMeals.reduce((a,m)=>({calories:a.calories+m.calories,protein:a.protein+m.protein,carbs:a.carbs+m.carbs,fat:a.fat+m.fat}),{calories:0,protein:0,carbs:0,fat:0});
 const remoteFoods=useMemo(()=>((catalogSearch.data||[]).map(p=>macroProduct(p,'Open Food Facts')).filter(Boolean) as Food[]),[catalogSearch.data]);
 const dbFoods=useMemo(()=>((catalogItems.data?.items||[]).map(mapCatalogItem).filter(Boolean) as Food[]),[catalogItems.data]);
 const catalogFoods=useMemo(()=>uniqueFoods([...dbFoods,...extraFoods,...allFoods]),[dbFoods,extraFoods]);
 const foodCategories=useMemo(()=>['Все',...new Set([...remoteFoods,...catalogFoods].filter(f=>foodTypeFilter==='all'||f.type===foodTypeFilter).map(f=>f.category))],[remoteFoods,catalogFoods,foodTypeFilter]);
 const serverSearchActive=remoteSearch.length>=2&&!!serverSearch.data&&!serverSearch.isError&&serverSearch.data.items.length>0;
 const filtered=useMemo(()=>mergeSearchFoods(serverSearchActive,serverFoods,remoteFoods,catalogFoods).filter(f=>(foodTypeFilter==='all'||f.type===foodTypeFilter)&&(foodCategory==='Все'||f.category===foodCategory)&&(serverSearchActive||foodSearchMatches(f.name,f.category,search,f.searchTerms))),[search,serverSearchActive,serverFoods,remoteFoods,catalogFoods,foodCategory,foodTypeFilter]);
 const recipe=ingredients.reduce((a,f)=>({calories:a.calories+f.kcal*f.grams/100,protein:a.protein+f.p*f.grams/100,carbs:a.carbs+f.c*f.grams/100,fat:a.fat+f.f*f.grams/100}),{calories:0,protein:0,carbs:0,fat:0});
 function chooseFood(food:Food){setIngredients(x=>[...x,{...food,grams:Math.max(1,grams)}]);setSearch('')}
 async function lookupBarcode(code=barcode){const normalized=extractProductCode(code);if(!normalized){setBarcodeMessage('Не удалось извлечь код товара из QR. Введите цифры со штрихкода вручную.');return}setBarcode(normalized);setBarcodeBusy(true);setBarcodeMessage(`Код ${normalized}: ищем в общем каталоге и Open Food Facts…`);try{const result=await request(`/nutrition/catalog/${encodeURIComponent(normalized)}`);if(!result.found){setBarcodeMessage(result.weighted?`Весовой код ${normalized}: товар не найден. Артикул весового товара — ${result.barcode_prefix}. Добавьте его по фото упаковки.`:`Код ${normalized} не найден. Добавьте продукт по фото упаковки и таблице КБЖУ.`);setProductName('');setProductBrand('');setProductMacros({kcal:'',protein:'',carbs:'',fat:''});setAddProductOpen(true);return}const food=macroProduct(result.product,result.source==='community'?'Каталог пользователей':result.source==='catalog'?'Каталог':'Open Food Facts');if(!food){setBarcodeMessage('Товар найден, но в карточке нет калорийности. Добавьте значения с этикетки.');setProductName(result.product?.product_name||'');setAddProductOpen(true);return}setExtraFoods(x=>[food,...x.filter(item=>item.name!==food.name)]);setQuickFood(food);setAddOpen(true);setComposer(false);setNotice(`Найдено: ${food.name}`)}catch(e:any){setBarcodeMessage(e.message||'Не удалось найти продукт')}finally{setBarcodeBusy(false)}}
 async function runOcr(file:File|null){if(!file)return;setOcrBusy(true);setBarcodeMessage('Распознаём таблицу КБЖУ…');try{const text=await recognizeNutritionLabel(file);setOcrText(text);const values=parseNutritionText(text);setProductMacros(v=>({kcal:values.kcal==null?v.kcal:String(values.kcal),protein:values.protein==null?v.protein:String(values.protein),carbs:values.carbs==null?v.carbs:String(values.carbs),fat:values.fat==null?v.fat:String(values.fat)}));setBarcodeMessage('Распознали текст. Сверьте каждое значение с этикеткой перед сохранением.')}catch(e:any){setBarcodeMessage(e.message||'OCR не сработал; заполните значения вручную.')}finally{setOcrBusy(false)}}
 async function saveCommunityFood(){const vals=Object.fromEntries(Object.entries(productMacros).map(([k,v])=>[k,Number(v)]));if(!barcode||!productName.trim()||Object.values(vals).some(v=>!Number.isFinite(v)||v<0)){setBarcodeMessage('Укажите штрихкод, название и корректные КБЖУ на 100 г.');return}setBarcodeBusy(true);try{const result=await request('/nutrition/catalog',{method:'POST',body:JSON.stringify({barcode,name:productName.trim(),brand:productBrand.trim(),...vals})});const food=macroProduct(result.product,'Общий каталог');if(food){setExtraFoods(x=>[food,...x.filter(item=>item.name!==food.name)]);setQuickFood(food);setAddOpen(true);setComposer(false);qc.invalidateQueries({queryKey:['catalog-items']})}setNotice('Продукт сохранён в каталог')}catch(e:any){setBarcodeMessage(e.message||'Не удалось сохранить продукт')}finally{setBarcodeBusy(false)}}
 useEffect(()=>{if(!scannerOpen)return;let stopped=false,stream:MediaStream|undefined,raf=0;const start=async()=>{try{const Detector=(window as any).BarcodeDetector;if(!Detector){setBarcodeMessage('Камера этого устройства не распознаёт штрихкоды. Введите код вручную ниже.');return}stream=await navigator.mediaDevices.getUserMedia({video:{facingMode:{ideal:'environment'}},audio:false});if(stopped){stream.getTracks().forEach(t=>t.stop());return}const video=videoRef.current;if(!video)return;video.srcObject=stream;await video.play();const detector=new Detector({formats:['ean_13','ean_8','upc_a','upc_e','qr_code']});const scan=async()=>{if(stopped)return;try{const codes=await detector.detect(video);if(codes.length){const found=codes[0].rawValue;setBarcode(found);setScannerOpen(false);lookupBarcode(found);return}}catch{}raf=requestAnimationFrame(scan)};scan()}catch{setBarcodeMessage('Не удалось открыть камеру. Разрешите доступ или введите штрихкод вручную.')}};start();return()=>{stopped=true;cancelAnimationFrame(raf);stream?.getTracks().forEach(t=>t.stop())}},[scannerOpen]);
 useEffect(()=>{if(!addOpen&&!composer)return;const handleKey=(event:KeyboardEvent)=>{if(event.key==='Escape'){setAddOpen(false);setComposer(false);setScannerOpen(false);setQuickFood(null)}};window.addEventListener('keydown',handleKey);return()=>window.removeEventListener('keydown',handleKey)},[addOpen,composer]);
 function saveRecipeToDay(){if(!mealName.trim()||!ingredients.length){setNotice('Добавьте название и ингредиенты блюда');return}add.mutate({day:selected,meal_type:mealType,name:mealName.trim(),calories:Math.round(recipe.calories),protein:+recipe.protein.toFixed(1),carbs:+recipe.carbs.toFixed(1),fat:+recipe.fat.toFixed(1),ingredients:ingredients.map(i=>`${i.name} — ${i.grams} г`).join(', '),serving_grams:ingredients.reduce((s,i)=>s+i.grams,0)})}
 function saveRecipe(){if(!mealName.trim()||!ingredients.length){setNotice('Добавьте название и ингредиенты блюда');return}saveSavedRecipe.mutate({name:mealName.trim(),meal_type:mealType,ingredients:ingredients.map(({name,category,kcal,p,c,f,grams})=>({name,category,kcal,p,c,f,grams}))})}
 function quickAdd(food:Food,grams=quickGrams){const scaled=scaleFoodNutrition(food,grams);add.mutate({day:selected,meal_type:mealType,name:food.name,calories:scaled.calories,protein:scaled.protein,carbs:scaled.carbs,fat:scaled.fat,ingredients:`${grams} г`,serving_grams:grams})}
 async function persistExternalFood(food:Food):Promise<Food|null>{
  if(!food.barcode||food.fromCatalog||food.source==='community'||food.source==='builtin')return food;
  try{
   await request('/nutrition/catalog',{method:'POST',body:JSON.stringify({barcode:food.barcode,name:food.name,brand:food.brand||'',kcal:food.kcal,protein:food.p,carbs:food.c,fat:food.f})});
   qc.invalidateQueries({queryKey:['catalog-items']});
   return food;
  }catch(e:any){
   if(e.status===409){
    qc.invalidateQueries({queryKey:['catalog-items']});
    try{
     const existing=await request(`/nutrition/catalog/${encodeURIComponent(food.barcode)}`);
     const resolved=existing?.found?macroProduct(existing.product,existing.source==='community'?'Каталог пользователей':existing.source==='catalog'?'Каталог':'Open Food Facts'):null;
     if(resolved){setNotice('Этот продукт уже сохранён другим пользователем — используются значения из общего каталога.');return resolved}
    }catch{}
    setNotice('Этот продукт уже сохранён другим пользователем. Не удалось загрузить сохранённые значения, попробуйте ещё раз.');
    return null;
   }
   setNotice(`Не удалось сохранить продукт в каталог: ${e.message}`);
   return null;
  }
 }
 async function confirmQuickFood(food:Food,grams:number){const resolved=await persistExternalFood(food);if(resolved)quickAdd(resolved,grams)}
 function addSaved(r:SavedRecipe){const base=r.ingredients.reduce((s,i)=>s+i.grams,0),portion=Number(recipePortions[r.id]||base),ratio=portion/Math.max(1,base),sum=r.ingredients.reduce((a,i)=>({calories:a.calories+i.kcal*i.grams/100,protein:a.protein+i.p*i.grams/100,carbs:a.carbs+i.c*i.grams/100,fat:a.fat+i.f*i.grams/100}),{calories:0,protein:0,carbs:0,fat:0});add.mutate({day:selected,meal_type:r.meal_type,name:r.name,calories:Math.round(sum.calories*ratio),protein:+(sum.protein*ratio).toFixed(1),carbs:+(sum.carbs*ratio).toFixed(1),fat:+(sum.fat*ratio).toFixed(1),ingredients:r.ingredients.map(i=>`${i.name} — ${Math.round(i.grams*ratio)} г`).join(', '),serving_grams:portion})}
 function exportCsv(){const rows=[['Дата','Приём пищи','Блюдо','Граммы','Ккал','Белки, г','Углеводы, г','Жиры, г','Состав'],...((data.data||[]).map(m=>[m.day,mealTypes.find(([t])=>t===m.meal_type)?.[1]||m.meal_type,m.name,m.serving_grams,m.calories,m.protein,m.carbs,m.fat,m.ingredients||'']))];const csv=buildCsv(rows);const url=URL.createObjectURL(new Blob([csv],{type:'text/csv;charset=utf-8'}));const a=document.createElement('a');a.href=url;a.download=`nutrition-${iso(start)}-${iso(end)}.csv`;a.click();URL.revokeObjectURL(url)}
 const macroPercentError=validateMacroPercentages(macroPercents);
 const targetError=validateNutritionTargets({calories:target,protein:proteinTarget,carbs:carbsTarget,fat:fatTarget});
 const macroCaloriesTotal=macroCalories({protein:proteinTarget,carbs:carbsTarget,fat:fatTarget});
 function updateCalorieTarget(value:number){setTarget(value);if(autoMacros){const calculated=calculateMacroGrams(value,macroPercents);if(calculated){setProteinTarget(calculated.protein);setCarbsTarget(calculated.carbs);setFatTarget(calculated.fat)}}}
 function updateMacroPercent(key:keyof MacroPercentages,value:number){const next={...macroPercents,[key]:value};setMacroPercents(next);if(autoMacros){const calculated=calculateMacroGrams(target,next);if(calculated){setProteinTarget(calculated.protein);setCarbsTarget(calculated.carbs);setFatTarget(calculated.fat)}}}
 function toggleAutoMacros(enabled:boolean){setAutoMacros(enabled);if(enabled){const calculated=calculateMacroGrams(target,macroPercents);if(calculated){setProteinTarget(calculated.protein);setCarbsTarget(calculated.carbs);setFatTarget(calculated.fat)}}}
 function updateMacroTarget(setter:(value:number)=>void,value:number){setter(value);setAutoMacros(false)}
 function persistTargets(){if(targetError){setNotice(targetError);return}saveTarget.mutate({calorie_target:target,protein_target:proteinTarget,carbs_target:carbsTarget,fat_target:fatTarget})}
 return <section className="nutrition">
  <div className="nutrition-top">
   <div className="week-nav">
    <button className="icon" onClick={()=>setWeekOffset(v=>v-1)} aria-label="Предыдущая неделя"><ChevronLeft size={18}/></button>
    <button className="week-label" onClick={()=>{setWeekOffset(0);setSelected(iso(today))}}>
     <b>{fmt(start,{day:'numeric',month:'short'})} — {fmt(end,{day:'numeric',month:'short'})}</b>
     <small>{weekOffset===0?'Эта неделя':'К текущей неделе'}</small>
    </button>
    <button className="icon" onClick={()=>setWeekOffset(v=>v+1)} aria-label="Следующая неделя"><ChevronRight size={18}/></button>
   </div>
   <div className="nutrition-days">{days.map(d=>{const key=iso(d),entries=(data.data||[]).filter(m=>m.day===key);return <button key={key} className={`nutrition-day ${selected===key?'active':''} ${entries.length?'filled':''}`} onClick={()=>setSelected(key)} aria-label={fmt(d,{weekday:'long',day:'numeric',month:'long'})}><small>{fmt(d,{weekday:'short'}).slice(0,2)}</small><b>{d.getDate()}</b>{entries.length>0&&<i/>}</button>})}</div>
  </div>

  <div className="nutrition-summary">
   <div className="calorie-total"><Flame size={18}/><b>{totals.calories}</b><small>из {target} ккал</small></div>
   <div className="macro-bars">{([['Белки',totals.protein,proteinTarget],['Углеводы',totals.carbs,carbsTarget],['Жиры',totals.fat,fatTarget]] as [string,number,number][]).map(([label,value,goal])=><div key={label}><div><span>{label}</span><b>{Math.round(value)} / {goal} г</b></div><i className="macro-progress"><i style={{width:`${Math.min(100,value/Math.max(1,goal)*100)}%`}}/></i></div>)}</div>
  </div>

  <button className="primary-button add-food-button" onClick={()=>{setQuickFood(null);setAddOpen(true)}}><Plus size={18}/>Добавить еду</button>

  <details className="settings-panel">
   <summary><Settings2 size={16}/>Цели и БЖУ<span>{target} ккал</span></summary>
   <div className="settings-body">
    <div className="goal-inputs"><label><input type="number" min="500" max="10000" value={target} aria-label="Цель ккал" onChange={e=>updateCalorieTarget(Number(e.target.value))} onBlur={persistTargets}/>ккал</label><label><input type="number" min="0" max="1000" value={proteinTarget} aria-label="Цель Б" onChange={e=>updateMacroTarget(setProteinTarget,Number(e.target.value))} onBlur={persistTargets}/>Б</label><label><input type="number" min="0" max="1500" value={carbsTarget} aria-label="Цель У" onChange={e=>updateMacroTarget(setCarbsTarget,Number(e.target.value))} onBlur={persistTargets}/>У</label><label><input type="number" min="0" max="500" value={fatTarget} aria-label="Цель Ж" onChange={e=>updateMacroTarget(setFatTarget,Number(e.target.value))} onBlur={persistTargets}/>Ж</label></div>
    <div className="macro-calculator"><label className="macro-auto"><input type="checkbox" checked={autoMacros} onChange={e=>toggleAutoMacros(e.target.checked)}/>Автоматически по процентам</label><div className="macro-percents"><label>Б <input type="number" min="0" max="100" value={macroPercents.protein} aria-label="Процент белков" onChange={e=>updateMacroPercent('protein',Number(e.target.value))}/>%</label><label>У <input type="number" min="0" max="100" value={macroPercents.carbs} aria-label="Процент углеводов" onChange={e=>updateMacroPercent('carbs',Number(e.target.value))}/>%</label><label>Ж <input type="number" min="0" max="100" value={macroPercents.fat} aria-label="Процент жиров" onChange={e=>updateMacroPercent('fat',Number(e.target.value))}/>%</label></div><small>4/4/9 ккал на грамм · ориентир, не медицинская норма</small><div className={`macro-total ${Math.abs(macroCaloriesTotal-target)>25?'warning':''}`}>Из БЖУ: <b>{Math.round(macroCaloriesTotal)} ккал</b></div>{(targetError||macroPercentError)&&<div className="error">{targetError||macroPercentError}</div>}</div>
   </div>
  </details>

  {savedRecipes.length>0&&<details className="settings-panel saved-recipes"><summary><Bookmark size={16}/>Мои блюда<span>{savedRecipes.length}</span></summary><div className="saved-list">{savedRecipes.map(r=>{const base=r.ingredients.reduce((s,i)=>s+i.grams,0),portion=Number(recipePortions[r.id]||base),ratio=portion/Math.max(1,base),kcal=r.ingredients.reduce((s,i)=>s+i.kcal*i.grams/100,0)*ratio;return <article key={r.id}><div><b>{r.name}</b><small>{r.ingredients.map(i=>i.name).join(' · ')}</small></div><label><input type="number" min="1" value={portion} onChange={e=>setRecipePortions(v=>({...v,[r.id]:e.target.value}))}/> г</label><strong>{Math.round(kcal)} ккал</strong><button onClick={()=>addSaved(r)} aria-label="Добавить блюдо"><Plus size={16}/></button><button className="saved-delete" onClick={()=>deleteSavedRecipe.mutate(r.id)} aria-label="Удалить рецепт"><Trash2 size={14}/></button></article>})}</div></details>}

  <div className="log-head"><div><h2>Рацион</h2><small>{dayMeals.length?`${dayMeals.length} блюд · ${totals.calories} ккал`:'Пока пусто'}</small></div><div className="log-actions"><button onClick={exportCsv} disabled={!data.data?.length} aria-label="Экспорт CSV" title="Экспорт CSV"><Download size={16}/></button><button onClick={()=>{setCopyDayError('');setCopyDayOpen(true)}} disabled={!dayMeals.length||copyDay.isPending} aria-label="Копировать день" title="Копировать день"><Copy size={16}/></button></div></div>

  {data.isLoading&&<p className="nutrition-empty">Загружаем дневник…</p>}{data.isError&&<p className="error">Не удалось загрузить питание: {(data.error as Error).message}</p>}
  {dayMeals.length>0?<div className="meal-groups">{mealTypes.map(([type,label])=>{const meals=dayMeals.filter(m=>m.meal_type===type);if(!meals.length)return null;return <div className="meal-group" key={type}><h3>{label}<span>{meals.reduce((s,m)=>s+m.calories,0)} ккал</span><button className="icon" onClick={()=>{setMealType(type);setQuickFood(null);setAddOpen(true)}} aria-label={`Добавить в ${label}`}><Plus size={15}/></button></h3>{meals.map(m=><article className="meal-entry" key={m.id}><div><b>{m.name}</b><small className="meal-macros">Б {Math.round(m.protein)} · У {Math.round(m.carbs)} · Ж {Math.round(m.fat)} г</small>{m.ingredients&&<small className="meal-ingredients">{m.ingredients}</small>}</div><span className="meal-kcal">{m.calories} ккал</span><label className="meal-portion"><input type="number" min="1" value={portionDraft[m.id]??m.serving_grams} onChange={e=>setPortionDraft(v=>({...v,[m.id]:e.target.value}))} onBlur={e=>{const n=Number(e.target.value);if(n>0&&n!==m.serving_grams)updatePortion.mutate({id:m.id,serving_grams:n})}}/><small>г</small></label><button className="icon" onClick={()=>remove.mutate(m.id)} aria-label="Удалить"><Trash2 size={14}/></button></article>)}</div>})}</div>:!data.isLoading&&<div className="nutrition-empty"><Utensils size={22}/><b>День пока пустой</b><span>Добавьте готовое блюдо или продукт.</span></div>}

  {addOpen&&<div className="sheet-backdrop" onClick={()=>{setAddOpen(false);setQuickFood(null)}}><section className="sheet food-sheet" role="dialog" aria-modal="true" aria-label="Добавить еду" onClick={e=>e.stopPropagation()}><div className="sheet-head"><div><b>Добавить еду</b><small>{mealTypes.find(([v])=>v===mealType)?.[1]}</small></div><button className="icon" onClick={()=>{setAddOpen(false);setQuickFood(null)}} aria-label="Закрыть"><X size={19}/></button></div><div className="sheet-body"><label className="nutrition-search"><Search size={17}/><input value={search} onChange={e=>setSearch(e.target.value)} placeholder="Найти блюдо или продукт" autoFocus/>{search&&<button type="button" onClick={()=>setSearch('')} aria-label="Очистить поиск"><X size={15}/></button>}</label><div className="segmented food-type-filter">{[['all','Все'],['prepared_meal','Блюда'],['ingredient','Продукты']].map(([value,label])=><button key={value} className={foodTypeFilter===value?'active':''} onClick={()=>{setFoodTypeFilter(value as typeof foodTypeFilter);setFoodCategory('Все')}}>{label}</button>)}</div><div className="meal-type-chips">{mealTypes.map(([value,label])=><button key={value} className={mealType===value?'active':''} onClick={()=>setMealType(value)}>{label}</button>)}</div><div className="food-categories">{foodCategories.map(category=><button key={category} className={foodCategory===category?'active':''} onClick={()=>setFoodCategory(category)}>{category}</button>)}</div><div className="food-results">{filtered.slice(0,40).map(f=><button key={f.id} className={quickFood?.id===f.id?'selected':''} onClick={()=>{setQuickFood(f);setQuickGrams(100)}}><span><b>{f.name}</b><small><em>{f.type==='prepared_meal'?'Блюдо':'Продукт'}</em> · {f.kcal} ккал · Б {f.p} · У {f.c} · Ж {f.f} г / 100 г</small></span><Plus size={16}/></button>)}{!filtered.length&&<p>Ничего не найдено. Попробуйте другое название.</p>}</div></div>{quickFood&&<div className="food-preview"><div><b>{quickFood.name}</b><small>{quickFood.type==='prepared_meal'?'Готовое блюдо':'Продукт'} · {quickFood.kcal} ккал / 100 г</small></div><label><input type="number" min="1" value={quickGrams} onChange={e=>setQuickGrams(Number(e.target.value))} aria-label="Вес порции"/> г</label>{quickFood.servings&&quickFood.servings.length>0&&<div className="serving-chips">{quickFood.servings.slice(0,4).map((s:any)=><button type="button" key={`${s.unit}-${s.grams}`} className={Math.round(Number(s.grams)||100)===Math.round(quickGrams)?'active':''} onClick={()=>setQuickGrams(Math.max(1,Math.round(Number(s.grams)||100)))}>{s.description||`${s.amount} ${s.unit}`}</button>)}</div>}<div className="preview-macros"><span>{scaleFoodNutrition(quickFood,quickGrams).calories} ккал</span><span>Б {scaleFoodNutrition(quickFood,quickGrams).protein}</span><span>У {scaleFoodNutrition(quickFood,quickGrams).carbs}</span><span>Ж {scaleFoodNutrition(quickFood,quickGrams).fat}</span></div><button className="primary-button" onClick={()=>confirmQuickFood(quickFood,quickGrams)} disabled={add.isPending}>{add.isPending?'Добавляем…':'Добавить'}</button></div>}<div className="sheet-actions"><button className="secondary-button" onClick={()=>{setAddOpen(false);setComposer(true)}}><ChefHat size={16}/>Собрать блюдо</button><button className="secondary-button" onClick={()=>{setAddOpen(false);setComposer(true);setScannerOpen(true)}}><Barcode size={16}/>По штрихкоду</button></div></section></div>}

  {composer&&<div className="sheet-backdrop" onClick={()=>{setComposer(false);setScannerOpen(false)}}><section className="sheet recipe-sheet" role="dialog" aria-modal="true" aria-label="Собрать блюдо" onClick={e=>e.stopPropagation()}><div className="sheet-head"><div><b>Собрать блюдо</b><small>Свой рецепт</small></div><button className="icon" onClick={()=>{setComposer(false);setScannerOpen(false)}} aria-label="Закрыть"><X size={19}/></button></div><div className="sheet-body"><label className="field">Название<input value={mealName} onChange={e=>setMealName(e.target.value)} placeholder="Например, паста с курицей"/></label><label className="field">Приём пищи<select value={mealType} onChange={e=>setMealType(e.target.value)}>{mealTypes.map(([v,l])=><option value={v} key={v}>{l}</option>)}</select></label><div className="ingredient-search"><input value={search} onChange={e=>setSearch(e.target.value)} placeholder="Найти ингредиент"/><input type="number" min="1" value={grams} onChange={e=>setGrams(Number(e.target.value))} aria-label="Вес в граммах"/><span>г</span></div><div className="ingredient-options">{(search?filtered:catalogFoods).slice(0,10).map(f=><button key={f.id} onClick={()=>chooseFood(f)}><Plus size={14}/>{f.name}<small>{f.kcal} ккал</small></button>)}<div className="custom-food"><input value={customName} onChange={e=>setCustomName(e.target.value)} placeholder="Свой продукт"/><input type="number" min="0" value={customKcal} onChange={e=>setCustomKcal(e.target.value)} placeholder="ккал"/><button disabled={!customName||!customKcal} onClick={()=>{chooseFood(enrichFood({name:customName,category:'Своё',kcal:Number(customKcal),p:0,c:0,f:0}));setCustomName('');setCustomKcal('')}} aria-label="Добавить свой продукт"><Plus size={15}/></button></div></div><div className="selected-ingredients">{ingredients.map((i,index)=><div key={`${i.name}-${index}`}><span>{i.name}</span><label><input type="number" min="1" value={i.grams} onChange={e=>setIngredients(v=>v.map((item,n)=>n===index?{...item,grams:Number(e.target.value)}:item))}/> г</label><b>{Math.round(i.kcal*i.grams/100)} ккал</b><button onClick={()=>setIngredients(v=>v.filter((_,n)=>n!==index))} aria-label="Убрать"><X size={14}/></button></div>)}{!ingredients.length&&<small>Выберите продукты и укажите вес.</small>}</div><details className="advanced-tools"><summary><Camera size={15}/>Штрихкод и фото</summary><div className="ocr-tools"><label className="field">Фото таблицы КБЖУ<input type="file" accept="image/*" capture="environment" onChange={e=>{const file=e.target.files?.[0]||null;setNutritionPhoto(file);if(file)runOcr(file)}}/></label>{nutritionPhoto&&<small>{ocrBusy?"Распознаём фото…":nutritionPhoto.name}</small>}{ocrText&&<details><summary>Распознанный текст</summary><pre>{ocrText}</pre></details>}<label className="field">Фото упаковки<input type="file" accept="image/*" capture="environment" onChange={e=>setFrontPhoto(e.target.files?.[0]||null)}/></label>{frontPhoto&&<small>{frontPhoto.name}</small>}</div><div className="barcode-tools"><div><Barcode size={17}/><input inputMode="numeric" value={barcode} onChange={e=>setBarcode(e.target.value)} placeholder="Штрихкод товара"/><button onClick={()=>lookupBarcode()} disabled={barcodeBusy}>{barcodeBusy?<LoaderCircle className="spin" size={16}/>:<Search size={16}/>}Найти</button></div><button className="camera-button" onClick={()=>{setScannerOpen(v=>!v);setBarcodeMessage('')}}><Camera size={16}/>{scannerOpen?'Закрыть камеру':'Сканировать камерой'}</button>{scannerOpen&&<video ref={videoRef} className="barcode-video" playsInline muted/>}{barcodeMessage&&<small className="barcode-message">{barcodeMessage}</small>}{addProductOpen&&<div className="community-product-form"><b>Добавить продукт в каталог</b><label>Название<input value={productName} onChange={e=>setProductName(e.target.value)} placeholder="Например, молоко 2,5%"/></label><label>Бренд<input value={productBrand} onChange={e=>setProductBrand(e.target.value)} placeholder="Производитель"/></label><div className="community-macros">{([['kcal','Ккал'],['protein','Белки'],['carbs','Углеводы'],['fat','Жиры']] as const).map(([key,label])=><label key={key}>{label}<input type="number" min="0" max="1000" step="0.1" value={productMacros[key]} onChange={e=>setProductMacros(v=>({...v,[key]:e.target.value}))}/></label>)}</div><button onClick={saveCommunityFood} disabled={barcodeBusy||ocrBusy||!productName.trim()||!productMacros.kcal}><Plus size={15}/>Сохранить продукт</button><button className="camera-button" onClick={()=>setAddProductOpen(false)}>Закрыть</button></div>}</div></details></div><div className="recipe-total"><div><b>{Math.round(recipe.calories)} ккал</b><small>Б {recipe.protein.toFixed(1)} · У {recipe.carbs.toFixed(1)} · Ж {recipe.fat.toFixed(1)} г</small></div><div className="recipe-buttons"><button onClick={saveRecipe} disabled={saveSavedRecipe.isPending}><Bookmark size={15}/>{saveSavedRecipe.isPending?'Сохраняем…':'В рецепты'}</button><button onClick={saveRecipeToDay} disabled={add.isPending}>{add.isPending?'Добавляем…':'В дневник'}</button></div></div></section></div>}

  <PromptDialog
    open={copyDayOpen}
    title="Копировать день"
    label="Дата назначения"
    inputType="date"
    initialValue={selected===iso(today)?iso(new Date(new Date(`${selected}T12:00:00`).getTime()+86400000)):iso(today)}
    confirmLabel="Копировать"
    busyLabel="Копируем…"
    busy={copyDay.isPending}
    error={copyDayError}
    validate={(value)=>{
      if(!/^\d{4}-\d{2}-\d{2}$/.test(value))return 'Введите дату в формате ГГГГ-ММ-ДД';
      if(value===selected)return 'Выберите другой день';
      return null;
    }}
    onSubmit={(value)=>copyDay.mutate(value)}
    onCancel={()=>setCopyDayOpen(false)}
  />
 </section>;
}
