<div align="center">

# ✦ Voyanta

### Multi-agent AI travel planner built with LangGraph, MCP and human-in-the-loop review

Turn a plain-English travel request into a structured, data-grounded trip plan, then review, revise and approve it before it's finalized.

[**Live Demo**](https://voyanta-o0yq.onrender.com) · [Report a Bug](https://github.com/Abhishek-roy-221/Voyanta/issues) · [Request a Feature](https://github.com/Abhishek-roy-221/Voyanta/issues)

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![LangGraph](https://img.shields.io/badge/LangGraph-1C3C3C?logo=langchain&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-4169E1?logo=postgresql&logoColor=white)
![Groq](https://img.shields.io/badge/Groq-F55036)
![MCP](https://img.shields.io/badge/MCP-Tools-B83B2F)

</div>

> **Heads-up:** the demo is hosted on Render's free tier, so the first request after a period of inactivity can take about 30 seconds while the server wakes up.

---

## Overview

Voyanta takes a request like:

> *"Plan a 5-day trip from Kolkata to Delhi with trains, hotels and sightseeing under ₹30,000."*

and turns it into a complete plan covering transportation, hotels, weather, a budget breakdown and a day-by-day itinerary.

A **supervisor agent** validates the request, extracts the trip details and decides which **specialist agents** to run. Those agents pull real data through **MCP tools**. The draft then pauses for **human review** before the final plan is written.

The core design goal is **trustworthy output**. Agents are instructed to use only the data their tools return. Values like train numbers, timings and prices are never invented, and when a tool fails the plan says so plainly instead of filling the gap.

## Features

- **Supervisor routing**: extracts origin, destination, duration, budget, travelers and preferences, then selects only the agents the request needs (for example, no train agent for an international trip).
- **Input guardrail**: blocks requests unrelated to travel and lets incomplete travel requests through.
- **Six specialist agents**: flights, trains, hotels, weather, budget and itinerary.
- **Live data via MCP**: AviationStack, RailRadar, Tavily and weather tools.
- **Structured outputs**: Pydantic schemas with strict JSON mode keep agent output predictable.
- **Human-in-the-loop**: the graph pauses at an approval step using LangGraph `interrupt()`. Approve it, or send feedback and get a revised plan.
- **Persistent state**: PostgreSQL checkpointing means a paused plan can be resumed by its thread ID.
- **Polished UI**: Markdown-rendered plan, trip-constraint cards, agent badges, one-click copy and PDF export.

## Architecture

```mermaid
flowchart TD
    U([User request]) --> S{Supervisor<br/>guardrail + routing}
    S -- not travel related --> B[Guardrail blocked]
    S --> F[Flight agent]
    S --> T[Train agent]
    F --> T
    T --> H[Hotel agent]
    H --> W[Weather agent]
    W --> BU[Budget agent]
    BU --> I[Itinerary agent]
    I --> HITL{{Human approval<br/>interrupt}}
    HITL -- approve / feedback --> FIN[Final agent]
    FIN --> OUT([Final travel plan])
    B --> END([End])

    F -.-> M1[(AviationStack MCP)]
    T -.-> M2[(RailRadar MCP)]
    H -.-> M3[(Tavily MCP)]
    W -.-> M4[(Weather MCP)]
    PG[(PostgreSQL<br/>checkpoints)] -.-> HITL
```

The supervisor picks which agents run, and they execute in a fixed order. Agents the supervisor skipped are bypassed, and the itinerary agent always runs.

### How the agents work

| Agent | Data source | What it does |
|---|---|---|
| **Supervisor** | LLM (structured output) | Applies the guardrail, extracts trip constraints, and selects agents. |
| **Flight** | AviationStack | Picks origin and destination airports, then builds a table of direct flights from the API response. |
| **Train** | RailRadar | Searches stations, has the LLM propose candidate station codes, then verifies each route against RailRadar before showing any train. |
| **Hotel** | Tavily | Searches the web, extracts real hotels, ratings and prices exactly as listed, and links each to its source. |
| **Weather** | Weather MCP | Fetches current conditions and a forecast for the destination. |
| **Budget** | LLM | Breaks down costs and labels each figure as **Verified** or **Estimate**. |
| **Itinerary** | LLM | Writes a destination-specific day-by-day plan using the data gathered above. |
| **Final** | LLM | Polishes the approved draft, or applies the user's revision feedback. |

### Reducing hallucination

- Tables for trains, flights and hotels are **built in code** from tool output, not written by the LLM.
- LLMs are used for **selection and extraction** (airport codes, station codes, hotel names), then each choice is checked against real tool results.
- Prompts forbid invented flight numbers, schedules and prices, and each agent is told what to do when its data source is unavailable.
- Only the sections for agents that actually ran are included in the final plan.

## Tech Stack

| Layer | Technology |
|---|---|
| Orchestration | LangGraph, LangChain |
| LLM | Groq (`openai/gpt-oss-120b`) |
| Tools | Model Context Protocol (MCP): AviationStack, RailRadar, Tavily, weather |
| Backend | FastAPI, Pydantic |
| State | PostgreSQL with the LangGraph Postgres checkpointer |
| Frontend | HTML, CSS, vanilla JavaScript, marked.js, html2pdf.js |
| Deployment | Render |

## Getting Started

### Prerequisites

- Python 3.11+
- A PostgreSQL database (a local instance or a hosted one such as Render)
- API keys for Groq, AviationStack, Tavily and your RailRadar and weather MCP providers

### Installation

```bash
# 1. Clone the repository
git clone https://github.com/Abhishek-roy-221/Voyanta.git
cd Voyanta

# 2. Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt
```

### Configuration

Create a `.env` file in the project root:

```env
# LLM
GROQ_API_KEY=your_groq_api_key

# LangGraph checkpointing (sslmode=require is added automatically if missing)
DATABASE_URL=postgresql://user:password@host:5432/dbname

# MCP tool providers (use the variable names your mcp_client.py reads)
AVIATIONSTACK_API_KEY=your_aviationstack_key
TAVILY_API_KEY=your_tavily_key
```

Never commit `.env` to Git. Make sure it is listed in `.gitignore`.

### Run locally

```bash
uvicorn main:app --reload
```

Open <http://127.0.0.1:8000> and try one of the example requests.

> Replace `main:app` with the module that creates your FastAPI app if it has a different name.

## Usage

1. Describe your trip in plain English, or pick one of the example chips.
2. Wait while the agents run. The result shows which agents were used and the trip details Voyanta extracted.
3. **Review the draft plan.** Approve it, or write feedback and request a revision.
4. Get the final plan, then copy it or download it as a PDF.

**Example requests**

```text
Plan a 5-day trip from Kolkata to Delhi with trains, hotels and sightseeing.
Plan a 5-day trip from Mumbai to Goa with hotels and sightseeing under ₹30,000.
Plan a 7-day Japan trip from Kolkata including flights, hotels and sightseeing under ₹2 lakh.
```

## Project Structure

```text
Voyanta/
├── backend.py        # LangGraph workflow: guardrail, supervisor, agents, HITL, final plan
├── mcp_client.py     # MCP clients for AviationStack, RailRadar, Tavily and weather
├── index.html        # Frontend page
├── static/
│   ├── style.css     # Styling
│   └── script.js     # API calls, rendering, approval flow, PDF export
├── requirements.txt
└── .env              # Local secrets (not committed)
```

## Known Limitations

- **Flight data:** AviationStack's flights endpoint returns recently operated and scheduled flights, not a future timetable. Times can change from day to day, and fares are not provided. Budget figures for transport are labelled as estimates.
- **International routes:** these often have no direct flights, so flight coverage can be thin.
- **Hotel prices:** prices come from web search snippets, are shown as listed (not converted), and may be out of date. Confirm on the booking site.
- **API plans:** some tool endpoints depend on your provider plan. When a plan blocks an endpoint, the affected section reports that data is unavailable.

## Roadmap

- [ ] Parallel agent execution to cut response time
- [ ] Streaming agent progress to the UI
- [ ] Saved trips and plan history per user
- [ ] Date-aware flight and train availability
- [ ] More transport modes (buses, cabs)

## Contributing

Contributions, issues and feature requests are welcome. Open an issue to discuss what you'd like to change, then submit a pull request.

## Author

**Abhishek Roy**

- GitHub: [@Abhishek-roy-221](https://github.com/Abhishek-roy-221)
- LinkedIn: [abhishek-roy-5baab0281](https://www.linkedin.com/in/abhishek-roy-5baab0281/)

---

<div align="center">

If you found Voyanta useful, consider giving it a ⭐

</div>
