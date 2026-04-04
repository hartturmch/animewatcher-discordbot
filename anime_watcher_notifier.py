#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import smtplib
import ssl
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path

from anime_watcher_feed import RSS_URL, FeedItem, fetch_feed, filter_items, format_timestamp, normalize_title, parse_feed, parse_pubdate


@dataclass
class Config:
    rss_url: str
    watchlist_path: Path
    state_path: Path
    timezone_name: str
    smtp_host: str
    smtp_port: int
    smtp_username: str
    smtp_password: str
    smtp_from: str
    smtp_to: list[str]
    smtp_starttls: bool
    discord_webhook_url: str
    discord_username: str
    bootstrap_mode: str


def load_env_file(env_path: Path) -> None:
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def resolve_path(base_dir: Path, raw_path: str | Path) -> Path:
    path = Path(raw_path)
    return path if path.is_absolute() else base_dir / path


def load_config(base_dir: Path, require_target: bool = True) -> Config:
    load_env_file(base_dir / ".env")

    watchlist_path = resolve_path(base_dir, os.getenv("CR_WATCHLIST_PATH", "watchlist.txt"))
    state_path = resolve_path(base_dir, os.getenv("CR_STATE_PATH", os.path.join("data", "state.json")))
    timezone_name = os.getenv("CR_TIMEZONE", "Europe/Lisbon")
    bootstrap_mode = os.getenv("CR_BOOTSTRAP_MODE", "mark_seen").strip().lower()

    if bootstrap_mode not in {"mark_seen", "notify_all"}:
        raise ValueError("CR_BOOTSTRAP_MODE must be 'mark_seen' or 'notify_all'")

    smtp_host = os.getenv("SMTP_HOST", "").strip()
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_username = os.getenv("SMTP_USERNAME", "").strip()
    smtp_password = os.getenv("SMTP_PASSWORD", "").strip()
    smtp_from = os.getenv("SMTP_FROM", "").strip()
    smtp_to = [item.strip() for item in os.getenv("SMTP_TO", "").split(",") if item.strip()]
    discord_webhook_url = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
    discord_username = os.getenv("DISCORD_USERNAME", "Anime Watcher").strip() or "Anime Watcher"

    has_email = bool(smtp_host and smtp_username and smtp_password and smtp_from and smtp_to)
    has_discord = bool(discord_webhook_url)

    if require_target and not (has_email or has_discord):
        raise ValueError(
            "Configure at least one notification target: SMTP_* for email or DISCORD_WEBHOOK_URL for Discord"
        )

    return Config(
        rss_url=os.getenv("CR_RSS_URL", RSS_URL),
        watchlist_path=watchlist_path,
        state_path=state_path,
        timezone_name=timezone_name,
        smtp_host=smtp_host,
        smtp_port=smtp_port,
        smtp_username=smtp_username,
        smtp_password=smtp_password,
        smtp_from=smtp_from,
        smtp_to=smtp_to,
        smtp_starttls=env_flag("SMTP_STARTTLS", True),
        discord_webhook_url=discord_webhook_url,
        discord_username=discord_username,
        bootstrap_mode=bootstrap_mode,
    )


def load_watchlist(path: Path) -> dict[str, str]:
    if not path.exists():
        raise FileNotFoundError(f"Watchlist file not found: {path}")

    watched: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        watched[normalize_title(line)] = line

    if not watched:
        raise ValueError(f"Watchlist file is empty: {path}")
    return watched


def load_state(path: Path) -> dict:
    if not path.exists():
        return {"seen_guids": []}

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"State file is not valid JSON: {path}") from exc


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(state, indent=2, ensure_ascii=False) + "\n"
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f"{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        handle.write(payload)
        temp_path = Path(handle.name)
    os.replace(temp_path, path)


def build_email(config: Config, items: list[FeedItem]) -> EmailMessage:
    subject = f"[Anime Watcher] {len(items)} novo(s) episodio(s)"
    lines: list[str] = []
    for item in items:
        episode_suffix = f"Episodio {item.episode_number}" if item.episode_number else "Novo episodio"
        title_suffix = f" - {item.episode_title}" if item.episode_title else ""
        lines.append(f"{item.series_title}: {episode_suffix}{title_suffix}")
        lines.append(f"Publicado: {format_timestamp(item.published_at, config.timezone_name)}")
        lines.append(f"Link: {item.link}")
        lines.append("")

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = config.smtp_from
    message["To"] = ", ".join(config.smtp_to)
    message.set_content("\n".join(lines).strip() + "\n")
    return message


def build_discord_payload(config: Config, items: list[FeedItem]) -> dict:
    embeds = []
    for item in items[:10]:
        episode_suffix = f"Episodio {item.episode_number}" if item.episode_number else "Novo episodio"
        title_suffix = f" - {item.episode_title}" if item.episode_title else ""
        embeds.append(
            {
                "title": f"{item.series_title}: {episode_suffix}{title_suffix}",
                "url": item.link,
                "description": f"Publicado: {format_timestamp(item.published_at, config.timezone_name)}",
            }
        )

    content = ""
    if len(items) > 10:
        content = f"Foram encontrados {len(items)} episodios novos. A mensagem mostra os primeiros 10."

    return {"username": config.discord_username, "content": content, "embeds": embeds}


def send_email(config: Config, message: EmailMessage) -> None:
    context = ssl.create_default_context()
    with smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=30) as server:
        if config.smtp_starttls:
            server.starttls(context=context)
        server.login(config.smtp_username, config.smtp_password)
        server.send_message(message)


def send_discord_webhook(config: Config, payload: dict) -> None:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        config.discord_webhook_url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            if response.status not in {200, 204}:
                raise RuntimeError(f"Discord webhook returned HTTP {response.status}")
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Discord webhook returned HTTP {exc.code}: {details}") from exc


def collect_new_items(config: Config, dry_run: bool) -> tuple[list[FeedItem], dict]:
    watchlist = load_watchlist(config.watchlist_path)
    state = load_state(config.state_path)
    seen_guids = set(state.get("seen_guids", []))

    matched_items = filter_items(parse_feed(fetch_feed(config.rss_url)), watchlist)
    matched_items.sort(key=lambda item: parse_pubdate(item.published_at))

    if not seen_guids and config.bootstrap_mode == "mark_seen":
        state["seen_guids"] = sorted({item.guid for item in matched_items})
        state["bootstrapped_at"] = datetime.now(timezone.utc).isoformat()
        return [], state

    new_items = [item for item in matched_items if item.guid not in seen_guids]
    updated_guids = seen_guids | {item.guid for item in matched_items}
    state["seen_guids"] = sorted(updated_guids)
    state["last_checked_at"] = datetime.now(timezone.utc).isoformat()

    if dry_run:
        state["seen_guids"] = sorted(seen_guids)

    return new_items, state


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Notify by email or Discord webhook when selected Crunchyroll RSS series publish new episodes."
    )
    parser.add_argument("--dry-run", action="store_true", help="Do not send notifications and do not persist state.")
    args = parser.parse_args()

    base_dir = Path(__file__).resolve().parent

    try:
        config = load_config(base_dir, require_target=not args.dry_run)
        new_items, state = collect_new_items(config, dry_run=args.dry_run)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if args.dry_run:
        print(f"Matched new items: {len(new_items)}")
        for item in new_items:
            print(f"- {item.series_title} | ep {item.episode_number or '?'} | {item.episode_title or item.title}")
        return 0

    if not new_items:
        save_state(config.state_path, state)
        print("No new matching episodes.")
        return 0

    try:
        delivered = False
        if config.smtp_host and config.smtp_username and config.smtp_password and config.smtp_from and config.smtp_to:
            send_email(config, build_email(config, new_items))
            delivered = True
        if config.discord_webhook_url:
            send_discord_webhook(config, build_discord_payload(config, new_items))
            delivered = True
        if not delivered:
            raise RuntimeError("No notification target configured.")
        save_state(config.state_path, state)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"Sent notification for {len(new_items)} new episode(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
