import asyncio

from mcp_client import train_mcp_search


async def main():
    result = await train_mcp_search(
    from_location="Howrah",
    to_location="Delhi",
)

    print(result)


asyncio.run(main())