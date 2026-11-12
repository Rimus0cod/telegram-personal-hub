from fastapi import APIRouter, Depends, HTTPException, Query
import httpx
from sqlalchemy.orm import Session
from app.database import get_db
from app.dependencies.auth import current_user
from app.models.models import User
router=APIRouter(prefix="/weather",tags=["weather"])
@router.get("/search")
async def search(city:str=Query(min_length=2,max_length=100),user:User=Depends(current_user)):
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r=await client.get("https://geocoding-api.open-meteo.com/v1/search",params={"name":city,"count":8,"language":"en","format":"json"})
    except httpx.RequestError as exc: raise HTTPException(502,"Cannot reach the city search service") from exc
    if r.is_error: raise HTTPException(502,"Weather provider unavailable")
    return r.json().get("results",[])
@router.get("")
async def weather(city:str|None=None,latitude:float|None=None,longitude:float|None=None,db:Session=Depends(get_db),user:User=Depends(current_user)):
    if (latitude is None) != (longitude is None): raise HTTPException(422,"Provide both latitude and longitude")
    if latitude is not None and not -90 <= latitude <= 90: raise HTTPException(422,"Latitude must be between -90 and 90")
    if longitude is not None and not -180 <= longitude <= 180: raise HTTPException(422,"Longitude must be between -180 and 180")
    if city:
        if latitude is None or longitude is None:
            try:
                async with httpx.AsyncClient(timeout=10) as client: geo=await client.get("https://geocoding-api.open-meteo.com/v1/search",params={"name":city,"count":1})
            except httpx.RequestError as exc: raise HTTPException(502,"Cannot reach the city search service") from exc
            if geo.is_error: raise HTTPException(502,"Weather provider unavailable")
            result=(geo.json().get("results") or [None])[0]
            if not result: raise HTTPException(404,"City not found")
            latitude,longitude=result["latitude"],result["longitude"]
            city=result["name"]
        user.last_city=city; db.commit()
    elif latitude is None or longitude is None:
        if user.last_city:
            try:
                async with httpx.AsyncClient(timeout=10) as client: geo=await client.get("https://geocoding-api.open-meteo.com/v1/search",params={"name":user.last_city,"count":1})
            except httpx.RequestError as exc: raise HTTPException(502,"Cannot reach the city search service") from exc
            try: result=(geo.json().get("results") or [None])[0] if not geo.is_error else None
            except ValueError as exc: raise HTTPException(502,"Invalid response from the city search service") from exc
            if result: latitude,longitude=result["latitude"],result["longitude"]
        if latitude is None: raise HTTPException(400,"Provide a city or coordinates")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r=await client.get("https://api.open-meteo.com/v1/forecast",params={"latitude":latitude,"longitude":longitude,"current":"temperature_2m,apparent_temperature,relative_humidity_2m,wind_speed_10m,wind_direction_10m,weather_code,is_day","daily":"weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,sunrise,sunset","timezone":"auto","forecast_days":5})
    except httpx.RequestError as exc: raise HTTPException(502,"Cannot reach the weather service") from exc
    if r.is_error: raise HTTPException(502,"Weather provider unavailable")
    data=r.json(); data["city"]=user.last_city or city
    return data
