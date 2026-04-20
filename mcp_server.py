#!/usr/bin/env python3
"""
MCP server exposing mem0 session memory via Ollama.
"""

from pathlib import Path
from mcp.server.fastmcp import FastMCP
from mem0 import Memory

mem0_config = {
    "llm": {
        "provider": "openai",
        "config": {
            "model": "qwen/qwen3.6-35b-a3b",
            "openai_base_url": "http://localhost:11434/v1",
            "api_key": "ollama",
        }
    },
    "embedder": {
        "provider": "openai",
        "config": {
            "model": "nomic-embed-text",
            "openai_base_url": "http://localhost:11434/v1",
            "api_key": "ollama",
        }
    },
    "vector_store": {
        "provider": "chroma",
        "config": {
            "collection_name": "session_memory",
            "path": str(Path.home() / ".local/share/opencode-mem/chroma"),
        }
    }
}

memory = Memory.from_config(mem0_config)
mcp = FastMCP("opencode-mem")

@mcp.tool()
def save_memory(content: str, user_id: str = "jweber") -> str:
    """Save something to persistent memory."""
    result = memory.add(content, user_id=user_id)
    return f"Saved: {result}"

def _extract_results(results) -> list:
    if isinstance(results, dict):
        return results.get("results", [])
    if isinstance(results, list):
        return results
    return []

@mcp.tool()
def search_memory(query: str, user_id: str = "jweber", limit: int = 5) -> str:
    """Search persistent memory for relevant context."""
    results = memory.search(query, user_id=user_id, limit=limit)
    items = _extract_results(results)
    if not items:
        return "No relevant memories found."
    return "\n\n".join([f"- {r['memory']}" for r in items])

@mcp.tool()
def list_memories(user_id: str = "jweber") -> str:
    """List all stored memories."""
    results = memory.get_all(user_id=user_id)
    items = _extract_results(results)
    if not items:
        return "No memories stored yet."
    return "\n\n".join([f"- {r['memory']}" for r in items])

@mcp.tool()
def delete_memory(memory_id: str) -> str:
    """Delete a specific memory by ID."""
    memory.delete(memory_id)
    return f"Deleted memory {memory_id}"

if __name__ == "__main__":
    mcp.run()
