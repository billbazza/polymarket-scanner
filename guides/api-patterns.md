# API Patterns Guide

## Adding New Endpoints to server.py

Follow these patterns when adding API endpoints:

### Read endpoint (GET)
```python
@app.get("/api/things")
async def list_things(limit: int = 50, filter: str = None):
    return db.get_things(limit=limit, filter=filter)
```

### Write endpoint (POST)
```python
@app.post("/api/things")
async def create_thing(name: str, value: float = 0):
    try:
        thing_id = db.save_thing(name, value)
        return {"id": thing_id, "status": "created"}
    except Exception as e:
        log.error("Failed to create thing: %s", e)
        return JSONResponse(status_code=500, content={"error": str(e)})
```

### Patterns to follow:
1. Always wrap external calls in try/except
2. Return structured JSON (dicts with status fields)
3. Use `JSONResponse` for errors, not bare `raise`
4. Log at INFO for successful operations, WARNING for recoverable errors, ERROR for failures
5. Use query parameters for simple filters, not request bodies

### Adding to the dashboard
1. Add the fetch call in the `<script>` section of dashboard.html
2. Add a button or table to display results
3. Use the existing helper functions: `fmt()`, `fmtUsd()`, `fmtPct()`, `esc()`

## Rate Limiting
- The sync API client (`api.py`) has a 200ms minimum interval between requests
- The async client (`async_api.py`) uses a semaphore to cap at 15 concurrent requests
- Never remove these — Polymarket will rate-limit or ban aggressive callers

## Error Responses from Polymarket
- 422: Invalid parameter (e.g., unsupported `order` field)
- 429: Rate limited — back off
- 500: Server error — retry with backoff
- Empty response body: Market may be delisted or resolved
