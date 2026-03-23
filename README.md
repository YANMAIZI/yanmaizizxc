# YT Clipper Bot — стабильный автопостинг YouTube + TikTok

## Что изменено концептуально

Старый подход через cookies и headless-браузер нестабилен для режима 24/7: сессии истекают, интерфейсы платформ меняются, публикация ломается без предупреждения.

В проекте теперь заложен **правильный приоритет**:

1. **YouTube — только через официальный YouTube Data API + OAuth refresh token**.
2. **TikTok — через TikTok Content Posting API / Direct Post + refresh token**.
3. `yt_cookies.txt` и `tt_cookies.txt` оставлены только как **legacy fallback**, но не как основной production-режим.

## Что нужно установить

### 1. FFmpeg
- https://ffmpeg.org/download.html
- Или Windows build: https://www.gyan.dev/ffmpeg/builds/

### 2. Telegram Bot Token
Создай бота через @BotFather и добавь в `.env`:

```env
BOT_TOKEN=...
```

### 3. Стабильный YouTube 24/7
Создай OAuth client в Google Cloud, получи refresh token и добавь в `.env`:

```env
YT_CLIENT_ID=...
YT_CLIENT_SECRET=...
YT_REFRESH_TOKEN=...
YT_PRIVACY=public
```

После этого бот будет сам обновлять access token без ручного обновления cookies.

### 4. Стабильный TikTok 24/7
Нужен доступ к TikTok Content Posting API / Direct Post. Добавь в `.env`:

```env
TT_CLIENT_KEY=...
TT_CLIENT_SECRET=...
TT_OPEN_ID=...
TT_REFRESH_TOKEN=...
TT_API_BASE_URL=https://open.tiktokapis.com
TT_DIRECT_POST_ENABLED=1
```

После этого бот будет публиковать через API и хранить обновляемые access token локально в папке `tokens/`.

## Legacy fallback
Если ты всё ещё хочешь временно использовать cookies, положи рядом с ботом:
- `yt_cookies.txt`
- `tt_cookies.txt`

Но это **не рекомендуется** для режима 24/7.

## Запуск

```bash
python yt_clipper_bot.py
```

## Что делает бот

1. Принимает ссылку на YouTube-видео.
2. Скачивает исходник через `yt-dlp`.
3. Нарезает видео на короткие клипы 9:16.
4. Отправляет клипы в Telegram.
5. Автоматически публикует на YouTube и TikTok через API, если платформа настроена.

## Почему это стабильнее

- Нет зависимости от живой браузерной сессии как от основного канала публикации.
- Access token обновляются автоматически через refresh token.
- Настройки токенов хранятся в JSON state-файлах, а не в памяти браузера.
- Если API-вызов падает, ошибка локализуется на уровне платформы, а не ломает весь пайплайн UI-автоматизации.

## Практический совет для «без нареканий 24/7»

Лучший production-вариант:
- запускать бота на VPS;
- хранить `.env` и `tokens/` на постоянном диске;
- использовать process manager (`systemd`, `supervisor`, Docker restart policy);
- не полагаться на cookies как на основной способ авторизации.
