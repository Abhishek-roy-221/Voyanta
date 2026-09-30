import os
import requests
from dotenv import load_dotenv

load_dotenv()

api_key = os.getenv("RAILRADAR_API_KEY")

url = "https://api.railradar.in/v1/trains/between/HWH/NDLS"

params = {
    "date": "2026-09-30",
    "byCity": "true"
}

headers = {
    "Authorization": f"Bearer {api_key}"
}

response = requests.get(
    url,
    params=params,
    headers=headers,
)

print("Status:", response.status_code)
print("URL:", response.url)
print(response.text)