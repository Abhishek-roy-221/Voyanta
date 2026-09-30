import os

import requests
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

load_dotenv()

RAILRADAR_API_KEY = os.getenv("RAILRADAR_API_KEY")

mcp = FastMCP("Voyanta Train Server")

@mcp.tool()
def search_stations(query: str) -> dict:
    if not RAILRADAR_API_KEY:
        raise RuntimeError("RAILRADAR_API_KEY is missing.")

    response = requests.get(
        "https://api.railradar.in/v1/lookup/search/stations",
        params={"q": query},
        headers={
            "Authorization": f"Bearer {RAILRADAR_API_KEY}",
        },
        timeout=30,
    )

    response.raise_for_status()

    return response.json()


@mcp.tool()
def search_trains(
    from_location: str,
    to_location: str,
    date: str = "",
    by_city: bool = True,
) -> dict:
    if not RAILRADAR_API_KEY:
        raise RuntimeError("RAILRADAR_API_KEY is missing.")

    url = (
        "https://api.railradar.in/v1/trains/between/"
        f"{from_location.upper()}/{to_location.upper()}"
    )

    params = {
        "byCity": str(by_city).lower(),
    }

    if date:
        params["date"] = date

   

    response = requests.get(
        url,
        params=params,
        headers={
            "Authorization": f"Bearer {RAILRADAR_API_KEY}",
        },
        timeout=30,
    )

    response.raise_for_status()

    return response.json()


if __name__ == "__main__":
    mcp.run(transport="stdio")