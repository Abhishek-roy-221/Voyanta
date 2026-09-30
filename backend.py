import os
import certifi
from dotenv import load_dotenv
load_dotenv()

from typing import TypedDict, Annotated ,Any
import operator
import uuid
import asyncio
import json

import psycopg
from psycopg.rows import dict_row
from psycopg import AsyncConnection
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.types import Command,interrupt
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.postgres import PostgresSaver
from langchain_core.messages import (AnyMessage,HumanMessage,AIMessage,SystemMessage)
from langchain_groq import ChatGroq
from pydantic import BaseModel, Field
# from tools.tavily_tool import tavily_search
# from tools.flight_tool import search_flights


from mcp_client import (
    tavily_mcp_search,
    aviation_mcp_call,
    extract_destination,
    forecast_mcp_search,
    weather_mcp_search,
    train_mcp_search,
    train_station_search,
)

os.environ["SSL_CERT_FILE"] = certifi.where()
os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()


def get_database_url():
    database_url = os.getenv("DATABASE_URL")

    if not database_url:
        raise ValueError(
            "DATABASE_URL is missing. Please add your Render PostgreSQL External Database URL to .env"
        )

    if "sslmode=" not in database_url:
        separator = "&" if "?" in database_url else "?"
        database_url = f"{database_url}{separator}sslmode=require"

    return database_url

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
if not GROQ_API_KEY:
    raise ValueError("GROQ_API_KEY is missing. Please add it to your .env file.")

GROQ_MODEL = "openai/gpt-oss-120b"

llm = ChatGroq(
    model=GROQ_MODEL,
    api_key=GROQ_API_KEY,
    temperature=0.2,
    max_retries=2,
)


# state

class TravelState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], operator.add]
    user_query: str

    # Supervisor + guardrail state
    guardrail_allowed: bool
    guardrail_reason: str
    selected_agents: list[str]
    trip_constraints: dict[str, Any]
    supervisor_reasoning: str

    # Original specialist results
    flight_results: str
    train_results: str
    hotel_results: str
    weather_results: str
    itinerary: str

    # New budget + HITL state
    budget_results: str
    approval_request: str
    approved: bool
    human_feedback: str
    final_response: str

    llm_calls: int


# class TravelState(TypedDict):
#     user_query: str
#     messages: Annotated[list[AnyMessage],operator.add]
#     flight_results: str
#     hotel_results: str
#     itinerary: str
#     llm_calls: int
#     weather_results: str


# Nodes

# def flight_agent(state: TravelState):
#     query = state["user_query"]
#     flight_data = search_flights(query)

#     return {
#         "flight_results": flight_data,
#         "messages": [
#             AIMessage(content="flight results fetched")
#         ],
#         "llm_calls": state.get("llm_calls",0) + 1
#     }

# shared helpers
KNOWN_AGENTS = [
    "flight_agent",
    "train_agent",
    "hotel_agent",
    "weather_agent",
    "budget_agent",
    "itinerary_agent",
]

AGENT_ORDER = [
    "flight_agent",
    "train_agent",
    "hotel_agent",
    "weather_agent",
    "budget_agent",
    "itinerary_agent",
]


# Structured-output schemas (Groq strict JSON schema / LangChain with_structured_output)

class GuardrailDecision(BaseModel):
    allowed: bool = Field(
        description="True if the request is about travel planning or travel information."
    )
    reason: str = Field(
        description="Short user-facing reason. Use an empty string when allowed is true.",
    )


class TripConstraints(BaseModel):
    origin: str = Field(description="Departure city/place exactly as the user gave it. Empty if not stated.")
    destination: str = Field(description="Destination city/place. Empty if not stated.")
    duration: str = Field(description="Trip length, e.g. '5 days'. Empty if not stated.")
    travel_date: str = Field(description="Travel date or month if stated. Empty otherwise.")
    budget: str = Field(description="Budget as stated, e.g. '₹30,000'. Empty if not stated.")
    num_travelers: str = Field(description="Number of travelers if stated. Empty otherwise.")
    travel_style: str = Field(description="Style such as budget, luxury, family, adventure. Empty if not stated.")
    transportation_preference: str = Field(description="Train, flight, both, etc. if stated. Empty otherwise.")
    hotel_requirements: str = Field(description="Explicit hotel requirements if stated. Empty otherwise.")
    weather_requirements: str = Field(description="Explicit weather needs if stated. Empty otherwise.")
    special_preferences: list[str] = Field(
        description="Other explicit preferences or constraints stated by the user. Use an empty list when none are stated.",
    )


class SupervisorDecision(BaseModel):
    selected_agents: list[str] = Field(
        description=(
            "Agents to run. Allowed values: flight_agent, train_agent, hotel_agent, "
            "weather_agent, budget_agent, itinerary_agent."
        )
    )
    trip_constraints: TripConstraints
    reasoning: str = Field(description="One or two sentences explaining the routing.")


class StationCandidate(BaseModel):
    station: str = Field(description="Station name.")
    code: str = Field(description="Railway station code.")


class StationSelection(BaseModel):
    origin_candidates: list[StationCandidate] = Field(description="Candidate origin stations. Use an empty list if none can be determined.")
    destination_candidates: list[StationCandidate] = Field(description="Candidate destination stations. Use an empty list if none can be determined.")


class TrainInfo(BaseModel):
    number: str
    name: str
    type: str
    departure: str
    arrival: str
    duration: str
    distance_km: str
    running_days: str


class TrainSummary(BaseModel):
    trains: list[TrainInfo] = Field(description="Verified train results. Use an empty list when no trains are available.")

class HotelInfo(BaseModel):
    name: str = Field(description="Actual hotel/property name, not a listing-page title.")
    area: str = Field(description="Neighbourhood/area or location text if stated. Empty if not stated.")
    category: str = Field(description="Star class or type (budget, 3-star, 5-star, etc.) if stated. Empty if not stated.")
    rating: str = Field(description="Rating exactly as stated, e.g. '8.6/10'. Empty if not stated.")
    price_per_night: str = Field(description="Price exactly as written incl. currency symbol, e.g. '₹2,499'. Empty if not stated.")
    highlights: str = Field(description="Short factual highlights stated in the text (breakfast, pool, free cancellation...). Empty if not stated.")
    source_title: str = Field(description="Title of the page this came from. Empty if not available.")
    source_url: str = Field(description="URL of the page this came from. Empty if not available.")


class HotelSummary(BaseModel):
    hotels: list[HotelInfo] = Field(description="Hotel results. Use an empty list when no verified hotels are available.")
    price_note: str = Field(description="One line about price currency/coverage limits found in the data. Empty if no note is needed.")
# Groq response helpers

def _content_to_text(content: Any) -> str:
    """Convert LangChain/Gemini message content (str, content blocks, dicts) to plain text."""
    if content is None:
        return ""

    if isinstance(content, str):
        return content

    if isinstance(content, dict):
        text = content.get("text")
        if isinstance(text, str):
            return text
        return json.dumps(content, ensure_ascii=False, default=str)

    if isinstance(content, (list, tuple)):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                block_type = item.get("type")
                text = item.get("text")
                # Only visible text blocks; skip thinking/tool/other blocks.
                if isinstance(text, str) and block_type in (None, "text"):
                    parts.append(text)
            else:
                text = getattr(item, "text", None)
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)

    text = getattr(content, "text", None)
    if isinstance(text, str):
        return text

    return str(content)


def _data_to_text(value: Any) -> str:
    """Convert tool output (str / dict / list / MCP-style blocks) to readable text without losing fields."""
    if value is None:
        return ""

    if isinstance(value, str):
        return value

    if isinstance(value, (list, tuple)) and value and all(
        isinstance(item, dict) and item.get("type") == "text" for item in value
    ):
        return _content_to_text(value)

    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, indent=2, default=str)

    content = getattr(value, "content", None)
    if content is not None:
        return _data_to_text(content)

    return str(value)


def _json_safe(value: Any) -> Any:
    """Make any value safe for JSONResponse."""
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))
    except Exception:
        return str(value)


def _looks_like_api_error(text: str) -> bool:
    lowered = text.lower()
    markers = (
        "api_error",
        "function_access_restricted",
        "does not support this api function",
        "subscription plan",
        "invalid_access_key",
        "usage_limit_reached",
        "access_restricted",
    )
    return any(marker in lowered for marker in markers)


def _llm_text(system_prompt: str, user_prompt: str) -> str:
    response = llm.invoke(
        [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ]
    )
    return _content_to_text(response.content)


def _llm_structured(system_prompt: str, user_prompt: str, schema: type[BaseModel]):
    """Invoke Groq with native structured output and return a validated schema instance."""
    messages = [
        SystemMessage(
            content=(
                system_prompt
                + "\nReturn ONLY a JSON object that matches the requested schema. "
                "Do not add commentary outside the JSON object."
            )
        ),
        HumanMessage(content=user_prompt),
    ]

    structured_llm = llm.with_structured_output(
        schema,
        method="json_schema",
        strict=True,
    )
    result = structured_llm.invoke(messages)

    if isinstance(result, schema):
        return result
    if isinstance(result, dict):
        return schema.model_validate(result)
    if isinstance(result, BaseModel):
        return schema.model_validate(result.model_dump())

    raise ValueError(f"Structured output was not returned for {schema.__name__}.")


def _empty_constraints() -> dict[str, Any]:
    return {
        "destination": "",
        "origin": "",
        "duration": "",
        "travel_date": "",
        "budget": "",
        "num_travelers": "",
        "travel_style": "",
        "transportation_preference": "",
        "hotel_requirements": "",
        "weather_requirements": "",
        "special_preferences": [],
    }


def _fallback_constraints(query: str) -> dict[str, Any]:
    """Best-effort deterministic extraction used only when supervisor LLM parsing fails."""
    import re

    constraints = _empty_constraints()
    text = query.strip()

    route = re.search(
        r"\bfrom\s+(.+?)\s+to\s+(.+?)(?:\s+(?:for|with|on|in|under|budget|starting)|$)",
        text,
        flags=re.IGNORECASE,
    )
    if route:
        constraints["origin"] = route.group(1).strip(" ,.-")
        constraints["destination"] = route.group(2).strip(" ,.-")
    else:
        route = re.search(
            r"\b([A-Za-z][A-Za-z .'-]{1,40})\s+to\s+([A-Za-z][A-Za-z .'-]{1,40})(?:\s+(?:for|with|on|in|under|budget|and)|$)",
            text,
            flags=re.IGNORECASE,
        )
        if route:
            constraints["origin"] = route.group(1).strip(" ,.-")
            constraints["destination"] = route.group(2).strip(" ,.-")

    duration = re.search(r"\b(\d+)\s*(day|days|night|nights)\b", text, re.IGNORECASE)
    if duration:
        constraints["duration"] = f"{duration.group(1)} {duration.group(2)}"

    travelers = re.search(
        r"\b(?:for|with)\s+(\d+)\s+(?:people|persons|travelers|travellers|adults)\b",
        text,
        flags=re.IGNORECASE,
    )
    if travelers:
        constraints["num_travelers"] = travelers.group(1)

    budget = re.search(
        r"(?:₹|Rs\.?|INR\s*)\s*([\d,]+)",
        text,
        flags=re.IGNORECASE,
    )
    if budget:
        constraints["budget"] = f"₹{budget.group(1)}"

    lowered = text.lower()
    if any(word in lowered for word in ("train", "railway", "rail")):
        constraints["transportation_preference"] = "train"
    elif any(word in lowered for word in ("flight", "flights", "airplane", "air travel")):
        constraints["transportation_preference"] = "flight"

    if any(word in lowered for word in ("hotel", "stay", "accommodation", "resort")):
        constraints["hotel_requirements"] = "Requested in user query"
    if any(word in lowered for word in ("weather", "climate", "forecast")):
        constraints["weather_requirements"] = "Requested in user query"

    return constraints


def _fallback_selected_agents(query: str, constraints: dict[str, Any]) -> list[str]:
    lowered = query.lower()
    selected: list[str] = []

    if any(word in lowered for word in ("flight", "flights", "airline", "airport", "fly", "air travel")):
        selected.append("flight_agent")
    if any(word in lowered for word in ("train", "railway", "rail", "vande bharat", "express train")):
        selected.append("train_agent")

    domestic_hint = bool(constraints.get("origin") and constraints.get("destination"))
    if domestic_hint and not selected and any(
        word in lowered for word in ("trip", "travel", "plan", "itinerary", "holiday", "vacation")
    ):
        selected.extend(["flight_agent", "train_agent"])

    if any(word in lowered for word in ("hotel", "stay", "accommodation", "resort")) or "trip" in lowered or "travel" in lowered:
        selected.append("hotel_agent")
    if any(word in lowered for word in ("weather", "climate", "forecast", "packing")) or "trip" in lowered or "travel" in lowered:
        selected.append("weather_agent")
    if constraints.get("budget") or any(word in lowered for word in ("budget", "cost", "price", "how much", "expenses")) or "trip" in lowered or "travel" in lowered:
        selected.append("budget_agent")

    selected.append("itinerary_agent")
    return [agent for agent in AGENT_ORDER if agent in selected]



# Presentation helpers (deterministic - no LLM, no invented data)

def _maybe_json(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return value
    return value


def _md_cell(value: Any) -> str:
    text = str(value if value is not None else "").replace("|", "\\|").replace("\n", " ").strip()
    return text or "-"


def _fmt_num(value: Any) -> str:
    try:
        return f"{round(float(value), 1)}"
    except Exception:
        return _md_cell(value)


def _short_api_error(text: str) -> str:
    parsed = _maybe_json(text.strip())
    if isinstance(parsed, dict):
        err = parsed.get("error") or parsed.get("message")
        if err:
            return str(err)
    return text.strip()[:300]


def _md_table(headers: list[str], rows: list[list[Any]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join(["---"] * len(headers)) + "|",
    ]
    for row in rows:
        lines.append("| " + " | ".join(_md_cell(cell) for cell in row) + " |")
    return "\n".join(lines)


def _preview_trains(train: Any) -> str:
    if not train:
        return "Train search did not return any result."
    if isinstance(train, str):
        return train

    trains = train.get("trains") or []
    origin = " ".join(x for x in [train.get("origin_station", ""), f"({train['origin_code']})" if train.get("origin_code") else ""] if x)
    dest = " ".join(x for x in [train.get("destination_station", ""), f"({train['destination_code']})" if train.get("destination_code") else ""] if x)

    out = []
    if origin or dest:
        out.append(f"**Verified route:** {origin or '-'} → {dest or '-'} · Source: {train.get('data_source', 'RailRadar')}")
    if not trains:
        out.append("No trains were returned for this route.")
        return "\n\n".join(out)

    out.append(
        _md_table(
            ["Train", "Name", "Type", "Departure", "Arrival", "Duration", "Distance (km)", "Running days"],
            [
                [t.get("number"), t.get("name"), t.get("type"), t.get("departure"),
                 t.get("arrival"), t.get("duration"), t.get("distance_km"), t.get("running_days")]
                for t in trains
            ],
        )
    )
    return "\n\n".join(out)


def _preview_flights(flights: Any) -> str:
    text = _data_to_text(flights).strip()
    if not text:
        return "Flight search did not return any result."
    if text.lower().startswith("flight data unavailable") or text.lower().startswith("flight information unavailable"):
        return "> " + text.replace("\n", "\n> ")
    return text


def _preview_hotels(raw: Any) -> str:
    data = _maybe_json(raw)
    if not isinstance(data, dict):
        return _data_to_text(raw) or "Hotel search did not return any result."

    hotels = data.get("hotels") or []
    if not hotels:
        return "No hotel details could be extracted from the search results."

    rows = []
    for h in hotels:
        url = h.get("source_url", "")
        title = h.get("source_title", "") or "Source"
        source = f"[{title[:40]}]({url})" if url else "-"
        rows.append([
            f"**{h.get('name', '-')}**",
            h.get("area"),
            h.get("category"),
            h.get("rating"),
            h.get("price_per_night") or "Not listed",
            h.get("highlights"),
            source,
        ])

    out = [_md_table(["Hotel", "Area", "Category", "Rating", "Price / night", "Highlights", "Source"], rows)]
    if data.get("price_note"):
        out.append(f"_{data['price_note']}_")
    out.append("_Prices are copied from search-result snippets exactly as listed (currency not converted) and may change. Confirm on the booking site._")
    return "\n\n".join(out)


def _preview_weather(raw: Any) -> str:
    data = _maybe_json(raw)
    if not isinstance(data, dict):
        return _data_to_text(raw) or "Weather data unavailable."

    current = _maybe_json(data.get("current"))
    forecast = _maybe_json(data.get("forecast"))
    out = []

    if isinstance(current, dict):
        rows = []
        for key, label, suffix in (
            ("temperature_c", "Temperature", " °C"),
            ("feels_like_c", "Feels like", " °C"),
            ("condition", "Condition", ""),
            ("humidity", "Humidity", " %"),
            ("wind_speed", "Wind speed", ""),
        ):
            value = current.get(key)
            if value not in (None, ""):
                shown = _fmt_num(value) if key.endswith("_c") or key == "wind_speed" else value
                rows.append([label, f"{shown}{suffix}"])
        if rows:
            out.append("**Current weather**\n\n" + _md_table(["Metric", "Value"], rows))
    elif isinstance(current, str) and current.strip():
        out.append(current.strip())

    items = forecast.get("forecast") if isinstance(forecast, dict) else forecast
    if isinstance(items, list) and items:
        rows = [
            [i.get("datetime"), _fmt_num(i.get("temperature_c")), i.get("condition")]
            for i in items if isinstance(i, dict)
        ]
        if rows:
            out.append("**Forecast**\n\n" + _md_table(["Time", "Temp (°C)", "Condition"], rows))
    elif isinstance(forecast, str) and forecast.strip():
        out.append(forecast.strip())

    return "\n\n".join(out) or "Weather data unavailable."


def _build_plan_preview(state: dict[str, Any]) -> str:
    """Readable draft shown at the approval step, built only from data the agents returned."""
    selected = state.get("selected_agents", []) or []
    constraints = state.get("trip_constraints", {}) or {}

    parts: list[str] = [
        "# Draft Travel Plan\n\n_Review the draft below. Approve it to get the polished final plan, or request changes._"
    ]

    labels = [
        ("origin", "Origin"), ("destination", "Destination"), ("duration", "Duration"),
        ("travel_date", "Travel date"), ("budget", "Budget"), ("num_travelers", "Travelers"),
        ("transportation_preference", "Transport"), ("travel_style", "Style"),
    ]
    rows = [[label, constraints.get(key)] for key, label in labels if constraints.get(key)]
    prefs = constraints.get("special_preferences") or []
    if prefs:
        rows.append(["Preferences", ", ".join(str(p) for p in prefs)])
    if rows:
        parts.append("## Trip Summary\n\n" + _md_table(["Detail", "Value"], rows))

    transport = []
    if "train_agent" in selected:
        transport.append("### Trains\n\n" + _preview_trains(state.get("train_results")))
    if "flight_agent" in selected:
        transport.append("### Flights\n\n" + _preview_flights(state.get("flight_results")))
    if transport:
        parts.append("## Transportation\n\n" + "\n\n".join(transport))

    if "hotel_agent" in selected:
        parts.append("## Hotels\n\n" + _preview_hotels(state.get("hotel_results")))

    if "weather_agent" in selected:
        parts.append("## Weather\n\n" + _preview_weather(state.get("weather_results")))

    if "budget_agent" in selected and state.get("budget_results"):
        parts.append("## Budget\n\n" + _content_to_text(state.get("budget_results")))

    itinerary = _content_to_text(state.get("itinerary", ""))
    if itinerary:
        parts.append("## Itinerary\n\n" + itinerary)

    return "\n\n".join(parts) + "\n"

# Supervisor Agent + Input Guardrail

GUARDRAIL_SYSTEM_PROMPT = (
    "You are the input guardrail for Voyanta, a travel-planning application. "
    "Decide only whether the request is allowed."
)

GUARDRAIL_PROMPT = """
Decide whether the user's request is about travel planning or travel information.

ALLOW requests about: trips and itineraries, destinations, flights, trains,
hotels and accommodation, weather, budgets and costs, visas, local transport,
sightseeing, food while travelling, and packing.

ALLOW a travel request even when details are missing (no dates, budget, or
origin). Missing information is never a reason to block.

BLOCK only requests that are clearly unrelated to travel (for example coding
help, homework, or general chat) or that ask for harmful or illegal
instructions. If blocked, give a short, polite reason that tells the user
Voyanta helps with travel planning.

User request:
{query}
"""

SUPERVISOR_SYSTEM_PROMPT = (
    "You are the supervisor of Voyanta, a multi-agent travel-planning system. "
    "You extract trip details and route work to specialist agents."
)

SUPERVISOR_PROMPT = """
Read the user's travel request, extract the trip details, and choose which
specialist agents should run.

Available agents:
- flight_agent: flights, airports, airlines, air routes
- train_agent: train routes, schedules and railway travel (India rail data)
- hotel_agent: hotels and accommodation search
- weather_agent: current weather and forecast for the destination
- budget_agent: trip cost breakdown and budget feasibility
- itinerary_agent: day-by-day itinerary (always required)

Extract trip_constraints ONLY from what the user actually wrote:
origin, destination, duration, travel_date, budget, num_travelers,
travel_style, transportation_preference, hotel_requirements,
weather_requirements, special_preferences.
Leave a field empty ("" or []) when the user did not state it. Never guess or
invent values.

Transportation rules:
1. Flights or air travel requested -> flight_agent.
2. Trains or railways requested -> train_agent.
3. Comparison of flights and trains -> both.
4. Domestic trip with no stated transport preference -> both flight_agent and train_agent.
5. International trip with no stated preference -> flight_agent only.
6. Never add train_agent for international trips unless the user explicitly asks for trains.

Other agent rules:
- hotel_agent: select when the user asks about hotels/stay/accommodation, or asks for a full trip plan.
- weather_agent: select when the user asks about weather/climate/packing, or asks for a full trip plan.
- budget_agent: select when the user mentions a budget, cost, or price, or asks for a full trip plan.
- itinerary_agent: ALWAYS select it.
- Do not select agents that are clearly irrelevant to the request.

Give a one or two sentence reasoning for your routing.

User request:
{query}
"""


def supervisor_agent(state: TravelState):
    query = state["user_query"]
    llm_calls = state.get("llm_calls", 0)

    # Fail open on model errors so a temporary issue does not block valid travel requests.
    try:
        decision = _llm_structured(
            GUARDRAIL_SYSTEM_PROMPT,
            GUARDRAIL_PROMPT.format(query=query),
            GuardrailDecision,
        )
        allowed = bool(decision.allowed)
        guardrail_reason = decision.reason.strip()
        llm_calls += 1
    except Exception as exc:
        print(f"Guardrail fallback used: {exc}")
        allowed = True
        guardrail_reason = "Guardrail validation fallback allowed the request."

    if not allowed:
        reason = guardrail_reason or (
            "Voyanta can only help with travel-planning requests. "
            "Please ask about a destination, flight, train, hotel, weather, budget, "
            "or itinerary."
        )
        return {
            "guardrail_allowed": False,
            "guardrail_reason": reason,
            "selected_agents": [],
            "trip_constraints": _empty_constraints(),
            "supervisor_reasoning": reason,
            "final_response": reason,
            "messages": [AIMessage(content=f"Guardrail blocked request: {reason}")],
            "llm_calls": llm_calls,
        }

    try:
        parsed = _llm_structured(
            SUPERVISOR_SYSTEM_PROMPT,
            SUPERVISOR_PROMPT.format(query=query),
            SupervisorDecision,
        )

        requested_agents = parsed.selected_agents
        selected_agents = [
            name for name in AGENT_ORDER
            if name in requested_agents and name in KNOWN_AGENTS
        ]

        # The itinerary agent integrates whichever specialist results were selected.
        if "itinerary_agent" not in selected_agents:
            selected_agents.append("itinerary_agent")

        constraints = _empty_constraints()
        for key, value in parsed.trip_constraints.model_dump().items():
            if value not in ("", None, []):
                constraints[key] = value

        reasoning = parsed.reasoning.strip()
        llm_calls += 1
    except Exception as exc:
        print(f"Supervisor fallback used: {exc}")
        constraints = _fallback_constraints(query)
        selected_agents = _fallback_selected_agents(query, constraints)
        reasoning = (
            "Supervisor structured output failed, so Voyanta used deterministic "
            "keyword and route extraction as a fallback."
        )

    return {
        "guardrail_allowed": True,
        "guardrail_reason": guardrail_reason,
        "selected_agents": selected_agents,
        "trip_constraints": constraints,
        "supervisor_reasoning": reasoning,
        "messages": [AIMessage(content="Supervisor created the agent plan.")],
        "llm_calls": llm_calls,
    }



# Guardrail blocked response

def guardrail_blocked_agent(state: TravelState):
    reason = state.get("final_response") or state.get("guardrail_reason") or (
        "This request was blocked by the travel input guardrail."
    )
    return {
        "final_response": reason,
        "messages": [AIMessage(content=reason)],
    }



# Flight Tool Router Prompt
FLIGHT_AGENT_PROMPT = """
You are the flight research agent for Voyanta.

User query:
{query}

Trip constraints extracted by the supervisor:
{constraints}

Data retrieved from AviationStack (this is the ONLY verified data you have):

Airport data status: {airport_status}
Airport data:
{airport_data}

Airline data status: {airline_status}
Airline data:
{airline_data}

Rules:
- Use ONLY the data above as verified facts. The airport/airline lists are general
  reference data, not live schedules for this route.
- Do NOT invent flight numbers, schedules, departure/arrival times, prices,
  fares, seat availability, or which airlines operate this exact route.
- If a data status says UNAVAILABLE, say plainly that this part of the flight
  data source could not be retrieved. Do not fill the gap with guesses.
- You may list airports (name, IATA/ICAO code, city, country) or airlines that
  appear in the data and clearly match the origin or destination city/country.
  Preserve every useful field shown for them.
- If nothing in the data matches the route, say so.
- You may add clearly labelled general guidance (e.g. "general knowledge, not
  verified by the data source") such as which city airports are commonly used,
  but never present it as verified data and never give prices.

Output Markdown with these parts (omit a part if there is nothing to say):
**Verified data** - matching airports/airlines with all available fields.
**Not available** - what the flight data source could not provide.
**General guidance (unverified)** - brief, clearly labelled.
"""


# Flight Agent

def flight_agent(state: TravelState):
    print("\nINSIDE FLIGHT AGENT\n")
    query = state["user_query"]
    constraints = state.get("trip_constraints", {})

    try:
        airports_text = ""
        airlines_text = ""
        airport_error = ""
        airline_error = ""

        try:
            airports = asyncio.run(aviation_mcp_call("list_airports"))
            airports_text = _data_to_text(airports)
            if _looks_like_api_error(airports_text):
                airport_error = _short_api_error(airports_text)
        except Exception as exc:
            airport_error = f"{type(exc).__name__}: {exc}"

        try:
            airlines = asyncio.run(aviation_mcp_call("list_airlines"))
            airlines_text = _data_to_text(airlines)
            if _looks_like_api_error(airlines_text):
                airline_error = _short_api_error(airlines_text)
        except Exception as exc:
            airline_error = f"{type(exc).__name__}: {exc}"

        print("\nAIRPORTS:", airports_text[:500])
        print("\nAIRLINES:", airlines_text[:500])

        if airport_error and airline_error:
            # Nothing verified is available - do NOT call the LLM, do NOT invent anything.
            reason = airport_error if airport_error == airline_error else f"{airport_error} / {airline_error}"
            flight_data = (
                "Flight data unavailable: the flight data source (AviationStack) "
                f"rejected the request ({reason}). "
                "No flight numbers, airlines, schedules or fares are shown because "
                "they could not be verified."
            )
            return {
                "flight_results": flight_data,
                "messages": [AIMessage(content="Flight data source unavailable")],
            }

        prompt = FLIGHT_AGENT_PROMPT.format(
            query=query,
            constraints=constraints,
            airport_status=(
                f"UNAVAILABLE ({airport_error})" if airport_error else "OK"
            ),
            airport_data=(
                "None" if airport_error else airports_text[:3000]
            ),
            airline_status=(
                f"UNAVAILABLE ({airline_error})" if airline_error else "OK"
            ),
            airline_data=(
                "None" if airline_error else airlines_text[:3000]
            ),
        )

        response = llm.invoke(
            [
                SystemMessage(
                    content=(
                        "You are a careful flight research agent. "
                        "You never invent flight data."
                    )
                ),
                HumanMessage(content=prompt),
            ]
        )
        flight_data = _content_to_text(response.content)
    except Exception as exc:
        flight_data = f"Flight information unavailable: {exc}"

    return {
        "flight_results": flight_data,
        "messages": [AIMessage(content="Flight recommendations generated")],
        "llm_calls": state.get("llm_calls", 0) + 1,
    }



def train_agent(state: TravelState) -> dict:
    query = state["user_query"]
    constraints = state.get("trip_constraints", {})

    origin = constraints.get("origin")
    destination = constraints.get("destination")

    if not origin or not destination:
        return {
            "train_results": (
                "Train search could not be completed because "
                "origin or destination is missing."
            )
        }

    try:
        origin_search = asyncio.run(
            train_station_search(origin)
        )

        destination_search = asyncio.run(
            train_station_search(destination)
        )

        station_prompt = f"""
You are the railway location reasoning agent for Voyanta.

User request:
{query}

Origin:
{origin}

Destination:
{destination}

Station search results for origin:
{_data_to_text(origin_search)}

Station search results for destination:
{_data_to_text(destination_search)}

Determine the railway stations most likely to serve this journey.

Return at most 2 candidate stations for each location.

Rules:
- Prefer major intercity railway stations serving the requested city or metropolitan area.
- Prefer station codes that appear in the supplied station-search results.
- A station must be geographically and semantically appropriate for the requested city.
- Do not select a similarly named station in another city just because its name matches.
- Do not invent station codes when the search evidence does not support them.
- Order candidates from most appropriate to least appropriate.
- These are candidate codes only. RailRadar verification is the source of truth.
- Never claim that a route exists until RailRadar returns trains for the candidate pair.
"""

        station_data = _llm_structured(
            "You are a railway location reasoning agent for Voyanta.",
            station_prompt,
            StationSelection,
        )

        origin_candidates = [c.model_dump() for c in station_data.origin_candidates]
        destination_candidates = [c.model_dump() for c in station_data.destination_candidates]

        if not origin_candidates or not destination_candidates:
            return {
                "train_results": (
                    f"Could not determine railway stations for "
                    f"{origin} to {destination}."
                )
            }

        verified_route = None

        for origin_candidate in origin_candidates:
            for destination_candidate in destination_candidates:

                origin_code = origin_candidate.get("code")
                destination_code = destination_candidate.get("code")

                if not origin_code or not destination_code:
                    continue

                train_result = asyncio.run(
                    train_mcp_search(
                        from_location=origin_code,
                        to_location=destination_code,
                        date="",
                        by_city=False,
                    )
                )

                result_text = _data_to_text(train_result)

                if (
                    "trains" in result_text.lower()
                    and '"count": 0' not in result_text
                ):
                    verified_route = {
                        "origin": origin_candidate,
                        "destination": destination_candidate,
                        "result": result_text,
                    }
                    break

            if verified_route:
                break

        if not verified_route:
            return {
                "train_results": (
                    f"No verified train routes were found between "
                    f"{origin} and {destination}."
                )
            }

        origin_station = verified_route["origin"]["station"]
        origin_code = verified_route["origin"]["code"]

        destination_station = verified_route["destination"]["station"]
        destination_code = verified_route["destination"]["code"]

        train_result = verified_route["result"]

        summary_prompt = f"""
You are the Train Research Agent for Voyanta.

User request:
{query}

Verified route:
{origin_station} ({origin_code}) -> {destination_station} ({destination_code})

RailRadar data (the ONLY source of truth):
{train_result[:20000]}

Extract the trains from the RailRadar data into the schema.

Rules:
- Include at most 8 trains.
- Use ONLY information present in the RailRadar data.
- Copy train number, name, type, departure, arrival, duration, distance and
  running days exactly as provided. If a field is missing, leave it empty.
- Do not invent or estimate any value.
- Do not include fares, seat availability, booking advice or recommendations.
"""

        summary = _llm_structured(
            "You are the Train Research Agent for Voyanta. You only copy verified data.",
            summary_prompt,
            TrainSummary,
        )

        # Station names/codes come from the verified RailRadar route, not from the LLM.
        train_data = {
            "origin_station": origin_station,
            "origin_code": origin_code,
            "destination_station": destination_station,
            "destination_code": destination_code,
            "trains": [t.model_dump() for t in summary.trains],
            "data_source": "RailRadar",
        }

        return {
            "train_results": train_data,
            "messages": [AIMessage(content="Train information verified.")],
            "llm_calls": state.get("llm_calls", 0) + 2,
        }
    except Exception as exc:
        return {
            "train_results": (
                f"Train research failed: {type(exc).__name__}: {exc}"
            )
        }


HOTEL_EXTRACT_PROMPT = """
Extract hotel details from the web search results below.

Trip constraints:
{constraints}

Search results (the ONLY source of truth):
{raw_text}

Rules:
- Return up to 10 real hotels/properties whose names appear in the text.
  Do NOT return listing-page titles such as "10 BEST Hotels in ..." as hotels.
- Prefer a spread of budget, mid-range and premium options when the text allows it.
- price_per_night: copy exactly as written, including the currency symbol
  (e.g. "₹2,499" or "from ₪21"). Do NOT convert currencies and do NOT estimate.
  Leave empty if no price is stated for that hotel.
- rating, area, category and highlights: copy only what the text states; otherwise leave empty.
- source_url / source_title: the page the hotel information came from (URLs appear in the text).
- price_note: one short line if the prices are in mixed currencies or only "starting from" prices.
- Never invent hotels, prices, ratings or links.
"""


def hotel_agent(state: TravelState):
    constraints = state.get("trip_constraints", {})
    hotel_destination = constraints.get("destination")

    if hotel_destination:
        queries = [
            f"best hotels in {hotel_destination} price per night in INR ₹ rating",
            f"budget and mid-range hotels in {hotel_destination} price per night ₹",
        ]
    else:
        queries = [f"Best hotels for {state['user_query']}"]

    raw_chunks: list[str] = []
    for search_query in queries:
        try:
            raw_chunks.append(
                _data_to_text(asyncio.run(tavily_mcp_search(search_query)))
            )
        except Exception as exc:
            print(
                f"HOTEL AGENT MCP ERROR: "
                f"{type(exc).__name__}: {exc}",
                flush=True,
            )

    llm_calls = state.get("llm_calls", 0)

    if not raw_chunks:
        hotel_results = (
            "Live hotel search is temporarily unavailable, so no hotel "
            "names, prices or ratings could be retrieved."
        )
    else:
        raw_text = "\n\n".join(raw_chunks)
        try:
            summary = _llm_structured(
                "You extract hotel facts from search results and never invent data.",
                HOTEL_EXTRACT_PROMPT.format(
                    constraints=constraints,
                    raw_text=raw_text[:24000],
                ),
                HotelSummary,
            )
            hotels = [h.model_dump() for h in summary.hotels]
            llm_calls += 1

            if hotels:
                hotel_results = json.dumps(
                    {"hotels": hotels, "price_note": summary.price_note},
                    ensure_ascii=False,
                )
            else:
                hotel_results = raw_text[:6000]
        except Exception as exc:
            print(f"HOTEL EXTRACTION ERROR: {type(exc).__name__}: {exc}", flush=True)
            hotel_results = raw_text[:6000]

    return {
        "hotel_results": hotel_results,
        "messages": [
            AIMessage(
                content="Hotel information processed."
            )
        ],
        "llm_calls": llm_calls,
    }





def weather_agent(state: TravelState):
    constraints = state.get("trip_constraints", {})
    city = constraints.get("destination") or extract_destination(
        state["user_query"]
    )

    try:
        weather_data = asyncio.run(
            weather_mcp_search(city)
        )

        forecast_data = asyncio.run(
            forecast_mcp_search(city)
        )

        weather_results = json.dumps(
            {
                "city": city,
                "current": _maybe_json(_data_to_text(weather_data)),
                "forecast": _maybe_json(_data_to_text(forecast_data)),
            },
            ensure_ascii=False,
        )

    except Exception as exc:
        print(
            f"WEATHER AGENT MCP ERROR: "
            f"{type(exc).__name__}: {exc}",
            flush=True,
        )

        weather_results = (
            f"Live weather information for {city} "
            "is temporarily unavailable. Verify the forecast "
            "before departure."
        )

    return {
        "weather_results": weather_results,
        "messages": [
            AIMessage(
                content="Weather information processed."
            )
        ],
    }


# Budget Agent 

def budget_agent(state: TravelState):
    prompt = f"""
Create a practical budget breakdown for this trip.

User Query:
{state['user_query']}

Trip Constraints (only what the user stated):
{state.get('trip_constraints', {})}

Flight Results:
{state.get('flight_results', '') or 'Not requested / not run'}

Train Results (RailRadar - verified schedules, no fares included):
{_data_to_text(state.get('train_results', '')) or 'Not requested / not run'}

Hotel Results:
{state.get('hotel_results', '') or 'Not requested / not run'}

Weather Results:
{state.get('weather_results', '') or 'Not requested / not run'}

Produce a Markdown budget with these parts:
1. Budget table with rows for: transportation (to and from), accommodation,
   food, local transportation, activities/sightseeing, and estimated total.
   Add a column "Basis" that says either "Verified" (the price appears in the
   data above) or "Estimate" (your approximate figure).
2. Budget feasibility versus the user's stated budget (if none was stated, say so).
3. Main budget risks.
4. Money-saving suggestions.

Rules:
- Train and flight tools above do NOT provide fares. Never present a fare as
  verified. Give a rough range labelled "Estimate" and say it should be
  confirmed on the booking platform.
- Only mark a hotel price "Verified" if it literally appears in the hotel results.
- Scale the estimates to the number of travelers and days if they were stated;
  if not stated, say which assumption you used.
- If flight data was unavailable, say so and do not estimate flight details.
- Keep it compact and clear.
"""

    response = llm.invoke(
        [
            SystemMessage(content="You are a practical travel budget analyst. You clearly separate verified prices from estimates."),
            HumanMessage(content=prompt),
        ]
    )

    return {
        "budget_results": _content_to_text(response.content),
        "messages": [AIMessage(content="Budget assessment generated.")],
        "llm_calls": state.get("llm_calls", 0) + 1,
    }





def itinerary_agent(state: TravelState):
    prompt = f"""
Create a destination-specific day-by-day travel itinerary.

User Query:
{state['user_query']}

Trip Constraints (only what the user stated):
{state.get('trip_constraints', {})}

Flight Information:
{state.get('flight_results', '') or 'Not requested / not run'}

Train Information (RailRadar verified):
{_data_to_text(state.get('train_results', '')) or 'Not requested / not run'}

Hotel Information:
{state.get('hotel_results', '') or 'Not requested / not run'}

Weather Information:
{state.get('weather_results', '') or 'Not requested / not run'}

Budget Information:
{state.get('budget_results', '') or 'Not requested / not run'}

Create ONLY the day-by-day itinerary.

For each day include:
- Morning
- Afternoon
- Evening
- Travel/transfer timing when relevant
- Named places and activities

Rules:
- Use the requested number of days. If the duration was not stated, choose a
  sensible length and say so in one line.
- Use real, well-known places and areas of the destination (monuments,
  markets, neighbourhoods, food areas). Do not write generic lines such as
  "Explore the city".
- Group nearby places on the same day to reduce travel time.
- Day 1 and the last day should account for travel time; use the verified
  train/flight timings above only when they are actually provided.
- Use weather information to place outdoor/indoor activities when available.
- If specific hotels were found, you may mention where the traveler stays.
- Do NOT invent train/flight numbers, ticket prices, opening hours or
  availability.
- Keep each day concise and easy to scan.
- Do not repeat full train/flight details, and do not add booking guides,
  packing lists or generic safety tips.

The output should contain only the day-by-day itinerary.
"""

    response = llm.invoke(
        [
            SystemMessage(
                content="You are an expert destination-specific travel itinerary planner."
            ),
            HumanMessage(content=prompt),
        ]
    )

    approval_request = (
        "Please review the generated draft itinerary. "
        "Approve it to create the final polished plan, "
        "or provide feedback for revision."
    )

    return {
        "itinerary": _content_to_text(response.content),
        "approval_request": approval_request,
        "messages": [
            AIMessage(
                content="Draft itinerary created for human review."
            )
        ],
        "llm_calls": state.get("llm_calls", 0) + 1,
    }


# Human-in-the-Loop approval

def human_approval_agent(state: TravelState):
    
    review = interrupt(
        {
            "question": "Do you approve this itinerary?",
            "draft_itinerary": state.get("itinerary", ""),
            "approval_request": state.get("approval_request", ""),
            "selected_agents": state.get("selected_agents", []),
            "supervisor_reasoning": state.get("supervisor_reasoning", ""),
            "expected_response": {
                "approved": True,
                "feedback": "Optional revision feedback",
            },
        }
    )

    approved = bool(review.get("approved", False))
    human_feedback = str(review.get("feedback", "")).strip()

    return {
        "approved": approved,
        "human_feedback": human_feedback,
        "messages": [AIMessage(content="Human approval step completed.")],
    }





# Final Response Agent - original format kept, HITL feedback added

def final_agent(state: TravelState):
    if state.get("approved", False):
        review_instruction = (
            "The user approved the draft. Preserve its decisions while polishing it."
        )
    else:
        review_instruction = f"""
The user requested a revision. Apply this feedback carefully:
{state.get('human_feedback', '') or 'Improve the draft before finalizing it.'}
"""

    final_prompt = f"""
Write the final travel plan for the user in Markdown.

Human Review:
{review_instruction}

User Request:
{state['user_query']}

Supervisor Constraints (only what the user stated):
{state.get('trip_constraints', {})}

Agents that ran:
{state.get('selected_agents', [])}

=== FLIGHT DATA (AviationStack) ===
{state.get('flight_results', '') or 'Not run'}

=== TRAIN DATA (RailRadar verified, structured) ===
{_data_to_text(state.get('train_results', '')) or 'Not run'}

=== HOTEL DATA ===
{state.get('hotel_results', '') or 'Not run'}

=== WEATHER DATA ===
{state.get('weather_results', '') or 'Not run'}

=== BUDGET ANALYSIS ===
{state.get('budget_results', '') or 'Not run'}

=== DRAFT ITINERARY ===
{state.get('itinerary', '')}

Use these sections, and include a section ONLY if it has relevant information:

# Trip Summary
Origin, destination, duration, travelers, budget and preferences the user actually stated.

# Transportation
## Trains
For every train in the data show: number, name, type, origin station and code,
destination station and code, departure, arrival, duration, distance and
running days. Use a table when there are several trains. Only include fields
that exist.
## Flights
Show verified airport/airline details that are present. If the flight data says
it is unavailable or failed, state clearly that flight information could not be
retrieved from the flight data source. Do not add flight numbers, schedules or
prices of your own.

# Hotels
A table with: Hotel, Area, Rating, Price per night (exactly as listed, with its currency; write "Not listed" if missing), Highlights, and a source link. Do not convert currencies or estimate prices. If hotel search failed, say so and give only clearly labelled general area advice.

# Weather
Temperature, condition, humidity, forecast and other fields actually provided,
plus short weather-based advice.

# Budget
The breakdown from the budget analysis, keeping the distinction between
verified prices and estimates.

# Itinerary
The day-by-day plan, incorporating the human review instruction above.

Rules:
- Do not invent train/flight numbers, times, prices, hotel details or weather values.
- If a tool failed or was not available, say so plainly instead of filling the gap.
- Do not drop useful tool fields, and do not repeat the same information in several sections.
- Do not include a section for an agent that did not run.
- Be clear, practical and specific to the destination.
"""

    response = llm.invoke(
        [
            SystemMessage(
                content=(
                    "You are a professional AI travel planner. You only present "
                    "verified tool data as fact and clearly label estimates."
                )
            ),
            HumanMessage(content=final_prompt),
        ]
    )

    final_text = _content_to_text(response.content).strip() or state.get("itinerary", "")

    return {
        "final_response": final_text,
        "messages": [AIMessage(content=final_text)],
        "llm_calls": state.get("llm_calls", 0) + 1,
    }



# Dynamic Supervisor Routing

ROUTE_MAP = {
    "flight_agent": "flight_agent",
    "train_agent": "train_agent",
    "hotel_agent": "hotel_agent",
    "weather_agent": "weather_agent",
    "budget_agent": "budget_agent",
    "itinerary_agent": "itinerary_agent",
}


def _selected_agents(state: TravelState) -> list[str]:
    selected = state.get("selected_agents", [])
    return [agent for agent in AGENT_ORDER if agent in selected]


def route_from_supervisor(state: TravelState) -> str:
    if not state.get("guardrail_allowed", True):
        return "guardrail_blocked"

    selected = _selected_agents(state)
    return selected[0] if selected else "itinerary_agent"


def route_after_agent(current_agent: str):
    def route(state: TravelState) -> str:
        selected = _selected_agents(state)
        current_index = AGENT_ORDER.index(current_agent)

        for next_agent in AGENT_ORDER[current_index + 1 :]:
            if next_agent in selected:
                return next_agent

        return "itinerary_agent"

    return route



# creating Graph
graph = StateGraph(TravelState)

graph.add_node("supervisor", supervisor_agent)
graph.add_node("guardrail_blocked", guardrail_blocked_agent)
graph.add_node("flight_agent", flight_agent)
graph.add_node("train_agent", train_agent)
graph.add_node("hotel_agent", hotel_agent)
graph.add_node("weather_agent", weather_agent)
graph.add_node("budget_agent", budget_agent)
graph.add_node("itinerary_agent", itinerary_agent)
graph.add_node("human_approval", human_approval_agent)
graph.add_node("final_agent", final_agent)

graph.add_edge(START, "supervisor")
graph.add_conditional_edges("supervisor", route_from_supervisor, ROUTE_MAP)

graph.add_conditional_edges(
    "flight_agent", route_after_agent("flight_agent"), ROUTE_MAP
)
graph.add_conditional_edges(
    "train_agent",
    route_after_agent("train_agent"),
    ROUTE_MAP
)
graph.add_conditional_edges(
    "hotel_agent", route_after_agent("hotel_agent"), ROUTE_MAP
)
graph.add_conditional_edges(
    "weather_agent", route_after_agent("weather_agent"), ROUTE_MAP
)
graph.add_conditional_edges(
    "budget_agent", route_after_agent("budget_agent"), ROUTE_MAP
)

graph.add_edge("itinerary_agent", "human_approval")
graph.add_edge("human_approval", "final_agent")
graph.add_edge("final_agent", END)
graph.add_edge("guardrail_blocked", END)


# Postgres setup
DATABASE_URL = get_database_url()

_conn = psycopg.connect(
    DATABASE_URL,
    autocommit=True,
    row_factory=dict_row
)

checkpointer = PostgresSaver(_conn)
checkpointer.setup()

travel_graph = graph.compile(checkpointer=checkpointer)


# FAST api logic 

def _interrupt_payload(result: dict[str, Any]) -> dict[str, Any] | None:
    interrupts = result.get("__interrupt__", [])
    if not interrupts:
        return None

    first_interrupt = interrupts[0]
    payload = getattr(first_interrupt, "value", first_interrupt)
    return payload if isinstance(payload, dict) else {"value": payload}


def _serialize_result(
    result: dict[str, Any],
    thread_id: str,
) -> dict[str, Any]:
    messages = result.get("messages", [])
    last_message = _content_to_text(messages[-1].content) if messages else ""
    answer = result.get("final_response") or last_message
    interrupt_payload = _interrupt_payload(result)

    if interrupt_payload:
        answer = _build_plan_preview(result)

    # The assistant answer must always be a plain string for the frontend Markdown renderer.
    answer = _content_to_text(answer)

    draft_itinerary = _content_to_text(
        interrupt_payload.get("draft_itinerary", "")
        if interrupt_payload
        else result.get("itinerary", "")
    )

    payload = {
        "thread_id": thread_id,
        "answer": answer,
        "requires_approval": interrupt_payload is not None,
        "approval_request": _content_to_text(
            interrupt_payload.get("approval_request", "")
            if interrupt_payload
            else result.get("approval_request", "")
        ),
        "flight_results": _data_to_text(result.get("flight_results", "")),
        "train_results": result.get("train_results", ""),
        "hotel_results": _data_to_text(result.get("hotel_results", "")),
        "weather_results": _data_to_text(result.get("weather_results", "")),
        "budget_results": _content_to_text(result.get("budget_results", "")),
        "itinerary": draft_itinerary,
        "selected_agents": result.get("selected_agents", []),
        "trip_constraints": result.get("trip_constraints", {}),
        "supervisor_reasoning": result.get("supervisor_reasoning", ""),
        "guardrail_allowed": result.get("guardrail_allowed", True),
        "guardrail_reason": result.get("guardrail_reason", ""),
        "approved": result.get("approved"),
        "human_feedback": result.get("human_feedback", ""),
        "llm_calls": result.get("llm_calls", 0),
    }

    return _json_safe(payload)


def run_travel_agent(
    user_input: str,
    thread_id: str | None = None,
):
    """Start a new travel-planning run and pause at human approval."""
    if not thread_id:
        thread_id = f"user_{uuid.uuid4().hex}"

    config = {
        "configurable": {
            "thread_id": thread_id
        }
    }

    result =  travel_graph.invoke(
        {
            "messages": [
                HumanMessage(content=user_input)
            ],
            "user_query": user_input,
            "guardrail_allowed": True,
            "guardrail_reason": "",
            "selected_agents": [],
            "trip_constraints": _empty_constraints(),
            "supervisor_reasoning": "",
            "flight_results": "",
            "train_results": {},
            "hotel_results": "",
            "weather_results": "",
            "budget_results": "",
            "itinerary": "",
            "approval_request": "",
            "approved": False,
            "human_feedback": "",
            "final_response": "",
            "llm_calls": 0,
        },
        config=config,
    )

    return _serialize_result(
        result,
        thread_id,
    )


def resume_travel_agent(
    thread_id: str,
    approved: bool,
    feedback: str = "",
):
    """Resume the paused LangGraph thread after human review."""
    if not thread_id:
        raise ValueError(
            "thread_id is required to resume a travel plan."
        )

    config = {
        "configurable": {
            "thread_id": thread_id
        }
    }

    result = travel_graph.invoke(
        Command(
            resume={
                "approved": approved,
                "feedback": feedback.strip(),
            }
        ),
        config=config,
    )

    return _serialize_result(
        result,
        thread_id,
    )