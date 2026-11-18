"""Small web chat to try the MCP server with an LLM agent (OpenAI Agents SDK).

    python server.py                       # MCP server (streamable HTTP)
    python -m demo.web_chat                # http://127.0.0.1:8020

Env: OPENAI_API_KEY, MCP_URL (default http://127.0.0.1:8000/mcp), OPENAI_MODEL.
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from agents import Agent, Runner
from agents.mcp import MCPServerStreamableHttp
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel

load_dotenv()

INSTRUCTIONS = """You are the internal assistant of a manufacturing group. Answer in the user's language,
briefly, using ONLY data returned by your tools (people directory, org chart, KPIs, action plans).
For a SQL request, use execute_readonly_query and report its answer faithfully, including refusals.
Never invent a person, a number or a date."""


@asynccontextmanager
async def lifespan(app: FastAPI):
    url = os.getenv("MCP_URL", f"http://127.0.0.1:{os.getenv('MCP_PORT', '8000')}/mcp")
    async with MCPServerStreamableHttp(params={"url": url}, cache_tools_list=True, name="enterprise") as server:
        app.state.agent = Agent(name="Enterprise assistant", instructions=INSTRUCTIONS, mcp_servers=[server],
                                model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"))
        app.state.tool_count = len(await server.list_tools())
        yield


app = FastAPI(lifespan=lifespan)


class Ask(BaseModel):
    message: str
    history: list = []


@app.get("/")
def page():
    return FileResponse(Path(__file__).parent / "chat.html")


@app.get("/api/info")
def info():
    return {"tools": app.state.tool_count}


@app.post("/api/ask")
async def ask(body: Ask):
    result = await Runner.run(app.state.agent, [*body.history, {"role": "user", "content": body.message}])
    calls = []
    for item in result.new_items:
        if item.type == "tool_call_item":
            raw = item.raw_item
            calls.append({"name": getattr(raw, "name", "tool"), "arguments": getattr(raw, "arguments", "")})
        elif item.type == "tool_call_output_item":
            output = str(item.output)
            if calls:
                calls[-1]["ok"] = '"success": false' not in output.replace("'", '"') and '"success":false' not in output
                calls[-1]["preview"] = output[:160]
    return {"reply": result.final_output, "tools": calls, "history": result.to_input_list()}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=int(os.getenv("CHAT_PORT", "8020")))
