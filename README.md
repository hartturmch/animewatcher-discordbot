# Anime Watcher

Bot de Discord para acompanhar lancamentos de anime via AniList.

Ele:

- consulta o AniList para ver agenda e proximo episodio;
- deixa voce escolher o anime direto no Discord com slash commands;
- guarda watchlists separadas por canal e por DM;
- manda aviso no canal ou na DM quando sair episodio novo.

Fonte atual:

- [AniList API](https://anilist.gitbook.io/anilist-apiv2-docs/)

## Comandos do bot

- `/watch add <titulo>`: procura o anime no AniList e adiciona na watchlist atual
- `/watch remove <titulo>`: remove um anime da watchlist atual
- `/watch list`: lista os animes salvos na watchlist atual
- `/watch clear`: limpa a watchlist atual
- `/watch check`: mostra a agenda atual dos animes salvos

Cada canal tem a propria watchlist. A DM com o bot tambem tem uma watchlist separada.

## Requisitos

- Ubuntu 22.04+ ou similar
- Python 3.11+
- um bot criado no Discord Developer Portal

## Configuracao

1. Copia o exemplo:

```bash
cp .env.example .env
```

2. Instala dependencias:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

3. Preenche no `.env`:

```env
DISCORD_BOT_TOKEN=...
DISCORD_POLL_MINUTES=15
CR_BOOTSTRAP_MODE=mark_seen
```

`CR_BOOTSTRAP_MODE=mark_seen` evita notificar episodios antigos no primeiro arranque.

4. Opcional: para os slash commands aparecerem mais rapido no setup, define:

```env
DISCORD_COMMAND_GUILD_ID=123456789012345678
```

Se deixares vazio, o sync sera global e pode demorar mais.

## Criar o bot no Discord

1. Vai a [Discord Developer Portal](https://discord.com/developers/applications).
2. Cria uma application.
3. Entra em `Bot`.
4. Clica em `Reset Token` ou `Copy Token`.
5. Guarda esse valor em `DISCORD_BOT_TOKEN`.
6. Em `Privileged Gateway Intents`, nao precisas de ativar intents especiais para este projeto.

## Convidar o bot para o servidor

No `OAuth2 -> URL Generator`:

- `Scopes`: marca `bot` e `applications.commands`
- `Bot Permissions`: marca pelo menos:
- `View Channels`
- `Send Messages`
- `Embed Links`

Depois abre a URL gerada e adiciona o bot ao teu servidor.

## Execucao local

```bash
. .venv/bin/activate
python3 anime_watcher_bot.py
```

## Deploy com Docker Compose

Na VPS:

```bash
sudo mkdir -p /opt/anime-watcher
sudo chown -R "$USER":"$USER" /opt/anime-watcher
cd /opt/anime-watcher
```

Copia os ficheiros do projeto para essa pasta e cria o `.env`.

Depois sobe:

```bash
docker compose up -d --build
```

Para ver os logs:

```bash
docker compose logs -f
```

Para parar:

```bash
docker compose down
```

Para atualizar depois de mudar codigo:

```bash
docker compose up -d --build
```

O estado do bot fica persistido em `./data` no host.

Notas sobre o `docker-compose.yml` atual:

- `network_mode: host`: evita os problemas de DNS que apareceram com o bridge padrao do Docker nesta VPS.
- `user: "0:0"`: mantido porque essa VPS teve problema de leitura de resolucao DNS no container com usuario nao-root.

Se no futuro voce trocar de VPS, vale testar remover essas duas opcoes e voltar para uma configuracao mais padrao.

## Deploy com systemd

Instala numa pasta, por exemplo:

```bash
sudo mkdir -p /opt/anime-watcher
sudo chown -R "$USER":"$USER" /opt/anime-watcher
cp -r . /opt/anime-watcher
cd /opt/anime-watcher
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

Edita o ficheiro `.env` e testa:

```bash
. .venv/bin/activate
python3 anime_watcher_bot.py
```

Copia o servico:

```bash
sudo cp deploy/anime-watcher.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now anime-watcher.service
```

Se usares virtualenv, ajusta `ExecStart` em [deploy/anime-watcher.service](/C:/Users/Harttur/Documents/crunchyrool%20reader/deploy/anime-watcher.service).

Para ver os logs:

```bash
journalctl -u anime-watcher.service -n 100 --no-pager
```

## Estrutura

- `anime_watcher_bot.py`: processo principal do bot
- `anime_watcher_feed.py`: parser e utilitarios do feed legado
- `anime_watcher_notifier.py`: modo legado de execucao unica por email/webhook
- `requirements.txt`: dependencias Python
- `Dockerfile`: imagem do bot
- `docker-compose.yml`: stack Docker Compose
- `.env.example`: configuracao de exemplo
- `deploy/anime-watcher.service`: servico systemd do bot

## Estado guardado

O bot guarda estado em `data/bot_state.json`.

Cada contexto guarda:

- watchlist propria
- ultimo episodio observado por anime
- ultimo episodio ja notificado

Assim, o mesmo anime pode ser acompanhado em canais diferentes e tambem em DM sem misturar historico.
