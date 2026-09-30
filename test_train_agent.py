import asyncio

from backend import train_agent

state = {
    "user_query": "Plan a trip from Kolkata to Delhi",
    "trip_constraints": {
        "origin": "Kolkata",
        "destination": "Delhi",
    },
}

result = train_agent(state)

print(result)