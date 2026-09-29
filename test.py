import asyncio
from main_client import get_all_tools

if __name__ == "__main__":
    query = "latest news about india"
    asyncio.run(get_all_tools())