import asyncio
from calcore.llm import call_llm  

async def _run_llm_insight(session_id: str):
    session = store.get(session_id)
    llm_cfg = session.get("llm_config", {})
    
    if not llm_cfg.get("endpoint") or not llm_cfg.get("model"):
        return
    
    try:
        insight = await call_llm(session, llm_cfg)
        # Emit SSE event with the insight
        emit_sse_event(session_id, "llm_insight", insight)
    except Exception as e:
        logger.error(f"LLM insight failed for session {session_id}: {e}")