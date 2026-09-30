import os

import requests
from dotenv import load_dotenv

load_dotenv()

api_key = os.getenv("RAILRADAR_API_KEY")

response = requests.get(
    "https://api.railradar.in/v1/trains/between/HWH/NDLS",
    headers={
        "Authorization": f"Bearer {api_key}",
    },
    timeout=30,
)

print("STATUS:", response.status_code)
print(response.text[:3000])