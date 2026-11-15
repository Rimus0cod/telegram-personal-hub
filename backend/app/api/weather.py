import asyncio
import re
from fastapi import APIRouter, Depends, HTTPException, Query
import httpx
from sqlalchemy.orm import Session
from app.database import get_db
from app.dependencies.auth import current_user
from app.models.models import User
router=APIRouter(prefix="/weather",tags=["weather"])

def _language_order(name:str):
    if re.search(r"[іїєґ]",name,re.IGNORECASE): return ("uk","ru","en")
    if re.search(r"[ыэёъ]",name,re.IGNORECASE): return ("ru","uk","en")
    if re.search(r"[a-z]",name,re.IGNORECASE): return ("en","uk","ru")
    return ("ru","uk","en")

async def _geocode(name:str,count:int=8):
    languages=_language_order(name)
    async with httpx.AsyncClient(timeout=10) as client:
        responses=await asyncio.gather(*(client.get("https://geocoding-api.open-meteo.com/v1/search",
            params={"name":name,"count":count,"language":language,"format":"json"}) for language in languages),return_exceptions=True)
    merged={}; any_response=False
    for response in responses:
        if isinstance(response,Exception) or response.is_error: continue
        any_response=True
        try: results=response.json().get("results") or []
        except ValueError: continue
        for result in results:
            key=result.get("id") or (round(result.get("latitude",0),4),round(result.get("longitude",0),4))
            if key not in merged:
                result["aliases"]=[result["name"]]; merged[key]=result
            elif result.get("name") not in merged[key]["aliases"]:
                merged[key]["aliases"].append(result["name"])
    if not any_response: raise HTTPException(502,"Weather provider unavailable")
    query=name.casefold().strip()
    def rank(result):
        aliases=[alias.casefold() for alias in result.get("aliases",[])]
        return (0 if query in aliases else 1 if any(alias.startswith(query) for alias in aliases) else 2,
                -int(result.get("population") or 0))
    return sorted(merged.values(),key=rank)[:count]

@router.get("/search")
async def search(city:str=Query(min_length=2,max_length=100),user:User=Depends(current_user)):
    return await _geocode(city)
@router.get("")
async def weather(city:str|None=Query(default=None,max_length=120),latitude:float|None=None,longitude:float|None=None,db:Session=Depends(get_db),user:User=Depends(current_user)):
    if (latitude is None) != (longitude is None): raise HTTPException(422,"Provide both latitude and longitude")
    if latitude is not None and not -90 <= latitude <= 90: raise HTTPException(422,"Latitude must be between -90 and 90")
    if longitude is not None and not -180 <= longitude <= 180: raise HTTPException(422,"Longitude must be between -180 and 180")
    if city:
        if latitude is None or longitude is None:
            result=next(iter(await _geocode(city,1)),None)
            if not result: raise HTTPException(404,"City not found")
            latitude,longitude=result["latitude"],result["longitude"]
            city=result["name"]
        if len(city) > 120: raise HTTPException(422,"City name is too long")
        user.last_city=city; db.commit()
    elif latitude is None or longitude is None:
        if user.last_city:
            result=next(iter(await _geocode(user.last_city,1)),None)
            if result: latitude,longitude=result["latitude"],result["longitude"]
        if latitude is None: raise HTTPException(400,"Provide a city or coordinates")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r=await client.get("https://api.open-meteo.com/v1/forecast",params={"latitude":latitude,"longitude":longitude,"current":"temperature_2m,apparent_temperature,relative_humidity_2m,wind_speed_10m,wind_direction_10m,weather_code,is_day","daily":"weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,sunrise,sunset","timezone":"auto","forecast_days":5})
    except httpx.RequestError as exc: raise HTTPException(502,"Cannot reach the weather service") from exc
    if r.is_error: raise HTTPException(502,"Weather provider unavailable")
    data=r.json(); data["city"]=user.last_city or city
    return data
