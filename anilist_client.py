from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo


ANILIST_URL = "https://graphql.anilist.co"

SEARCH_QUERY = """
query ($search: String) {
  Page(page: 1, perPage: 5) {
    media(search: $search, type: ANIME, isAdult: false, sort: SEARCH_MATCH) {
      id
      seasonYear
      season
      format
      title {
        romaji
        english
        native
      }
      synonyms
      siteUrl
      status
      nextAiringEpisode {
        episode
        airingAt
      }
      streamingEpisodes {
        title
        thumbnail
        url
        site
      }
      externalLinks {
        site
        url
      }
    }
  }
}
"""

WATCH_QUERY = """
query ($ids: [Int], $perPage: Int) {
  Page(page: 1, perPage: $perPage) {
    media(id_in: $ids, type: ANIME) {
      id
      seasonYear
      season
      format
      title {
        romaji
        english
        native
      }
      synonyms
      siteUrl
      status
      nextAiringEpisode {
        episode
        airingAt
      }
      streamingEpisodes {
        title
        thumbnail
        url
        site
      }
      externalLinks {
        site
        url
      }
    }
  }
}
"""

ANILIST_BATCH_SIZE = 50


@dataclass
class AniListTitle:
    romaji: str
    english: str
    native: str


@dataclass
class AniListEpisodeLink:
    title: str
    url: str
    site: str


@dataclass
class AniListMedia:
    media_id: int
    title: AniListTitle
    synonyms: list[str]
    site_url: str
    status: str
    season: str
    season_year: int | None
    media_format: str
    next_episode_number: int | None
    next_airing_at: int | None
    streaming_links: list[AniListEpisodeLink]
    external_links: list[AniListEpisodeLink]

    @property
    def display_title(self) -> str:
        return self.title.english or self.title.romaji or self.title.native or f"AniList #{self.media_id}"

    @property
    def season_label(self) -> str:
        parts: list[str] = []
        if self.media_format:
            parts.append(self.media_format.replace("_", " ").title())
        if self.season:
            parts.append(self.season.title())
        if self.season_year:
            parts.append(str(self.season_year))
        return " | ".join(parts) if parts else "Sem temporada informada"


def anilist_request(query: str, variables: dict) -> dict:
    payload = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    request = urllib.request.Request(
        ANILIST_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "anime-watcher/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"AniList returned HTTP {exc.code}: {details}") from exc

    payload = json.loads(body)
    errors = payload.get("errors") or []
    if errors:
        messages = "; ".join(
            error.get("message", "Unknown AniList error")
            for error in errors
            if isinstance(error, dict)
        ) or "Unknown AniList error"
        raise RuntimeError(messages)
    if "data" not in payload:
        raise RuntimeError("AniList response did not include data")
    return payload


def parse_media_list(raw_items: list[dict]) -> list[AniListMedia]:
    media_list: list[AniListMedia] = []
    for item in raw_items:
        next_episode = item.get("nextAiringEpisode") or {}
        media_list.append(
            AniListMedia(
                media_id=item["id"],
                title=AniListTitle(
                    romaji=(item.get("title") or {}).get("romaji") or "",
                    english=(item.get("title") or {}).get("english") or "",
                    native=(item.get("title") or {}).get("native") or "",
                ),
                synonyms=item.get("synonyms") or [],
                site_url=item.get("siteUrl") or "",
                status=item.get("status") or "",
                season=item.get("season") or "",
                season_year=item.get("seasonYear"),
                media_format=item.get("format") or "",
                next_episode_number=next_episode.get("episode"),
                next_airing_at=next_episode.get("airingAt"),
                streaming_links=[
                    AniListEpisodeLink(
                        title=entry.get("title") or "",
                        url=entry.get("url") or "",
                        site=entry.get("site") or "",
                    )
                    for entry in (item.get("streamingEpisodes") or [])
                    if entry.get("url")
                ],
                external_links=[
                    AniListEpisodeLink(
                        title=entry.get("site") or "",
                        url=entry.get("url") or "",
                        site=entry.get("site") or "",
                    )
                    for entry in (item.get("externalLinks") or [])
                    if entry.get("url")
                ],
            )
        )
    return media_list


def search_anime(search_text: str) -> list[AniListMedia]:
    payload = anilist_request(SEARCH_QUERY, {"search": search_text})
    raw_items = ((payload.get("data") or {}).get("Page") or {}).get("media") or []
    media_list = parse_media_list(raw_items)
    query = search_text.casefold().strip()

    def season_number_from_text(media: AniListMedia) -> int:
        haystack = " ".join(
            [
                media.title.english,
                media.title.romaji,
                media.title.native,
                *media.synonyms,
            ]
        )
        match = re.search(r"\bseason\s+(\d+)\b", haystack, flags=re.IGNORECASE)
        return int(match.group(1)) if match else 0

    def format_priority(media: AniListMedia) -> int:
        priorities = {
            "TV": 4,
            "TV_SHORT": 3,
            "ONA": 2,
            "MOVIE": 1,
            "SPECIAL": 0,
        }
        return priorities.get(media.media_format, 0)

    def query_match_priority(media: AniListMedia) -> int:
        titles = [media.title.english, media.title.romaji, media.title.native, *media.synonyms]
        normalized = [title.casefold().strip() for title in titles if title]
        if any(title == query for title in normalized):
            return 3
        if any(query in title for title in normalized):
            return 2
        if any(all(token in title for token in query.split()) for title in normalized):
            return 1
        return 0

    media_list.sort(
        key=lambda media: (
            media.season_year or 0,
            season_number_from_text(media),
            query_match_priority(media),
            format_priority(media),
            media.media_id,
        ),
        reverse=True,
    )
    return media_list


def fetch_media_by_ids(media_ids: list[int]) -> list[AniListMedia]:
    if not media_ids:
        return []
    media_list: list[AniListMedia] = []
    for index in range(0, len(media_ids), ANILIST_BATCH_SIZE):
        batch = media_ids[index : index + ANILIST_BATCH_SIZE]
        payload = anilist_request(WATCH_QUERY, {"ids": batch, "perPage": len(batch)})
        raw_items = ((payload.get("data") or {}).get("Page") or {}).get("media") or []
        media_list.extend(parse_media_list(raw_items))
    return media_list


def format_airing_timestamp(airing_at: int | None, timezone_name: str) -> str:
    if not airing_at:
        return "desconhecido"
    dt = datetime.fromtimestamp(airing_at, tz=timezone.utc).astimezone(ZoneInfo(timezone_name))
    return dt.strftime("%Y-%m-%d %H:%M:%S %Z")
