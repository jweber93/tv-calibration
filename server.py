from fastapi import FastAPI, Request
from sse_starlette.sse import EventSourceResponse

app = FastAPI()

_sse_clients: Dict[str, List[EventSourceResponse]] = {}

@app.get("/api/session/{sid}/llm/stream")
async def llm_stream(sid: str, request: Request):
    session = store.get(sid)
    if not session:
        raise HTTPException(404, "Session not found")

    async def event_generator():
        while True:
            if await request.is_disconnected():
                _sse_clients[sid].remove(event_generator)
                break
            # Yield events from the session's SSE queue
            yield {"event": "llm_insight", "data": "Insight data here"}
            await asyncio.sleep(1)  # Adjust sleep time as needed

    new_client = EventSourceResponse(event_generator())
    _sse_clients.setdefault(sid, []).append(new_client)
    return new_client

def emit_sse_event(session_id: str, event_type: str, data: Any):
    for client in _sse_clients.get(session_id, []):
        client.sse.put({"event": event_type, "data": data})