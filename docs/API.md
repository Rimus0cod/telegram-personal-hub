# API reference

OpenAPI UI is available at `/docs` on FastAPI. All routes below use `/api/v1`. Private routes require `Authorization: tma <Telegram WebApp initData>`.

| Method | Route | Description |
|---|---|---|
| GET | `/auth/me` | Verify launch and return current user |
| GET | `/weather?city=Kyiv` | Current weather, five-day forecast, save city |
| GET | `/weather?latitude=...&longitude=...` | Forecast at coordinates |
| GET | `/weather/search?city=Kyiv` | Geocoding search results |
| GET/POST | `/notes` | List/search (`q`) or create note |
| GET/PATCH/DELETE | `/notes/{id}` | Read/update/delete owned note |
| GET/POST | `/tasks` | List/filter (`status`) or create task |
| GET/PATCH/DELETE | `/tasks/{id}` | Read/update/delete owned task |
| POST | `/focus/sessions` | Record a session |
| GET | `/focus/sessions` | Recent sessions |
| GET | `/focus/stats` | Completed focus totals and recent history |
| GET | `/health` | Service liveness |

Typical errors: `401` invalid/missing/expired Telegram data, `404` missing or unowned resource, `422` invalid request, `502` weather provider failure, and `503` missing bot configuration.
