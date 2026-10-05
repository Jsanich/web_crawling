import requests
from bs4 import BeautifulSoup
url = "https://www.python.org"
response = requests.get(url)
soup = BeautifulSoup(response.text, "html.parser")
menus = soup.select("#top ul.menu li")
for menu in menus:
    print(menu.text.strip())