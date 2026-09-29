from tools.tavily_tool import tavily_search
from tools.flight_tool import search_flights

# result = tavily_search("Top 5 best hotels in Patna,India")
# print(result)

res = search_flights("plan a 7 days trip from japan to india")
print(res)