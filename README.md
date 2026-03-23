# YT Clipper Bot - Установка и запуск

## Что нужно установить:

### 1. FFmpeg (обязательно!)
Скачай с официального сайта:
- https://ffmpeg.org/download.html
- Или для Windows: https://www.gyan.dev/ffmpeg/builds/
- Скачай `ffmpeg-release-full.7z`, распакуй, положи `ffmpeg.exe` в PATH или рядом с ботом

### 2. Бот токен Telegram
1. Найди @BotFather в Telegram
2. `/newbot` → создай бота
3. Скопируй токен и вставь в файл `.env`

### 3. Cookies для YouTube (если хочешь автопостинг)
1. Установи расширение "Get cookies.txt LOCALLY" в Chrome/Edge
2. Зайди на youtube.com (залогинься)
3. Нажми расширение → Export → сохрани как `yt_cookies.txt`
4. Положи файл рядом с ботом

### 4. Cookies для TikTok (если хочешь автопостинг)
1. Установи расширение "Get cookies.txt LOCALLY" в Chrome/Edge
2. Зайди на tiktok.com (залогинься)
3. Нажми расширение → Export → сохрани как `tt_cookies.txt`
4. Положи файл рядом с ботом

## Запуск:
```bash
python yt_clipper_bot.py
```

## Команды бота:
- `/start` - начать настройку
- `/settings` - открыть настройки
- `/status` - статус платформ
- `/reset` - сбросить настройки

## Как работает:
1. Отправляешь ссылку на YouTube видео
2. Бот качает видео и нарезает на клипы по 60 секунд
3. Отправляет клипы в Telegram
4. Если настроен - постит на YouTube и TikTok

## Проблемы и решения:

### ffmpeg не найден
- Скачай с https://ffmpeg.org/download.html
- Распакуй и положи ffmpeg.exe рядом с ботом

### Cookies не работают
- Убедись что не выходил из аккаунта в браузере
- Перезайди и экспортируй cookies заново

### Видео не качается
- Проверь что видео доступно и не приватное
- Попробуй другую ссылку
