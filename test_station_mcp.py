import asyncio

from mcp_client import train_station_search


async def main():
    for city in ["Delhi", "Howrah", "Kolkata"]:
        print(f"\n===== {city} =====")
        result = await train_station_search(city)
        print(result)


asyncio.run(main())