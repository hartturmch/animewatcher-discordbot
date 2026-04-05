#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import tasks

from anilist_client import AniListMedia, fetch_media_by_ids, format_airing_timestamp, search_anime


logger = logging.getLogger("anime_watcher_bot")


@dataclass
class BotConfig:
    token: str
    timezone_name: str
    poll_minutes: float
    sync_guild_id: int | None
    state_path: Path


def load_env_file(env_path: Path) -> None:
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def resolve_path(base_dir: Path, raw_path: str | Path) -> Path:
    path = Path(raw_path)
    return path if path.is_absolute() else base_dir / path


def load_config(base_dir: Path) -> BotConfig:
    load_env_file(base_dir / ".env")

    token = os.getenv("DISCORD_BOT_TOKEN", "").strip()
    if not token:
        raise ValueError("Missing required environment variable: DISCORD_BOT_TOKEN")

    raw_sync_guild_id = os.getenv("DISCORD_COMMAND_GUILD_ID", "").strip()
    sync_guild_id = int(raw_sync_guild_id) if raw_sync_guild_id else None

    poll_minutes = float(os.getenv("DISCORD_POLL_MINUTES", "15"))
    if poll_minutes <= 0:
        raise ValueError("DISCORD_POLL_MINUTES must be greater than 0")

    return BotConfig(
        token=token,
        timezone_name=os.getenv("CR_TIMEZONE", "Europe/Lisbon"),
        poll_minutes=poll_minutes,
        sync_guild_id=sync_guild_id,
        state_path=resolve_path(base_dir, os.getenv("CR_BOT_STATE_PATH", os.path.join("data", "bot_state.json"))),
    )


class SubscriptionStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.data = self._load()

    def _load(self) -> dict:
        if not self.path.exists():
            return {"subscriptions": {}}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Bot state file is not valid JSON: {self.path}") from exc

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self.data, indent=2, ensure_ascii=False) + "\n"
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=self.path.parent,
            prefix=f"{self.path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(payload)
            temp_path = Path(handle.name)
        os.replace(temp_path, self.path)

    def get_subscription(
        self,
        subscription_key: str,
        *,
        channel_id: int | None = None,
        scope: str | None = None,
        owner_user_id: int | None = None,
    ) -> dict:
        key = str(subscription_key)
        subscriptions = self.data["subscriptions"]
        changed = False
        if key not in subscriptions:
            subscriptions[key] = {
                "channel_id": channel_id,
                "scope": scope or "channel",
                "owner_user_id": owner_user_id,
                "media": {},
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            changed = True
        subscription = subscriptions[key]
        if "media" not in subscription:
            subscription["media"] = {}
            changed = True
        if scope and subscription.get("scope") != scope:
            subscription["scope"] = scope
            changed = True
        if channel_id is not None and subscription.get("channel_id") != channel_id:
            subscription["channel_id"] = channel_id
            changed = True
        if owner_user_id is not None and subscription.get("owner_user_id") != owner_user_id:
            subscription["owner_user_id"] = owner_user_id
            changed = True
        if changed:
            self.save()
        return subscription

    def list_media(self, subscription_key: str, *, channel_id: int | None = None, scope: str | None = None, owner_user_id: int | None = None) -> list[dict]:
        subscription = self.get_subscription(
            subscription_key,
            channel_id=channel_id,
            scope=scope,
            owner_user_id=owner_user_id,
        )
        media = list(subscription.get("media", {}).values())
        media.sort(key=lambda item: (item.get("title") or "").casefold())
        return media

    def add_media(
        self,
        subscription_key: str,
        user_id: int,
        media: AniListMedia,
        *,
        channel_id: int | None = None,
        scope: str | None = None,
        owner_user_id: int | None = None,
    ) -> tuple[str, bool, bool]:
        subscription = self.get_subscription(
            subscription_key,
            channel_id=channel_id,
            scope=scope,
            owner_user_id=owner_user_id,
        )
        media_map = subscription["media"]
        key = str(media.media_id)
        created_media = key not in media_map
        if created_media:
            media_map[key] = {
                "media_id": media.media_id,
                "title": media.display_title,
                "subscribers": [],
                "last_notified_episode": None,
                "next_episode_seen": media.next_episode_number,
                "site_url": media.site_url,
            }

        entry = media_map[key]
        subscribers = set(entry.get("subscribers", []))
        added_user = user_id not in subscribers
        subscribers.add(user_id)
        entry["subscribers"] = sorted(subscribers)
        entry["title"] = media.display_title
        entry["site_url"] = media.site_url
        self.save()
        return entry["title"], created_media, added_user

    def remove_media(
        self,
        subscription_key: str,
        user_id: int,
        search_text: str,
        *,
        channel_id: int | None = None,
        scope: str | None = None,
        owner_user_id: int | None = None,
    ) -> tuple[str | None, bool]:
        subscription = self.get_subscription(
            subscription_key,
            channel_id=channel_id,
            scope=scope,
            owner_user_id=owner_user_id,
        )
        search_key = search_text.casefold().strip()
        for key, entry in list(subscription["media"].items()):
            if (entry.get("title") or "").casefold() != search_key:
                continue

            subscribers = set(entry.get("subscribers", []))
            if user_id not in subscribers:
                return entry["title"], False

            subscribers.remove(user_id)
            title = entry["title"]
            if subscribers:
                entry["subscribers"] = sorted(subscribers)
            else:
                del subscription["media"][key]

            self.save()
            return title, True

        return None, False

    def clear_channel(self, subscription_key: str, *, channel_id: int | None = None, scope: str | None = None, owner_user_id: int | None = None) -> int:
        subscription = self.get_subscription(
            subscription_key,
            channel_id=channel_id,
            scope=scope,
            owner_user_id=owner_user_id,
        )
        count = len(subscription.get("media", {}))
        subscription["media"] = {}
        self.save()
        return count

    def iter_subscriptions(self) -> list[dict]:
        return [entry for entry in self.data["subscriptions"].values() if entry.get("media")]

    def update_notified_episode(self, subscription_key: str, media_id: int, episode_number: int | None) -> None:
        subscription = self.get_subscription(subscription_key)
        entry = subscription["media"].get(str(media_id))
        if entry is None:
            return
        entry["last_notified_episode"] = episode_number
        entry["updated_at"] = datetime.now(timezone.utc).isoformat()
        self.save()

    def update_next_episode_seen(self, subscription_key: str, media_id: int, episode_number: int | None) -> None:
        subscription = self.get_subscription(subscription_key)
        entry = subscription["media"].get(str(media_id))
        if entry is None:
            return
        entry["next_episode_seen"] = episode_number
        entry["updated_at"] = datetime.now(timezone.utc).isoformat()
        self.save()


class WatchGroup(app_commands.Group):
    def __init__(self, bot: "AnimeWatcherBot") -> None:
        super().__init__(name="watch", description="Gerenciar animes monitorados")
        self.bot = bot

    def get_subscription_context(
        self, interaction: discord.Interaction
    ) -> tuple[str, int | None, str, int | None, str]:
        channel = interaction.channel
        if interaction.guild is None:
            return f"dm:{interaction.user.id}", channel.id if channel else None, "dm", interaction.user.id, "nesta DM"
        return str(channel.id) if channel else "", channel.id if channel else None, "channel", None, "neste canal"

    @app_commands.command(name="add", description="Adicionar um anime")
    @app_commands.describe(title="Nome do anime para procurar no AniList")
    async def add(self, interaction: discord.Interaction, title: str) -> None:
        channel = interaction.channel
        if channel is None:
            await interaction.response.send_message("Nao consegui identificar onde salvar essa watchlist.", ephemeral=True)
            return
        subscription_key, context_channel_id, scope, owner_user_id, location_label = self.get_subscription_context(interaction)

        await interaction.response.defer(ephemeral=True)
        candidates = await asyncio.to_thread(search_anime, title)
        if not candidates:
            await interaction.followup.send("Nao achei esse anime no AniList.", ephemeral=True)
            return

        if len(candidates) == 1:
            media = candidates[0]
            stored_title, created_media, added_user = self.bot.store.add_media(
                subscription_key,
                interaction.user.id,
                media,
                channel_id=context_channel_id,
                scope=scope,
                owner_user_id=owner_user_id,
            )
            if added_user:
                if created_media:
                    await interaction.followup.send(
                        f"Adicionado: `{stored_title}`. Vou te avisar quando sair episodio novo {location_label}.",
                        ephemeral=True,
                    )
                else:
                    await interaction.followup.send(
                        f"Voce tambem foi inscrito em `{stored_title}` {location_label}.",
                        ephemeral=True,
                    )
                return

            await interaction.followup.send(f"Voce ja estava inscrito nesse anime {location_label}.", ephemeral=True)
            return

        view = AnimeSelectView(self.bot, subscription_key, context_channel_id, scope, owner_user_id, interaction.user.id, candidates)
        lines = []
        for index, media in enumerate(candidates, start=1):
            parts = [
                f"{index}. {media.display_title}",
                media.season_label,
                f"status {media.status or 'desconhecido'}",
            ]
            if media.next_episode_number is not None:
                parts.append(f"prox ep {media.next_episode_number}")
            lines.append(" | ".join(parts))
        await interaction.followup.send(
            "Achei varias opcoes. Escolhe uma abaixo:\n" + "\n".join(lines),
            ephemeral=True,
            view=view,
        )

    @app_commands.command(name="remove", description="Remover sua inscricao de um anime")
    @app_commands.describe(title="Titulo exibido em /watch list")
    async def remove(self, interaction: discord.Interaction, title: str) -> None:
        channel = interaction.channel
        if channel is None:
            await interaction.response.send_message("Nao consegui identificar de onde remover essa inscricao.", ephemeral=True)
            return
        subscription_key, context_channel_id, scope, owner_user_id, location_label = self.get_subscription_context(interaction)

        removed_title, removed_user = self.bot.store.remove_media(
            subscription_key,
            interaction.user.id,
            title,
            channel_id=context_channel_id,
            scope=scope,
            owner_user_id=owner_user_id,
        )
        if removed_title is None:
            await interaction.response.send_message(f"Nao encontrei esse anime na watchlist {location_label}.", ephemeral=True)
            return
        if not removed_user:
            await interaction.response.send_message(f"Voce nao estava inscrito nesse anime {location_label}.", ephemeral=True)
            return

        await interaction.response.send_message(f"Voce deixou de seguir: `{removed_title}`", ephemeral=True)

    @app_commands.command(name="list", description="Listar animes monitorados")
    async def list(self, interaction: discord.Interaction) -> None:
        channel = interaction.channel
        if channel is None:
            await interaction.response.send_message("Nao consegui identificar qual watchlist mostrar.", ephemeral=True)
            return
        subscription_key, context_channel_id, scope, owner_user_id, location_label = self.get_subscription_context(interaction)

        media_entries = self.bot.store.list_media(
            subscription_key,
            channel_id=context_channel_id,
            scope=scope,
            owner_user_id=owner_user_id,
        )
        if not media_entries:
            await interaction.response.send_message(f"Ainda nao ha animes na watchlist {location_label}.", ephemeral=True)
            return

        formatted = "\n".join(
            f"- {entry['title']} ({len(entry.get('subscribers', []))} inscrito(s))"
            for entry in media_entries
        )
        await interaction.response.send_message(f"Watchlist {location_label}:\n{formatted}", ephemeral=True)

    @app_commands.command(name="clear", description="Limpar a watchlist")
    async def clear(self, interaction: discord.Interaction) -> None:
        channel = interaction.channel
        if channel is None:
            await interaction.response.send_message("Nao consegui identificar qual watchlist limpar.", ephemeral=True)
            return
        subscription_key, context_channel_id, scope, owner_user_id, location_label = self.get_subscription_context(interaction)

        count = self.bot.store.clear_channel(
            subscription_key,
            channel_id=context_channel_id,
            scope=scope,
            owner_user_id=owner_user_id,
        )
        await interaction.response.send_message(f"Removidos {count} anime(s) da watchlist {location_label}.", ephemeral=True)

    @app_commands.command(name="check", description="Ver a agenda atual dos animes")
    async def check(self, interaction: discord.Interaction) -> None:
        channel = interaction.channel
        if channel is None:
            await interaction.response.send_message("Nao consegui identificar qual watchlist consultar.", ephemeral=True)
            return
        subscription_key, context_channel_id, scope, owner_user_id, location_label = self.get_subscription_context(interaction)

        await interaction.response.defer(ephemeral=True)
        media_entries = self.bot.store.list_media(
            subscription_key,
            channel_id=context_channel_id,
            scope=scope,
            owner_user_id=owner_user_id,
        )
        if not media_entries:
            await interaction.followup.send(f"Ainda nao ha animes na watchlist {location_label}.", ephemeral=True)
            return

        media_ids = [entry["media_id"] for entry in media_entries]
        results = await asyncio.to_thread(fetch_media_by_ids, media_ids)
        if not results:
            await interaction.followup.send("Nao consegui falar com o AniList agora.", ephemeral=True)
            return

        lines = []
        for media in results[:10]:
            episode = media.next_episode_number or "?"
            when = format_airing_timestamp(media.next_airing_at, self.bot.config.timezone_name)
            lines.append(f"- {media.display_title} | prox. episodio {episode} | {when}")

        await interaction.followup.send(f"Estado atual {location_label}:\n" + "\n".join(lines), ephemeral=True)


class AnimeWatcherBot(discord.Client):
    def __init__(self, config: BotConfig) -> None:
        intents = discord.Intents.default()
        super().__init__(intents=intents)
        self.config = config
        self.tree = app_commands.CommandTree(self)
        self.store = SubscriptionStore(config.state_path)
        self.watch_group = WatchGroup(self)
        self.tree.add_command(self.watch_group)
        self.poll_updates.change_interval(minutes=self.config.poll_minutes)

    async def setup_hook(self) -> None:
        if self.config.sync_guild_id:
            guild = discord.Object(id=self.config.sync_guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            logger.info("Synced slash commands to guild %s", self.config.sync_guild_id)
        else:
            await self.tree.sync()
            logger.info("Synced global slash commands")
        self.poll_updates.start()

    async def on_ready(self) -> None:
        logger.info("Logged in as %s (%s)", self.user, self.user.id if self.user else "unknown")

    async def subscribe_user_to_media(
        self,
        subscription_key: str,
        channel_id: int | None,
        scope: str,
        owner_user_id: int | None,
        user_id: int,
        media: AniListMedia,
    ) -> tuple[str, bool, bool]:
        return self.store.add_media(
            subscription_key,
            user_id,
            media,
            channel_id=channel_id,
            scope=scope,
            owner_user_id=owner_user_id,
        )

    def find_best_watch_link(self, media: AniListMedia, released_episode: int | None) -> tuple[str | None, str | None, str | None]:
        if released_episode is not None:
            for link in media.streaming_links:
                if re.search(rf"\bepisode\s*{released_episode}\b", link.title, flags=re.IGNORECASE):
                    return link.url, link.site, link.title or None

        if media.streaming_links:
            link = media.streaming_links[0]
            return link.url, link.site, link.title or None
        if media.external_links:
            link = media.external_links[0]
            return link.url, link.site, link.title or None
        if media.site_url:
            return media.site_url, "AniList", None
        return None, None, None

    async def send_media_update(
        self,
        channel: discord.abc.Messageable,
        entry: dict,
        media: AniListMedia,
        released_episode: int | None,
        *,
        scope: str,
    ) -> None:
        subscribers = entry.get("subscribers", [])
        mention_text = " ".join(f"<@{user_id}>" for user_id in subscribers)
        episode_number = released_episode or "?"
        watch_url, watch_site, episode_title = self.find_best_watch_link(media, released_episode)
        title_suffix = f" - {episode_title}" if episode_title else ""
        description_lines = [f"Episodio liberado: {episode_number}{title_suffix}"]
        if media.next_episode_number is not None:
            description_lines.append(
                f"Proximo episodio previsto: {media.next_episode_number} em {format_airing_timestamp(media.next_airing_at, self.config.timezone_name)}"
            )

        if watch_url:
            description_lines.append(f"Assistir: {watch_url}")
        elif media.site_url:
            description_lines.append(f"Pagina: {media.site_url}")

        embed = discord.Embed(
            title=f"{media.display_title} - Episodio {episode_number}{title_suffix}",
            description="\n".join(description_lines),
            colour=discord.Colour.orange(),
        )
        if watch_site:
            embed.set_footer(text=f"Fonte de link: {watch_site}")

        allowed_mentions = discord.AllowedMentions(users=True, roles=False, everyone=False)
        if scope == "dm":
            await channel.send(content=f"Saiu episodio novo de {media.display_title}.", embed=embed)
            return

        content = f"{mention_text} saiu episodio novo de {media.display_title}.".strip()
        await channel.send(content=content, embed=embed, allowed_mentions=allowed_mentions)

    async def resolve_delivery_target(self, subscription: dict) -> tuple[discord.abc.Messageable | None, str]:
        scope = subscription.get("scope") or "channel"
        if scope == "dm":
            owner_user_id = subscription.get("owner_user_id")
            if owner_user_id is None:
                return None, scope
            user = self.get_user(owner_user_id)
            if user is None:
                user = await self.fetch_user(owner_user_id)
            return user, scope

        channel_id = subscription.get("channel_id")
        if channel_id is None:
            return None, scope
        channel = self.get_channel(channel_id)
        if channel is None:
            channel = await self.fetch_channel(channel_id)
        return channel, scope

    @tasks.loop(minutes=15)
    async def poll_updates(self) -> None:
        subscriptions = [
            (subscription_key, subscription)
            for subscription_key, subscription in self.store.data["subscriptions"].items()
            if subscription.get("media")
        ]
        if not subscriptions:
            return

        all_media_ids = sorted(
            {
                entry["media_id"]
                for _, subscription in subscriptions
                for entry in subscription.get("media", {}).values()
            }
        )
        if not all_media_ids:
            return

        try:
            results = await asyncio.to_thread(fetch_media_by_ids, all_media_ids)
        except Exception:
            logger.exception("Failed to fetch AniList media data")
            return

        media_by_id = {media.media_id: media for media in results}

        for subscription_key, subscription in subscriptions:
            try:
                channel, scope = await self.resolve_delivery_target(subscription)
            except Exception:
                logger.exception("Failed to resolve delivery target for subscription %s", subscription_key)
                continue
            if channel is None:
                logger.error("Subscription %s has no valid delivery target", subscription_key)
                continue

            for entry in subscription.get("media", {}).values():
                media = media_by_id.get(entry["media_id"])
                if media is None or media.next_episode_number is None:
                    continue

                previous_next_episode = entry.get("next_episode_seen")
                if previous_next_episode is None:
                    self.store.update_next_episode_seen(subscription_key, media.media_id, media.next_episode_number)
                    continue

                if previous_next_episode == media.next_episode_number:
                    continue

                last_notified = entry.get("last_notified_episode")
                if media.next_episode_number < previous_next_episode:
                    logger.warning(
                        "AniList next episode moved backwards for media %s: %s -> %s",
                        media.media_id,
                        previous_next_episode,
                        media.next_episode_number,
                    )
                    self.store.update_next_episode_seen(subscription_key, media.media_id, media.next_episode_number)
                    continue

                first_released_episode = max(previous_next_episode, (last_notified or (previous_next_episode - 1)) + 1)
                released_episodes = list(range(first_released_episode, media.next_episode_number))
                if not released_episodes:
                    self.store.update_next_episode_seen(subscription_key, media.media_id, media.next_episode_number)
                    continue

                try:
                    for released_episode in released_episodes:
                        await self.send_media_update(channel, entry, media, released_episode, scope=scope)
                        self.store.update_notified_episode(subscription_key, media.media_id, released_episode)
                    self.store.update_next_episode_seen(subscription_key, media.media_id, media.next_episode_number)
                    logger.info(
                        "Sent update for media %s episodes %s-%s to subscription %s",
                        media.media_id,
                        released_episodes[0],
                        released_episodes[-1],
                        subscription_key,
                    )
                except Exception:
                    logger.exception("Failed to send update for media %s to subscription %s", media.media_id, subscription_key)

    @poll_updates.before_loop
    async def before_poll_updates(self) -> None:
        await self.wait_until_ready()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    base_dir = Path(__file__).resolve().parent

    try:
        config = load_config(base_dir)
    except Exception as exc:
        print(f"ERROR: {exc}")
        return 1

    bot = AnimeWatcherBot(config)
    bot.run(config.token)
    return 0


class AnimeSelect(discord.ui.Select):
    def __init__(
        self,
        bot: AnimeWatcherBot,
        subscription_key: str,
        channel_id: int | None,
        scope: str,
        owner_user_id: int | None,
        user_id: int,
        candidates: list[AniListMedia],
    ) -> None:
        self.bot = bot
        self.subscription_key = subscription_key
        self.channel_id = channel_id
        self.scope = scope
        self.owner_user_id = owner_user_id
        self.user_id = user_id
        self.candidates = {str(media.media_id): media for media in candidates}
        options = [
            discord.SelectOption(
                label=media.display_title[:100],
                value=str(media.media_id),
                description=(
                    " | ".join(
                        [
                            media.season_label,
                            media.status or "desconhecido",
                            *([f"ep {media.next_episode_number}"] if media.next_episode_number is not None else []),
                        ]
                    )
                )[:100],
            )
            for media in candidates[:25]
        ]
        options.append(
            discord.SelectOption(
                label="Cancelar",
                value="cancel",
                description="Cancelar esta adicao",
            )
        )
        super().__init__(placeholder="Escolha o anime", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("So quem abriu a busca pode escolher uma opcao.", ephemeral=True)
            return

        if self.values[0] == "cancel":
            await interaction.response.edit_message(content="Adicao cancelada.", view=None)
            return

        media = self.candidates[self.values[0]]
        stored_title, created_media, added_user = await self.bot.subscribe_user_to_media(
            self.subscription_key,
            self.channel_id,
            self.scope,
            self.owner_user_id,
            self.user_id,
            media,
        )
        if added_user:
            if created_media:
                details = f"{media.season_label} | {media.status or 'desconhecido'}"
                location_label = "nesta DM" if self.scope == "dm" else "neste canal"
                message = f"Adicionado: `{stored_title}` ({details}). Vou te avisar quando sair episodio novo {location_label}."
            else:
                details = f"{media.season_label} | {media.status or 'desconhecido'}"
                location_label = "nesta DM" if self.scope == "dm" else "neste canal"
                message = f"Voce tambem foi inscrito em `{stored_title}` ({details}) {location_label}."
        else:
            location_label = "nesta DM" if self.scope == "dm" else "neste canal"
            message = f"Voce ja estava inscrito nesse anime {location_label}."

        await interaction.response.edit_message(content=message, view=None)


class AnimeSelectView(discord.ui.View):
    def __init__(
        self,
        bot: AnimeWatcherBot,
        subscription_key: str,
        channel_id: int | None,
        scope: str,
        owner_user_id: int | None,
        user_id: int,
        candidates: list[AniListMedia],
    ) -> None:
        super().__init__(timeout=120)
        self.add_item(AnimeSelect(bot, subscription_key, channel_id, scope, owner_user_id, user_id, candidates))


if __name__ == "__main__":
    raise SystemExit(main())
