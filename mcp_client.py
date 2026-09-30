import os
import shutil
import sys
from pathlib import Path
from typing import Any

import certifi
from dotenv import load_dotenv
from langchain_groq import ChatGroq
from langchain_mcp_adapters.client import MultiServerMCPClient

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

os.environ["SSL_CERT_FILE"] = certifi.where()
os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")

AVIATION_STACK_API_KEY = (
    os.getenv("AVIATION_STACK_API_KEY")
    or os.getenv("AVIATIONSTACK_API_KEY")
)

OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY")
RAILRADAR_API_KEY = os.getenv("RAILRADAR_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

WEATHER_SERVER_PATH = BASE_DIR / "weather_mcp_custom.py"
TRAIN_SERVER_PATH = BASE_DIR / "train_mcp.py"
UVX_COMMAND = shutil.which("uvx") or "uvx"



def _require_env(name: str, value: str | None) -> str:
    if not value:
        raise RuntimeError(
            f"{name} is missing. Add {name}=your_key to the project .env file."
        )
    return value



def _subprocess_env(**updates: str | None) -> dict[str, str]:
    env = os.environ.copy()
    for key, value in updates.items():
        if value:
            env[key] = value
    return env


llm = ChatGroq(
    model="openai/gpt-oss-120b",
    api_key=_require_env("GROQ_API_KEY", GROQ_API_KEY),
    temperature=0.2,
    max_retries=2,
)


client = MultiServerMCPClient(
    {
        "tavily": {
            "transport": "streamable_http",
            "url": f"https://mcp.tavily.com/mcp/?tavilyApiKey={TAVILY_API_KEY or ''}",
        },
        "aviationstack": {
            "transport": "stdio",
            "command": UVX_COMMAND,
            "args": ["aviationstack-mcp"],
            "env": _subprocess_env(
                AVIATION_STACK_API_KEY=AVIATION_STACK_API_KEY,
            ),
        },
        "weather": {
            "transport": "stdio",
            "command": sys.executable,
            "args": [str(WEATHER_SERVER_PATH)],
            "env": _subprocess_env(
                OPENWEATHER_API_KEY=OPENWEATHER_API_KEY,
            ),
        },
        "train": {
            "transport": "stdio",
            "command": sys.executable,
            "args": [str(TRAIN_SERVER_PATH)],
            "env": _subprocess_env(
                RAILRADAR_API_KEY=RAILRADAR_API_KEY,
            ),
        },
    }
)


async def _get_server_tool(server_name: str, tool_name: str):
    if server_name == "tavily":
        _require_env("TAVILY_API_KEY", TAVILY_API_KEY)

    elif server_name == "aviationstack":
        _require_env("AVIATION_STACK_API_KEY", AVIATION_STACK_API_KEY)
        if shutil.which("uvx") is None:
            raise RuntimeError(
                "uvx was not found. Install uv, reopen the terminal, "
                "activate the travel environment, and run `uvx --version`."
            )

    elif server_name == "weather":
        _require_env("OPENWEATHER_API_KEY", OPENWEATHER_API_KEY)
        if not WEATHER_SERVER_PATH.is_file():
            raise FileNotFoundError(
                f"Weather MCP server not found: {WEATHER_SERVER_PATH}"
            )

    elif server_name == "train":
        _require_env("RAILRADAR_API_KEY", RAILRADAR_API_KEY)
        if not TRAIN_SERVER_PATH.is_file():
            raise FileNotFoundError(
                f"Train MCP server not found: {TRAIN_SERVER_PATH}"
            )

    tools = await client.get_tools(server_name=server_name)

    tool = next(
        (item for item in tools if item.name == tool_name),
        None,
    )

    if tool is None:
        available_tools = ", ".join(
            sorted(item.name for item in tools)
        ) or "none"
        raise RuntimeError(
            f"MCP tool '{tool_name}' was not found on server '{server_name}'. "
            f"Available tools: {available_tools}"
        )

    return tool


async def get_all_tools() -> None:
    for server_name in ("tavily", "aviationstack", "weather", "train"):
        try:
            tools = await client.get_tools(server_name=server_name)
            tool_names = ", ".join(tool.name for tool in tools) or "no tools"
            print(f"{server_name}: OK -> {tool_names}", flush=True)
        except Exception as exc:
            print(
                f"{server_name}: FAILED -> {type(exc).__name__}: {exc}",
                flush=True,
            )


async def tavily_mcp_search(query: str):
    search_tool = await _get_server_tool("tavily", "tavily_search")
    return await search_tool.ainvoke({"query": query})


async def aviation_mcp_call(
    tool_name: str,
    tool_args: dict[str, Any] | None = None,
):
    aviation_tool = await _get_server_tool("aviationstack", tool_name)
    return await aviation_tool.ainvoke(tool_args or {})


async def weather_mcp_search(city: str):
    weather_tool = await _get_server_tool("weather", "get_current_weather")
    return await weather_tool.ainvoke({"city": city})


async def forecast_mcp_search(city: str):
    forecast_tool = await _get_server_tool("weather", "get_forecast")
    return await forecast_tool.ainvoke({"city": city})


async def train_station_search(query: str):
    station_tool = await _get_server_tool("train", "search_stations")
    return await station_tool.ainvoke({"query": query})


async def train_mcp_search(
    from_location: str,
    to_location: str,
    date: str = "",
    by_city: bool = True,
):
    train_tool = await _get_server_tool("train", "search_trains")
    return await train_tool.ainvoke(
        {
            "from_location": from_location,
            "to_location": to_location,
            "date": date,
            "by_city": by_city,
        }
    )



def _content_to_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        if isinstance(content.get("text"), str):
            return content["text"]
        if isinstance(content.get("content"), (str, list, dict)):
            return _content_to_text(content["content"])
        return str(content)
    if isinstance(content, (list, tuple)):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
            else:
                text = getattr(item, "text", None)
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    text = getattr(content, "text", None)
    return text if isinstance(text, str) else str(content)



def extract_destination(query: str) -> str:
    response = llm.invoke(
        [
            {
                "role": "system",
                "content": (
                    "Extract only the destination city or country from the travel request. "
                    "Return only the destination name and nothing else."
                ),
            },
            {
                "role": "user",
                "content": query,
            },
        ]
    )

    destination = _content_to_text(response.content).strip()

    if not destination:
        raise ValueError("The destination could not be extracted.")

    return destination
