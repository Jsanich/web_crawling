import requests
from bs4 import BeautifulSoup
url = "https://www.python.org"
response = requests.get(url)
soup = BeautifulSoup(response.text, "html.parser")
print(soup.title.text)