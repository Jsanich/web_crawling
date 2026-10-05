import requests
url = "https://www.python.org"
response = requests.get(url)
print(response.status_code)
print(response.text[:500])