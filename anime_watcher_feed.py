from __future__ import annotations

import re
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable
from zoneinfo import ZoneInfo


RSS_URL = "https://feeds.feedburner.com/crunchyroll/rss/anime"
CRUNCHYROLL_NS = {"cr": "http://www.crunchyroll.com/rss"}


@dataclass
class FeedItem:
    guid: str
    link: str
    title: str
    series_title: str
    episode_title: str
    episode_number: str
    published_at: str


def normalize_title(value: str) -> str:
    value = value.casefold()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def fetch_feed(url: str = RSS_URL) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "anime-watcher/1.0 (+https://feeds.feedburner.com/crunchyroll/rss/anime)"
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", errors="replace")


def text_or_empty(node: ET.Element | None) -> str:
    return (node.text or "").strip() if node is not None else ""


def parse_feed(feed_xml: str) -> list[FeedItem]:
    root = ET.fromstring(feed_xml)
    items: list[FeedItem] = []

    for item in root.findall("./channel/item"):
        guid = text_or_empty(item.find("guid"))
        series_title = text_or_empty(item.find("cr:seriesTitle", CRUNCHYROLL_NS))
        if not guid or not series_title:
            continue

        items.append(
            FeedItem(
                guid=guid,
                link=text_or_empty(item.find("link")),
                title=text_or_empty(item.find("title")),
                series_title=series_title,
                episode_title=text_or_empty(item.find("cr:episodeTitle", CRUNCHYROLL_NS)),
                episode_number=text_or_empty(item.find("cr:episodeNumber", CRUNCHYROLL_NS)),
                published_at=text_or_empty(item.find("pubDate")),
            )
        )

    return items


def parse_pubdate(raw_value: str) -> datetime:
    return datetime.strptime(raw_value, "%a, %d %b %Y %H:%M:%S %Z").replace(tzinfo=timezone.utc)


def filter_items(items: Iterable[FeedItem], watchlist: dict[str, str] | set[str]) -> list[FeedItem]:
    normalized = watchlist if isinstance(watchlist, set) else set(watchlist.keys())
    return [item for item in items if normalize_title(item.series_title) in normalized]


def format_timestamp(raw_value: str, timezone_name: str) -> str:
    try:
        local_dt = parse_pubdate(raw_value).astimezone(ZoneInfo(timezone_name))
        return local_dt.strftime("%Y-%m-%d %H:%M:%S %Z")
    except Exception:
        return raw_value
