"""네이트 종합 관심뉴스 상위 10개 수집 및 핵심 문장 추출 요약.

실행: python 06_nate_ranking.py
옵션: python 06_nate_ranking.py --limit 10 --sentences 2
설치: python -m pip install requests beautifulsoup4
"""

import argparse
import csv
import re
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

RANK_URL = "https://news.nate.com/rank/?mid=n1000"
FIELDS = ["순위", "제목", "언론사", "기사일시", "요약", "요약방식", "기사URL", "수집일시", "수집상태"]


def fetch(session, url):
    response = session.get(url, timeout=(10, 30))
    response.raise_for_status()
    # 네이트는 EUC-KR 페이지를 제공한다. apparent_encoding에 의존하지 않는다.
    encoding = response.encoding
    if not encoding or encoding.lower() == "iso-8859-1":
        encoding = "euc-kr"
    return BeautifulSoup(response.content.decode(encoding), "html.parser")


def clean(text):
    return re.sub(r"\s+", " ", text).strip()


def ranking(soup):
    rows = []
    # 상위 5개와 6~50위는 HTML 구조가 서로 다르다.
    for box in soup.select(".mduSubjectList, .mduRankSubject > li"):
        rank = box.select_one(".mduRank dt em")
        title = box.select_one("a h2")
        if rank is None or title is None:
            continue
        anchor = title.find_parent("a")
        media = box.select_one(".medium")
        publisher = clean(" ".join(media.find_all(string=True, recursive=False))) if media else ""
        preview = box.select_one(".tb")
        preview_text = clean(" ".join(preview.find_all(string=True, recursive=False))) if preview else ""
        rows.append({"rank": int(rank.get_text(strip=True)), "title": clean(title.get_text()),
                     "publisher": publisher, "url": urljoin(RANK_URL, anchor["href"]),
                     "preview": preview_text})
    rows.sort(key=lambda row: row["rank"])
    if not rows or len({row["rank"] for row in rows}) != len(rows):
        raise RuntimeError("랭킹 목록을 정상적으로 읽지 못했습니다. HTML 선택자를 확인하세요.")
    return rows


def article(soup):
    title = soup.select_one("h3.articleSubTit, h1.articleSubTit")
    if title is None:
        meta = soup.find("meta", property="og:title")
        full_title = clean(meta.get("content", "")) if meta else ""
        full_title = re.sub(r"\s*:\s*네이트 (?:뉴스|연예|스포츠)$", "", full_title)
    else:
        full_title = clean(title.get_text())
    date = soup.select_one(".articleInfo .firstDate, .articleInfo .date, .firstDate")
    published = clean(date.get_text()) if date else ""
    body = soup.select_one("#realArtcContents, #articleContetns .content_view")
    if body is None:
        raise ValueError("기사 본문을 찾을 수 없습니다.")
    for node in body.select("script, style, iframe, table, .articleMedia, .imageZoom, [id^=ad_]"):
        node.decompose()
    text = body.get_text("\n", strip=True)
    # 본문 뒤에 붙는 관련 기사, 저작권 문구는 요약 대상에서 제외한다.
    text = re.split(r"\[[^\]\n]*(?:주요\s*뉴스|관련\s*기사)[^\]\n]*\]|▶|☞|ⓒ|저작권자|무단\s*전재", text, maxsplit=1)[0]
    lines = [clean(line) for line in text.splitlines()]
    lines = [line for line in lines if line and not re.search(r"[\w.+-]+@[\w.-]+", line)]
    if not lines:
        raise ValueError("추출된 본문이 비어 있습니다.")
    return full_title, published, lines


def summarize(lines, title, count):
    """단어 빈도, 제목 관련성, 앞부분 가중치로 문장을 골라 원문 순서로 출력."""
    sentences = []
    for line in lines:
        for sentence in re.split(r"(?<=[.!?])\s+", line):
            sentence = clean(sentence)
            if len(sentence) >= 35 and sentence not in sentences:
                sentences.append(sentence)
    if not sentences:
        return clean(" ".join(lines))[:300]
    stop = {"있는", "있다", "했다", "한다고", "대한", "위해", "통해", "이날", "밝혔다", "말했다", "것으로", "그리고"}
    tokens = lambda text: [w for w in re.findall(r"[가-힣]{2,}|[A-Za-z]{2,}", text) if w not in stop]
    frequency = Counter(w for sentence in sentences for w in tokens(sentence))
    title_words = set(tokens(title))
    scores = []
    for index, sentence in enumerate(sentences):
        words = tokens(sentence)
        score = sum(frequency[w] for w in set(words)) / max(len(words), 1) ** 0.5
        score += 3 * len(set(words) & title_words) + 5 / (index + 1)
        scores.append((score, index))
    chosen = []
    for _, index in sorted(scores, reverse=True):
        words = set(tokens(sentences[index]))
        if any(len(words & set(tokens(sentences[other]))) / max(len(words | set(tokens(sentences[other]))), 1) > 0.65
               for other in chosen):
            continue
        chosen.append(index)
        if len(chosen) >= count:
            break
    selected = sorted(chosen)
    return " ".join(sentences[index] for index in selected)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=10, help="수집 개수 (1~50, 기본 10)")
    parser.add_argument("--sentences", type=int, default=2, help="요약 문장 수 (기본 2)")
    parser.add_argument("--delay", type=float, default=0.7, help="기사 요청 사이 대기 초")
    args = parser.parse_args()
    if not 1 <= args.limit <= 50 or args.sentences < 1 or args.delay < 0:
        parser.error("limit은 1~50, sentences는 1 이상, delay는 0 이상이어야 합니다.")
    session = requests.Session()
    session.headers["User-Agent"] = "Mozilla/5.0 (compatible; PersonalNewsSummary/1.0)"
    retry = Retry(total=2, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
    session.mount("https://", HTTPAdapter(max_retries=retry))
    now = datetime.now(timezone(timedelta(hours=9)))
    output = Path(__file__).resolve().parent / f"nate_ranking_{now:%Y%m%d_%H%M%S}.csv"
    rows = ranking(fetch(session, RANK_URL))[:args.limit]
    failures = 0
    # 기사별로 즉시 기록하여 중간에 종료되어도 수집한 결과를 보존한다.
    with output.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=FIELDS)
        writer.writeheader()
        for index, row in enumerate(rows):
            if index:
                time.sleep(args.delay)
            title, published = row["title"], ""
            try:
                full_title, published, lines = article(fetch(session, row["url"]))
                title = full_title or title
                summary = summarize(lines, title, args.sentences)
                method, status = "본문 핵심 문장 추출", "성공"
            except (requests.RequestException, ValueError, UnicodeError) as error:
                failures += 1
                summary = row["preview"]
                method = "목록 미리보기 대체" if summary else "요약 없음"
                status = f"본문 수집 실패: {error}"
            writer.writerow(dict(zip(FIELDS, [row["rank"], title, row["publisher"], published,
                                             summary, method, row["url"], now.isoformat(timespec="seconds"), status])))
            file.flush()
            print(f"[{index + 1}/{len(rows)}] rank={row['rank']} {status}", flush=True)
    session.close()
    print(f"Saved: {output}\nRows: {len(rows)}, article failures: {failures}")


if __name__ == "__main__":
    main()
