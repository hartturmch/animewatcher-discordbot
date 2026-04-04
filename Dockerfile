FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONNOUSERSITE=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

RUN useradd --create-home --shell /usr/sbin/nologin appuser \
    && python -m venv /opt/venv

COPY requirements.txt ./
RUN python -m ensurepip --upgrade \
    && /opt/venv/bin/python -m pip install --no-cache-dir --upgrade pip setuptools wheel \
    && /opt/venv/bin/python -m pip install --no-cache-dir --force-reinstall -r requirements.txt \
    && /opt/venv/bin/python -c "import discord; import discord.app_commands; print(discord.__file__); print(discord.__version__)"

COPY anime_watcher_bot.py anime_watcher_feed.py anime_watcher_notifier.py anilist_client.py ./

RUN mkdir -p /app/data \
    && chown -R appuser:appuser /app /opt/venv

USER appuser

CMD ["/opt/venv/bin/python", "anime_watcher_bot.py"]
