import os
import requests

api_key = os.getenv("RUNPOD_API_KEY")
endpoint_id = os.getenv("RUNPOD_ENDPOINT_ID", "h5mizgivdrya8z")
image_name = "nguyendangtri070304/omnivoice-runpod-worker:v20260426-final-optimized"

query = """
mutation saveEndpoint($input: EndpointInput!) {
  saveEndpoint(input: $input) {
    id
  }
}
"""

variables = {
    "input": {
        "id": endpoint_id,
        "imageName": image_name
    }
}

url = f"https://api.runpod.io/graphql?api_key={api_key}"
response = requests.post(url, json={"query": query, "variables": variables})
print(response.status_code)
print(response.text)
