# Anime Watcher

![Anime Watcher banner](assets/anime-watcher-banner-readme.png)

Bot de Discord para acompanhar lançamentos de anime via AniList e avisar quando sair episódio novo.

[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Discord Bot](https://img.shields.io/badge/Discord-Bot-5865F2?logo=discord&logoColor=white)](https://discord.com/oauth2/authorize?client_id=1490000578871427212)
[![Docker Ready](https://img.shields.io/badge/Docker-ready-2496ED?logo=docker&logoColor=white)](https://www.docker.com/)
[![AniList](https://img.shields.io/badge/Data-AniList-02A9FF)](https://anilist.gitbook.io/anilist-apiv2-docs/)

## Adicione ao seu servidor

[Adicionar o Anime Watcher ao seu servidor](https://discord.com/oauth2/authorize?client_id=1490000578871427212)

Se preferir fazer isso manualmente:

1. Abra o link acima.
2. Escolha o servidor.
3. Autorize o bot com `View Channels`, `Send Messages`, `Embed Links` e `Use Application Commands`.
4. Entre em um canal do servidor e use `/watch add <nome do anime>`.

![Anime Watcher avatar](assets/anime-watcher-avatar.png)

## Usando o bot

Comandos principais:

- `/watch add <título>`: procura o anime e adiciona na watchlist atual
- `/watch remove <título>`: remove um anime da watchlist atual
- `/watch list`: mostra os animes salvos no contexto atual
- `/watch clear`: limpa a watchlist atual
- `/watch check`: mostra a agenda atual dos animes salvos

O bot funciona em dois contextos separados:

- canal do servidor
- DM com o bot

Cada contexto tem a própria watchlist. O que você salvar na DM não mistura com o que salvar em um canal.

## Exemplo rápido

No servidor:

```text
/watch add Jujutsu Kaisen
/watch list
/watch check
```

Quando o AniList indicar que um episódio novo saiu, o bot manda a notificação no mesmo contexto em que o anime foi salvo.

## O que o bot faz

- consulta o AniList para ver agenda e próximo episódio
- ajuda a escolher entre temporadas, filmes e variantes quando o nome for ambíguo
- guarda o estado em disco para não perder a watchlist ao reiniciar
- manda link para assistir quando o AniList fornecer esse link

Fonte atual:

- [AniList API](https://anilist.gitbook.io/anilist-apiv2-docs/)

## Rodar na sua VPS

Se você quiser hospedar sua própria instância do bot:

### Requisitos

- Ubuntu 22.04+ ou similar
- Docker com Docker Compose
- um bot criado no Discord Developer Portal

### Configuração

1. Copie o projeto para a VPS.
2. Crie o arquivo `.env` a partir do exemplo:

```bash
cp .env.example .env
```

3. Preencha pelo menos:

```env
DISCORD_BOT_TOKEN=...
DISCORD_POLL_MINUTES=15
CR_BOOTSTRAP_MODE=mark_seen
```

`CR_BOOTSTRAP_MODE=mark_seen` evita notificar episódios antigos no primeiro arranque.

4. Suba com Docker Compose:

```bash
docker compose up -d --build
```

Para ver os logs:

```bash
docker compose logs -f
```

Para atualizar depois de mudar código:

```bash
docker compose up -d --build
```

Para parar:

```bash
docker compose down
```

## Notas do deploy atual

O `docker-compose.yml` deste projeto usa:

- `network_mode: host`
- `user: "0:0"`

Isso foi mantido porque essa VPS teve problema de DNS com a configuração Docker padrão.

## Estrutura

- `anime_watcher_bot.py`: processo principal do bot
- `anilist_client.py`: integração com AniList
- `anime_watcher_feed.py`: parser do feed legado
- `anime_watcher_notifier.py`: modo legado de execução única por email/webhook
- `docker-compose.yml`: stack Docker Compose
- `Dockerfile`: imagem do bot
- `.env.example`: configuração de exemplo
- `deploy/anime-watcher.service`: serviço systemd

## Estado salvo

O bot salva os dados em `data/bot_state.json`.

Isso inclui:

- watchlists
- inscritos por anime
- último episódio observado
- último episódio notificado

Assim, reiniciar o container ou a VPS não apaga os animes salvos.
