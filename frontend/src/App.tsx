import { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeSanitize from 'rehype-sanitize';
import Nutrition from './Nutrition';
import FocusTimer from './focus/FocusTimer';
import { useFocusTimer } from './focus/useFocusTimer';
import { classifyDeliveryError } from './focus/delivery';
import PromptDialog from './PromptDialog';
import type { CompletedSession } from './focus/timer';
import { CloudSun, CheckSquare, StickyNote, Timer, Plus, Search, Trash2, Sun, Cloud, CloudRain, CloudSnow, CloudFog, CloudLightning, LocateFixed, RefreshCw, Droplets, Wind, Sunrise, Sunset, Pencil, X, Check, MapPin, Copy, BookOpen, ArrowLeft, CalendarDays } from 'lucide-react';
import './style.css';

const API=import.meta.env.VITE_API_URL||'';
const telegram=(window as any).Telegram?.WebApp;
const initData=telegram?.initData||'';
async function request(path:string, options:RequestInit={}) {
 const response=await fetch(`${API}/api/v1${path}`,{...options,headers:{'Content-Type':'application/json',Authorization:`tma ${initData}`,...options.headers}});
 if(!response.ok){let detail=`Request failed (${response.status})`;try{const body=await response.json();detail=body.detail||detail}catch{};const error=Object.assign(new Error(detail),{status:response.status});throw error}
 return response.status===204?null:response.json();
}
type Tab='home'|'tasks'|'notes'|'focus'|'weather'|'nutrition';
function prepareNoteMarkdown(source:string){
 const lines=source.replace(/\r\n?/g,'\n').split('\n');
 const isPipeRow=(line:string)=>line.includes('|')&&line.split('|').length>=3;
 for(let i=0;i<lines.length;){
  if(!isPipeRow(lines[i])||/^\s*\|?\s*:?-{3,}/.test(lines[i])){i++;continue}
  let end=i;while(end<lines.length&&isPipeRow(lines[end]))end++;
  if(end-i>=2&&!/^\s*\|?\s*:?-{3,}/.test(lines[i+1])){
   const columns=lines[i].split('|').length-1;
   lines.splice(i+1,0,Array(columns+1).fill('---').join(' | '));
   end++;
  }
  i=end;
 }
 return lines.join('\n');
}
function weatherInfo(code:number,isDay=true){
 if(code===0)return {label:'Ясно',Icon:isDay?Sun:CloudSun};
 if([1,2].includes(code))return {label:'Переменная облачность',Icon:CloudSun};
 if(code===3)return {label:'Облачно',Icon:Cloud};
 if([45,48].includes(code))return {label:'Туман',Icon:CloudFog};
 if([51,53,55,56,57,61,63,65,66,67,80,81,82].includes(code))return {label:'Дождь',Icon:CloudRain};
 if([71,73,75,77,85,86].includes(code))return {label:'Снег',Icon:CloudSnow};
 if([95,96,99].includes(code))return {label:'Гроза',Icon:CloudLightning};
 return {label:'Переменная облачность',Icon:CloudSun};
}
export default function App(){
 const [tab,setTab]=useState<Tab>('home'),[draft,setDraft]=useState(''),[noteContent,setNoteContent]=useState(''),[editingNote,setEditingNote]=useState<any>(null),[viewingNote,setViewingNote]=useState<any>(null),[city,setCity]=useState(''),[selectedCityName,setSelectedCityName]=useState(''),[cityOptions,setCityOptions]=useState<any[]>([]),[searchingCities,setSearchingCities]=useState(false),[loadingWeather,setLoadingWeather]=useState(false),[searchText,setSearchText]=useState(''),[query,setQuery]=useState(''),[taskFilter,setTaskFilter]=useState('all'),[notice,setNotice]=useState(''),[editingTask,setEditingTask]=useState<any>(null),[taskEditError,setTaskEditError]=useState('');
 const qc=useQueryClient(); const me=useQuery({queryKey:['me'],queryFn:()=>request('/auth/me'),retry:false,enabled:!!initData});
 const tasks=useQuery({queryKey:['tasks',taskFilter],queryFn:()=>request('/tasks?status='+taskFilter) as Promise<any[]> ,enabled:!!initData});
 const notes=useQuery({queryKey:['notes',query],queryFn:()=>request('/notes?q='+encodeURIComponent(query)) as Promise<any[]>,enabled:!!initData});
 const weather=useQuery({queryKey:['weather'],queryFn:()=>request('/weather') as Promise<any>,enabled:!!initData,retry:false});
 const addTask=useMutation({mutationFn:()=>request('/tasks',{method:'POST',body:JSON.stringify({title:draft})}),onSuccess:()=>{setDraft('');setNotice('Task saved');qc.invalidateQueries({queryKey:['tasks']})},onError:(e:any)=>setNotice(e.message)});
 const addNote=useMutation({mutationFn:()=>request('/notes',{method:'POST',body:JSON.stringify({title:draft,content:noteContent})}),onSuccess:()=>{setDraft('');setNoteContent('');setNotice('Note saved');qc.invalidateQueries({queryKey:['notes']})},onError:(e:any)=>setNotice(e.message)});
 const toggle=useMutation({mutationFn:(t:any)=>request(`/tasks/${t.id}`,{method:'PATCH',body:JSON.stringify({completed:!t.completed})}),onSuccess:()=>qc.invalidateQueries({queryKey:['tasks']})});
 const updateTask=useMutation({mutationFn:({t,title}:any)=>request(`/tasks/${t.id}`,{method:'PATCH',body:JSON.stringify({title})}),onSuccess:()=>{qc.invalidateQueries({queryKey:['tasks']});setEditingTask(null);setTaskEditError('')},onError:(e:any)=>setTaskEditError(e.message)});
 const deleteTask=useMutation({mutationFn:(t:any)=>request(`/tasks/${t.id}`,{method:'DELETE'}),onSuccess:()=>qc.invalidateQueries({queryKey:['tasks']})});
 const updateNote=useMutation({mutationFn:({n,title,content}:any)=>request(`/notes/${n.id}`,{method:'PATCH',body:JSON.stringify({title,content})}),onSuccess:()=>{setEditingNote(null);setNotice('Note updated');qc.invalidateQueries({queryKey:['notes']})},onError:(e:any)=>setNotice(e.message)});
 const remove=useMutation({mutationFn:(n:any)=>request(`/notes/${n.id}`,{method:'DELETE'}),onSuccess:(_data,n)=>{if(editingNote?.id===n.id){setEditingNote(null);setDraft('');setNoteContent('')}if(viewingNote?.id===n.id)setViewingNote(null);setNotice('Заметка удалена');qc.invalidateQueries({queryKey:['notes']})},onError:(e:any)=>setNotice(`Не удалось удалить заметку: ${e.message}`)});
 const record=useMutation({mutationFn:(session:CompletedSession)=>request('/focus/sessions',{method:'POST',body:JSON.stringify({duration:session.durationMinutes,mode:session.mode,completed:true,started_at:new Date(session.startedAt).toISOString(),finished_at:new Date(session.finishedAt).toISOString(),...(session.clientId?{client_id:session.clientId}:{})})}),onSuccess:(_data,session)=>{qc.invalidateQueries({queryKey:['stats']});focusTimer.markSaved(session)},onError:(error,session)=>focusTimer.markFailed(session,classifyDeliveryError(error))});
 const focusTimer=useFocusTimer({onComplete:session=>record.mutate(session)});
 const stats=useQuery({queryKey:['stats'],queryFn:()=>request('/focus/stats'),enabled:!!initData});
 useEffect(()=>{if(!initData||city.trim().length<2||city.trim()===selectedCityName){setCityOptions([]);setSearchingCities(false);return}let active=true;setSearchingCities(true);const timer=setTimeout(()=>request('/weather/search?city='+encodeURIComponent(city.trim())).then((results:any[])=>{if(active)setCityOptions(results)}).catch((e:any)=>{if(active)setNotice(`Не удалось найти город: ${e.message}`)}).finally(()=>{if(active)setSearchingCities(false)}),300);return()=>{active=false;clearTimeout(timer)}},[city,selectedCityName]);
 useEffect(()=>{const timer=setTimeout(()=>setQuery(searchText.trim()),250);return()=>clearTimeout(timer)},[searchText]);
 useEffect(()=>{if(!viewingNote)return;const handleKey=(event:KeyboardEvent)=>{if(event.key==='Escape')setViewingNote(null)};window.addEventListener('keydown',handleKey);return()=>window.removeEventListener('keydown',handleKey)},[viewingNote]);
 async function chooseCity(name=city,latitude?:number,longitude?:number){if(!name.trim())return;setLoadingWeather(true);setNotice('');try{const params=new URLSearchParams({city:name.trim()});if(latitude!=null&&longitude!=null){params.set('latitude',String(latitude));params.set('longitude',String(longitude))}const r=await request('/weather?'+params.toString());qc.setQueryData(['weather'],r);setCityOptions([]);setSelectedCityName(r.city||name);setCity(r.city||name);setNotice('')}catch(e:any){setNotice(`Не удалось загрузить погоду: ${e.message}`)}finally{setLoadingWeather(false)}}
 function useCurrentLocation(){if(!navigator.geolocation){setNotice('Геолокация недоступна в этом приложении.');return}setLoadingWeather(true);setNotice('Определяем местоположение…');navigator.geolocation.getCurrentPosition(async position=>{try{const {latitude,longitude}=position.coords;const data=await request(`/weather?latitude=${latitude}&longitude=${longitude}`);qc.setQueryData(['weather'],{...data,city:'Моё местоположение'});setNotice('')}catch(e:any){setNotice(`Не удалось загрузить погоду: ${e.message}`)}finally{setLoadingWeather(false)}},error=>{setLoadingWeather(false);setNotice(error.code===1?'Разрешите доступ к местоположению в настройках Telegram.':'Не удалось определить местоположение.')},{enableHighAccuracy:false,timeout:10000,maximumAge:300000})}
 function confirmDeleteNote(note:any){const removeNote=()=>remove.mutate(note);if(telegram?.showConfirm){telegram.showConfirm('Удалить эту заметку?',(confirmed:boolean)=>{if(confirmed)removeNote()})}else if(window.confirm('Удалить эту заметку?'))removeNote()}
 async function copyNote(note:any){try{await navigator.clipboard.writeText(`${note.title}\n\n${note.content||''}`);setNotice('Текст заметки скопирован')}catch{setNotice('Не удалось скопировать текст на этом устройстве')}}
 const list=tab==='notes'?notes.data:[];
 const currentWeather=weather.data?.current;
 const currentCondition=weatherInfo(currentWeather?.weather_code??0,currentWeather?.is_day!==0);
 return <main><header><div><span className="eyebrow">YOUR PERSONAL SPACE</span><h1>{tab==='home'?`Good day${me.data?.first_name?`, ${me.data.first_name}`:''}`:({tasks:'Tasks',notes:'Notes',focus:'Focus',weather:'Weather',nutrition:'Питание'} as any)[tab]}</h1></div><span className="avatar">{me.data?.first_name?.[0]||'✦'}</span></header>
 {!initData&&<section className="card warning">Откройте приложение через Telegram-бота, чтобы войти и сохранять свои данные. В обычном браузере Telegram не передаёт данные безопасного входа.</section>}
 {notice&&<button className="notice" onClick={()=>setNotice('')}>{notice} <span>×</span></button>}
 {tab==='home'&&<><button className="card weather" onClick={()=>setTab('weather')}><currentCondition.Icon/><div><b>{currentWeather?.temperature_2m!=null?`${Math.round(currentWeather.temperature_2m)}° · ${currentCondition.label}`:weather.isLoading?'Загружаем погоду…':'Выберите город'}</b><small>{weather.data?.city||'Прогноз на 5 дней'}</small></div><span className="chevron">›</span></button><div className="grid"><button className="card stat" onClick={()=>setTab('tasks')}><CheckSquare/><b>{tasks.data?.filter(t=>!t.completed).length??'—'}</b><small>Open tasks</small></button><button className="card stat" onClick={()=>setTab('notes')}><StickyNote/><b>{notes.data?.length??'—'}</b><small>Notes</small></button></div><button className="card focus-card" onClick={()=>setTab('focus')}><Timer/><div><b>Focus timer</b><small>{stats.data?.sessions??0} sessions · {stats.data?.focus_minutes??0} min focused</small></div><span className="chevron">›</span></button><h2>Quick actions</h2><div className="quick"><button onClick={()=>setTab('tasks')}>+ New task</button><button onClick={()=>setTab('notes')}>+ New note</button></div></>}
 {tab==='tasks'&&<><form onSubmit={e=>{e.preventDefault();if(draft.trim()&&initData)addTask.mutate();else if(!initData)setNotice('Откройте Mini App через Telegram, чтобы создавать задачи.')}}><input value={draft} onChange={e=>setDraft(e.target.value)} placeholder="Что нужно сделать?" disabled={!initData}/><button aria-label="Добавить задачу" disabled={!initData||addTask.isPending}><Plus/></button></form><div className="quick">{[['all','Все'],['active','Активные'],['completed','Готовые']].map(([f,label])=><button className={taskFilter===f?'selected':''} key={f} onClick={()=>setTaskFilter(f)}>{label}</button>)}</div>{tasks.isLoading&&initData&&<p className="empty">Загружаем задачи…</p>}{tasks.isError&&<p className="error">Не удалось загрузить задачи: {(tasks.error as Error).message}</p>}{tasks.data?.map((t:any)=><article className="row" key={t.id}><input type="checkbox" checked={t.completed} onChange={()=>toggle.mutate(t)}/><span className={t.completed?'done':''} onClick={()=>{setTaskEditError('');setEditingTask(t)}}>{t.title}</span><small className={'priority '+t.priority}>{t.priority}</small><button className="icon" onClick={()=>deleteTask.mutate(t)} aria-label="Удалить задачу"><Trash2 size={16}/></button></article>)}{tasks.data?.length===0&&<p className="empty">Пока задач нет. Добавьте первую выше.</p>}</>}
 {tab==='notes'&&<>
  <form className="note-form" onSubmit={e=>{e.preventDefault();if(!initData){setNotice('Откройте Mini App через Telegram, чтобы сохранять заметки.');return}if(!draft.trim()){setNotice('Добавьте заголовок заметки.');return}if(editingNote)updateNote.mutate({n:editingNote,title:draft.trim(),content:noteContent});else addNote.mutate()}}>
   <input maxLength={200} value={draft} onChange={e=>setDraft(e.target.value)} placeholder={editingNote?'Заголовок заметки':'Новая заметка'} disabled={!initData}/>
   <textarea maxLength={20000} value={noteContent} onChange={e=>setNoteContent(e.target.value)} placeholder="Идеи, планы, ссылки — сохраните всё важное…" rows={5} disabled={!initData}/>
   <div className="editor-meta"><small>{noteContent.length.toLocaleString()} / 20 000</small><div className="form-actions">{editingNote&&<button type="button" className="secondary" onClick={()=>{setEditingNote(null);setDraft('');setNoteContent('')}}><X size={16}/>Отмена</button>}<button type="submit" disabled={!initData||!draft.trim()||addNote.isPending||updateNote.isPending}>{addNote.isPending||updateNote.isPending?<RefreshCw className="spin" size={16}/>:editingNote?<Check size={16}/>:<Plus size={16}/>} {addNote.isPending||updateNote.isPending?'Сохраняем…':editingNote?'Сохранить изменения':'Сохранить заметку'}</button></div></div>
  </form>
  <label className="search"><Search size={17}/><input value={searchText} onChange={e=>setSearchText(e.target.value)} placeholder="Найти заметку по заголовку или тексту"/>{searchText&&<button type="button" className="clear-search" onClick={()=>setSearchText('')} aria-label="Очистить поиск"><X size={16}/></button>}</label>
  <div className="list-heading"><b>{query?'Результаты поиска':'Мои заметки'}</b><small>{notes.data?.length??0}</small></div>
  {notes.isFetching&&initData&&<p className="inline-loading"><RefreshCw className="spin" size={15}/> Обновляем список…</p>}{notes.isError&&<p className="error">Не удалось загрузить заметки: {(notes.error as Error).message}</p>}
  <div className="notes-list">{list?.map((n:any)=><article className={`note-card ${editingNote?.id===n.id?'editing':''}`} key={n.id}>
   <button className="note-open" onClick={()=>setViewingNote(n)} aria-label={`Открыть заметку: ${n.title}`}><span className="note-card-title">{n.title}</span><span className="note-preview">{n.content||'Нет текста — нажмите, чтобы открыть'}</span><span className="note-date">Изменена {new Date(n.updated_at).toLocaleDateString(undefined,{day:'numeric',month:'short',year:'numeric'})}</span></button>
   <div className="note-actions"><button className="icon" onClick={()=>{setEditingNote(n);setDraft(n.title);setNoteContent(n.content);window.scrollTo({top:0,behavior:'smooth'})}} aria-label="Редактировать"><Pencil size={16}/></button><button className="icon danger-icon" onClick={()=>confirmDeleteNote(n)} aria-label="Удалить заметку"><Trash2 size={16}/></button></div>
  </article>)}</div>
  {notes.data?.length===0&&<p className="empty">{query?'По этому запросу заметок нет. Попробуйте изменить слова поиска.':'Пока заметок нет. Создайте первую — она останется здесь.'}</p>}
 </>}
 {tab==='weather'&&<>
  <div className="weather-toolbar"><button className="location-button" onClick={useCurrentLocation} disabled={!initData||loadingWeather}><LocateFixed size={16}/><span>Моё местоположение</span></button><button className="icon refresh-button" onClick={()=>weather.refetch()} disabled={!initData||weather.isFetching} aria-label="Обновить погоду"><RefreshCw className={weather.isFetching?'spin':''} size={17}/></button></div>
  <form className="city-form" onSubmit={e=>{e.preventDefault();const result=cityOptions[0];chooseCity(result?.name||city,result?.latitude,result?.longitude)}}>
   <input value={city} onChange={e=>setCity(e.target.value)} placeholder="City / Город / Місто"/>
   <button aria-label="Найти город" disabled={!initData||city.trim().length<2||loadingWeather}>{searchingCities?<RefreshCw className="spin"/>:<Search/>}</button>
  </form>
  {cityOptions.length>0&&<div className="city-options">{cityOptions.map((c:any)=><button key={`${c.id}-${c.latitude}`} onClick={()=>chooseCity(c.name,c.latitude,c.longitude)}><MapPin size={18}/><span><b>{c.name}</b><small>{[c.admin1,c.country].filter(Boolean).join(', ')}{c.aliases?.filter((name:string)=>name!==c.name).length?` · ${c.aliases.filter((name:string)=>name!==c.name).join(' / ')}`:''}{c.population?` · ${new Intl.NumberFormat().format(c.population)} жителей`:''}</small></span><span className="chevron">›</span></button>)}</div>}
  {loadingWeather&&<div className="weather-loading"><span className="loader"/> Получаем актуальный прогноз…</div>}
  {weather.isError&&(weather.error as any)?.status!==400&&!loadingWeather&&<p className="error">Погоду не удалось загрузить: {(weather.error as Error).message}. Попробуйте обновить прогноз.</p>}
  {weather.data&&<>
   <section className="card climate"><div className="climate-top"><span>{weather.data.city||'Моё местоположение'}</span><small>{currentWeather?.time?new Date(currentWeather.time).toLocaleTimeString(undefined,{hour:'2-digit',minute:'2-digit'}):''}</small></div><currentCondition.Icon className="weather-main-icon" size={58}/><strong>{Math.round(currentWeather?.temperature_2m??0)}°</strong><span className="condition-label">{currentCondition.label}</span><p>Ощущается как {Math.round(currentWeather?.apparent_temperature??0)}°</p><div className="weather-metrics"><div><Droplets size={17}/><b>{currentWeather?.relative_humidity_2m}%</b><small>Влажность</small></div><div><Wind size={17}/><b>{Math.round(currentWeather?.wind_speed_10m??0)} км/ч</b><small>Ветер · {currentWeather?.wind_direction_10m}°</small></div></div></section>
   <div className="forecast-heading"><b>Прогноз на 5 дней</b><small>Мин. / макс.</small></div>
   <div className="forecast">{weather.data.daily?.time.map((d:string,i:number)=>{const info=weatherInfo(weather.data.daily.weather_code[i]);const Icon=info.Icon;return <div className="card day" key={d}><b>{i===0?'Сегодня':new Date(`${d}T12:00:00`).toLocaleDateString(undefined,{weekday:'short'})}</b><Icon size={22}/><small>{Math.round(weather.data.daily.temperature_2m_min[i])}° / {Math.round(weather.data.daily.temperature_2m_max[i])}°</small><small className="rain-chance"><Droplets size={12}/>{weather.data.daily.precipitation_probability_max?.[i]??0}%</small></div>})}</div>
   <div className="sun-times"><span><Sunrise size={17}/> Восход <b>{weather.data.daily?.sunrise?.[0]?new Date(weather.data.daily.sunrise[0]).toLocaleTimeString(undefined,{hour:'2-digit',minute:'2-digit'}):'—'}</b></span><span><Sunset size={17}/> Закат <b>{weather.data.daily?.sunset?.[0]?new Date(weather.data.daily.sunset[0]).toLocaleTimeString(undefined,{hour:'2-digit',minute:'2-digit'}):'—'}</b></span></div>
  </>}
 </>}
 {tab==='focus'&&<FocusTimer timer={focusTimer} stats={stats.data} statsError={stats.isError?(stats.error as Error).message:null} saveError={record.isError?(record.error as Error).message:null} saving={record.isPending}/>}
 {tab==='nutrition'&&<Nutrition request={request} initData={initData} setNotice={setNotice}/>}
 {viewingNote&&<div className="note-reader-backdrop" onClick={()=>setViewingNote(null)}><section className="note-reader" role="dialog" aria-modal="true" aria-labelledby="reader-title" onClick={e=>e.stopPropagation()}><div className="reader-toolbar"><button className="reader-back" onClick={()=>setViewingNote(null)}><ArrowLeft size={17}/>К заметкам</button><button className="icon" onClick={()=>setViewingNote(null)} aria-label="Закрыть"><X size={19}/></button></div><div className="reader-meta"><span><BookOpen size={15}/> ЗАМЕТКА</span><span><CalendarDays size={14}/>{new Date(viewingNote.updated_at).toLocaleDateString(undefined,{day:'numeric',month:'long',year:'numeric'})}</span></div><h2 id="reader-title" className="reader-title">{viewingNote.title}</h2><div className="reader-content">{viewingNote.content?<ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeSanitize]} components={{a:({node,...props})=><a {...props} target="_blank" rel="noopener noreferrer"/>}}>{prepareNoteMarkdown(viewingNote.content)}</ReactMarkdown>:<span className="reader-empty">В этой заметке пока нет текста.</span>}</div><div className="reader-actions"><button className="reader-copy" onClick={()=>copyNote(viewingNote)}><Copy size={16}/>Копировать</button><button onClick={()=>{setEditingNote(viewingNote);setDraft(viewingNote.title);setNoteContent(viewingNote.content||'');setViewingNote(null);window.scrollTo({top:0,behavior:'smooth'})}}><Pencil size={16}/>Редактировать</button><button className="reader-delete" onClick={()=>confirmDeleteNote(viewingNote)}><Trash2 size={16}/>Удалить</button></div></section></div>}
  <PromptDialog
    open={!!editingTask}
    title="Изменить задачу"
    label="Название задачи"
    initialValue={editingTask?.title ?? ''}
    confirmLabel="Сохранить"
    busy={updateTask.isPending}
    error={taskEditError}
    onSubmit={(title)=>{if(editingTask)updateTask.mutate({t:editingTask,title})}}
    onCancel={()=>setEditingTask(null)}
  />
 <nav>{([['home','Hub'],['tasks','Tasks'],['notes','Notes'],['nutrition','Питание'],['focus','Focus'],['weather','Weather']] as [Tab,string][]).map(([v,label])=><button className={tab===v?'active':''} onClick={()=>setTab(v)} key={v}>{label}</button>)}</nav></main>
}
