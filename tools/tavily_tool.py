from tavily import TavilyClient
import os
from dotenv import load_dotenv

load_dotenv()

client = TavilyClient(
    api_key=os.getenv("TAVILY_API_KEY")
)


def tavily_search(query):
    response = client.search(
        query=query,
        max_results=5
    )

    results = []

    i = 1

    for r in response["results"]:
        title = r.get("title", "Unknown")
        url = r.get("url", "")
        snippet = r.get("content", "").strip()

        if len(snippet) > 300:
            snippet = snippet[:300].rsplit(" ", 1)[0] + "..."

        results.append(
            f"{i}. **{title}**\n{url}\n{snippet}"
        )

        i += 1

    return "\n\n".join(results)