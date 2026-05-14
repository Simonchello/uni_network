# Lockdown

Self-hosted платформа для приватного удалённого доступа: многопротокольный шлюз Xray (VLESS+Reality, VLESS+XHTTP), веб-админка с реалтайм-статистикой по WebSocket и Telegram-бот для управления пользователями.

---

## Что внутри

```
.
├── web/                 — FastAPI веб-админка с WebSocket (Python 3.12)
│   ├── main.py          — приложение, lifespan, статика
│   ├── auth.py          — JWT + bcrypt
│   ├── stats_poller.py  — фоновый опрос xray, pub-sub
│   ├── routes_api.py    — REST
│   ├── routes_ws.py     — WebSocket /ws/stats
│   └── static/          — HTML/JS/CSS дашборд
│
├── bot/                 — Telegram-бот для управления ключами
│   ├── bot.py           — long-poll
│   ├── db.py            — aiosqlite (users, keys, pending requests)
│   ├── xray_manager.py  — генерация ключей, рестарт xray
│   └── stats.py         — парсинг `xray api statsquery`
│
├── server/
│   └── xray-server.json — конфиг Xray
│
├── deploy/
│   ├── nginx-lockdown.conf — nginx для нативной установки
│   ├── nginx-docker.conf   — то же, но для docker-compose
│   ├── web-admin.service   — systemd unit
│   ├── setup_vps.sh        — bootstrap (apt, venv, systemd, certbot)
│   └── sync.sh             — rsync деплой на VPS
│
├── docs/
│   ├── ARCHITECTURE.md  — топология, потоки данных
│   └── NETWORK_STACK.md — TLS, HTTP/2, WebSocket, gRPC, JWT
│
├── Dockerfile           — multi-stage build веб-админки
├── docker-compose.yml   — web + nginx
└── .dockerignore
```

---

## Архитектура (коротко)

```
VPN-клиент ─TLS(Reality)─►  Xray  :443/:2096
                              │
                              │ gRPC stats (loopback)
                              ▼
Browser ─HTTPS─► nginx :4443 ─proxy/upgrade─► uvicorn :8001 (FastAPI)
                              │                  │
                              │                  ├─ poll каждые 2s
                              │                  ├─ JWT (bcrypt + HS256)
                              │                  └─ WS broadcast (pub-sub)
                              ▼
                       статистика трафика
                       по пользователям

Telegram ─HTTPS long-poll─► bot (отдельный процесс) ─► xray config + SQLite
```

Подробности — в [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) и [docs/NETWORK_STACK.md](docs/NETWORK_STACK.md).

---

## Запуск через Docker

### 1. Заполнить переменные окружения

```bash
cp web/.env.example .env
```

В `.env` нужно установить:

```bash
JWT_SECRET=<openssl rand -hex 32>
ADMIN_USERNAME=admin
ADMIN_PASSWORD_HASH=<python -c "import bcrypt; print(bcrypt.hashpw(b'YOUR_PASSWORD', bcrypt.gensalt()).decode())">
MOCK_STATS=1     # запустить без Xray на тестовых данных
POLL_INTERVAL_SEC=2.0
```

### 2. Поправить домен в nginx-конфиге

```bash
sed -i 's|<YOUR_DOMAIN>|your.domain.tld|g' deploy/nginx-docker.conf
```

### 3. Сертификаты

Положить TLS-сертификаты в `deploy/certs/`:

```
deploy/certs/
├── fullchain.pem
└── privkey.pem
```

Получить Let's Encrypt-сертификат вне контейнера:

```bash
sudo certbot certonly --standalone -d your.domain.tld
sudo cp /etc/letsencrypt/live/your.domain.tld/{fullchain,privkey}.pem deploy/certs/
```

### 4. Поднять

```bash
docker compose up -d
docker compose ps
docker compose logs -f web
```

Открыть `https://your.domain.tld:4443`.

---

## Запуск без Docker

См. `deploy/setup_vps.sh` — bootstrap для нативной установки на VPS (apt, venv, systemd, certbot, nginx). Запускать на чистом Ubuntu 24.04 от root.

---

## Конфигурация Xray

В `server/xray-server.json` нужно подставить:

1. Пару ключей Reality:
   ```bash
   xray x25519
   ```
   `privateKey` → `xray-server.json`, `publicKey` → `bot/config.py`.

2. `shortId`:
   ```bash
   openssl rand -hex 8
   ```
   Подставить в оба файла.

3. SNI-host (`serverNames` и `dest` в xray-server.json) — публично-доступный TLS-сервер для маскировки fingerprint.

4. UUID клиентов — `xray uuid` или любой UUIDv4 генератор.

---

## Конфигурация Telegram-бота

```bash
cp bot/config.py.example bot/config.py
chmod 600 bot/config.py
```

Заполнить:
- `BOT_TOKEN` — от `@BotFather`
- `ADMIN_IDS` — telegram ID администраторов (узнать через `@userinfobot`)
- Reality keys и server IP

---

## Стек

| Компонент | Технология |
|---|---|
| Backend | FastAPI, uvicorn |
| Auth | bcrypt + PyJWT (HS256) |
| Realtime | WebSocket (RFC 6455) с heartbeat и reconnect |
| Bot | python-telegram-bot 20.x, aiosqlite |
| Proxy | nginx 1.27 (TLS termination, WS upgrade) |
| VPN | Xray-core (VLESS+Reality+Vision, VLESS+XHTTP+Reality) |
| Stats | xray gRPC API через CLI subprocess |
| Deploy | Docker Compose или systemd + rsync |

---

## Безопасность

- Секреты — через `.env` (в `.gitignore`)
- `chmod 600` на `bot/config.py` и `.env`
- bcrypt cost factor 12
- JWT с коротким TTL (24h), no refresh
- HSTS, X-Frame-Options, X-Content-Type-Options
- nginx HTTP/2 + TLS 1.2/1.3
- Xray stats gRPC слушает только `127.0.0.1`
