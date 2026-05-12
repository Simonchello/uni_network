# ARCHITECTURE.md — Архитектура проекта Lockdown

> Самохостинг-платформа приватного удалённого доступа с веб-админкой и Telegram-ботом.
> Документ подготовлен к устной защите курсового проекта по предмету «Сетевые технологии».

---

## Оглавление

1. [Обзор проекта](#1-обзор-проекта)
2. [Топология сети на VPS](#2-топология-сети-на-vps)
3. [Компоненты и их роли](#3-компоненты-и-их-роли)
4. [Внешние endpoints (публичные порты)](#4-внешние-endpoints-публичные-порты)
5. [Внутренние endpoints (localhost)](#5-внутренние-endpoints-localhost)
6. [Путь запроса: открытие страницы дашборда](#6-путь-запроса-открытие-страницы-дашборда)
7. [Путь запроса: логин (POST /auth/login)](#7-путь-запроса-логин-post-authlogin)
8. [Путь запроса: WebSocket /ws/stats](#8-путь-запроса-websocket-wsstats)
9. [Путь запроса: фоновый опрос xray](#9-путь-запроса-фоновый-опрос-xray)
10. [Отказоустойчивость](#10-отказоустойчивость)
11. [Чего нет и почему (чтобы не врасплох)](#11-чего-нет-и-почему-чтобы-не-врасплох)

---

## 1. Обзор проекта

### 1.1 Назначение

Проект **Lockdown** — это **самохостинг-платформа для приватного удалённого доступа** к собственному VPS.
Решаемая задача — дать доверенному кругу пользователей (семья, друзья) туннель через принадлежащий мне сервер,
обеспечив при этом обфускацию трафика по паттерну (traffic pattern obfuscation), SNI masking и возможность
проксировать трафик на произвольный TLS-destination (arbitrary TLS destination).

В составе системы три крупных подсистемы:

1. **Прокси-сервер Xray-core** на VPS (`<YOUR_VPS_IP>`, Ubuntu 24.04). Два inbound'а:
   - VLESS + XTLS Vision + Reality на порту 443 (TCP),
   - VLESS + XHTTP + Reality на порту 2096 (HTTP/2 внутри TLS).
   Оба с SNI-masquerading и подменой TLS-fingerprint.

2. **Telegram-бот** `<your_bot>` — человекоориентированный интерфейс «заявка → одобрение → ключ».
   Пишется в файле `bot/bot.py`, использует `python-telegram-bot` + `aiosqlite`.

3. **Веб-админка** на FastAPI + WebSocket — наша часть курсового. Даёт оператору (админу) живой дашборд
   с таблицей пользователей и счётчиками байт `↑ uplink / ↓ downlink`, обновляющимися в реальном времени.
   Код в директории `web/`, статика в `web/static/`.

### 1.2 Какие требования курса выполняет

Преподаватель требует от курсовой по «Сетевым технологиям» следующее:

| Требование курса                                          | Где реализовано в проекте                                   |
|-----------------------------------------------------------|-------------------------------------------------------------|
| Собственный сайт с доменом                                | `https://<YOUR_DOMAIN>:4443`                         |
| Передача данных по сети между компонентами                | FastAPI ↔ браузер (HTTP/2 + WebSocket), FastAPI ↔ Xray (subprocess → gRPC), bot ↔ Telegram API (HTTPS long-poll), bot ↔ Xray (subprocess + gRPC) |
| Live-модификация содержимого                              | WebSocket `/ws/stats`: каждые 2 секунды рассылает snapshot всем подписчикам, JS перерисовывает таблицу пользователей |
| Работа с протоколами                                      | TCP, TLS 1.2/1.3, HTTP/1.1, HTTP/2, WebSocket (RFC 6455), gRPC поверх HTTP/2, SQLite (файловый), Telegram Bot API |
| Безопасность                                              | bcrypt-хеш пароля, JWT HS256, HSTS, X-Frame-Options, X-Content-Type-Options, сервисы биндятся на `127.0.0.1`, nginx terminates TLS |

### 1.3 Общая идея сетевой архитектуры

```
                                 ИНТЕРНЕТ
                                    │
              ┌─────────────────────┼─────────────────────────┐
              ▼                     ▼                         ▼
       VPN-клиент           Браузер админа             Telegram-пользователи
       (Hiddify, v2rayNG)   (Chrome/Firefox)           (телефон)
              │                     │                         │
       :443 / :2096              :4443                api.telegram.org
         (Reality)              (nginx TLS)            (long-poll)
              │                     │                         │
              ▼                     ▼                         ▼
                        VPS <YOUR_VPS_IP>
       ┌─────────────────────────────────────────────────────────┐
       │  systemd                                                │
       │    • xray.service           (прокси, Xray-core)         │
       │    • web-admin.service      (FastAPI + uvicorn)         │
       │    • nginx.service          (reverse-proxy + TLS)       │
       │    • xray-bot.service       (Telegram-бот)              │
       │    • mtg.service            (MTProto-прокси, чужой)     │
       └─────────────────────────────────────────────────────────┘
```

Вся документация дальше — про внутренности этой чёрной коробки **«VPS»**.

---

## 2. Топология сети на VPS

### 2.1 Полная карта портов, процессов и интерфейсов

```
╔═══════════════════════════════════════════════════════════════════════════════╗
║                       VPS  <YOUR_VPS_IP>  (Ubuntu 24.04)                     ║
║                                                                               ║
║  eth0 (public)         ┌─────────────────────────────────────────────┐        ║
║  <YOUR_VPS_IP>        │               NETFILTER / UFW                │       ║
║                        │   allow: 22, 80, 443, 2096, 4443, 8443       │       ║
║                        └───────────────────┬─────────────────────────┘        ║
║                                            │                                  ║
║  ┌─────────────────────────────────────────┼────────────────────────────────┐ ║
║  │                                         ▼                                │ ║
║  │   :22/tcp          :80/tcp          :443/tcp         :2096/tcp           │ ║
║  │     │                │                 │                │                │ ║
║  │  openssh          nginx              xray             xray               │ ║
║  │  (sshd)        [redirect→4443]    VLESS+Vision     VLESS+XHTTP           │ ║
║  │                                   +Reality         +Reality              │ ║
║  │                      │                │                │                 │ ║
║  │                      │                │                │                 │ ║
║  │   :4443/tcp       :8443/tcp          dest:           dest:               │ ║
║  │     │                │             <MASKED_SNI_HOST>:443      <MASKED_SNI_HOST>:443            │ ║
║  │  nginx             mtg            (pass-through     (pass-through        │ ║
║  │  (TLS 1.2/1.3      (чужой          для зондов)       для зондов)         │ ║
║  │   термин.)          MTProto)                                             │ ║
║  │     │                                                                    │ ║
║  │     │ proxy_pass                                                         │ ║
║  │     ▼                                                                    │ ║
║  │   lo (loopback)                                                          │ ║
║  │   127.0.0.1                                                              │ ║
║  │                                                                          │ ║
║  │   :8001/tcp        :10085/tcp         bot.db (файл)                      │ ║
║  │     │                │                     │                             │ ║
║  │  uvicorn           xray                 xray-bot                         │ ║
║  │  FastAPI           gRPC                 (python-telegram-bot             │ ║
║  │  (web-admin)       StatsService          HTTPS long-poll                 │ ║
║  │                    (dokodemo-door)       к api.telegram.org)             │ ║
║  │     ▲                ▲                      ▲                            │ ║
║  │     │                │                      │                            │ ║
║  │     │                └── subprocess ────────┤                            │ ║
║  │     │                    xray api statsquery│                            │ ║
║  │     │                                       │                            │ ║
║  │     └─── subprocess (asyncio.to_thread) ────┘                            │ ║
║  │                                                                          │ ║
║  │   Файлы:                                                                 │ ║
║  │     /usr/local/etc/xray/config.json  ← конфиг Xray (bot редактирует)     │ ║
║  │     /opt/lockdown-web/               ← код web-admin + .venv + .env      │ ║
║  │     /opt/xray-bot/                   ← код бота + bot.db                 │ ║
║  │     /etc/letsencrypt/live/...        ← LE-сертификаты (может отсутств.)  │ ║
║  │     /etc/nginx/sites-enabled/lockdown← nginx-конфиг                      │ ║
║  │                                                                          │ ║
║  └──────────────────────────────────────────────────────────────────────────┘ ║
╚═══════════════════════════════════════════════════════════════════════════════╝
```

### 2.2 Ключевые наблюдения по топологии

**Порт 443 занят Xray** — поэтому веб-админка слушает на нестандартном 4443. Это нетривиальное решение
и требует объяснения на защите:

- На обычном VPS без Xray был бы 443 (nginx TLS).
- У нас 443 == Xray+Reality+VLESS, и отдавать его nginx'у нельзя, т.к. Xray нужен именно этот порт
  для честного SNI-masquerading (DPI ожидает HTTPS на 443).
- 2096 — второй inbound Xray (XHTTP). Это тоже нестандартный порт HTTPS, который уже зарегистрирован
  как IANA «Internet Relay Chat Over TLS» — но фактически используется как «второй HTTPS» многими сервисами.
- 8443 — MTProto-прокси `mtg` для Telegram, это параллельно живущий сервис, **не часть нашего проекта**.
- 4443 — свободный порт из диапазона «альтернативного HTTPS», закрепился как «second HTTPS» в community.
  Сюда и повесили nginx для админки.

**TLS termination двумя разными демонами:**
- Xray делает TLS termination для Reality-туннеля (SNI = `<MASKED_SNI_HOST>`, fingerprint = Chrome).
  Используется **Xray-встроенная TLS-реализация**, не OpenSSL.
- nginx делает TLS termination для админки (SNI = `<YOUR_DOMAIN>`, стандартный сертификат LE
  или self-signed). Использует OpenSSL.

Эти два TLS никак не пересекаются — слушают разные порты, разные сертификаты, разные политики.

**Self-signed или Let's Encrypt:**
В проде на `<YOUR_DOMAIN>:4443` **предполагался LE-сертификат**, но Let's Encrypt rate-limit'ит
общий домен `fvds.ru` (его используют тысячи клиентов FirstVDS), поэтому фактически пришлось
перейти на self-signed. В браузере это выглядит как предупреждение, но TLS работает — TLS не требует
валидного CA для работы, только ClientHello/ServerHello/keyexchange/ApplicationData.

### 2.3 Сетевые интерфейсы

| Интерфейс   | Адрес            | Назначение                                         |
|-------------|------------------|----------------------------------------------------|
| `eth0`      | <YOUR_VPS_IP>/24| Публичный интерфейс VPS, видит интернет            |
| `lo`        | 127.0.0.1/8      | Loopback, все внутренние сервисы биндятся сюда     |
| (нет TUN)   | —                | На **сервере** TUN не нужен, только на клиентах    |

### 2.4 Сетевые пути снаружи внутрь

```
  Internet  ───▶  eth0:80  ───▶  nginx  ──301 Moved──▶  eth0:4443 (HTTPS)
  Internet  ───▶  eth0:4443 ─▶  nginx  ──proxy_pass──▶  lo:8001 (uvicorn)
  Internet  ───▶  eth0:443  ─▶  xray   ──────────────▶  destination TLS
  Internet  ───▶  eth0:2096 ─▶  xray   ──────────────▶  destination TLS
  Internet  ───▶  eth0:22   ─▶  sshd   ──login────────▶ bash
  Internet  ───▶  eth0:8443 ─▶  mtg    (чужой сервис, не трогаем)

  localhost ───▶  lo:10085  ─▶  xray   (gRPC StatsService)
  localhost ───▶  lo:8001   ─▶  uvicorn (FastAPI, только через nginx)
```

---

## 3. Компоненты и их роли

### 3.1 Сводная таблица

| Компонент             | Язык / рантайм  | Что делает                                                                        | Куда слушает                 |
|-----------------------|-----------------|-----------------------------------------------------------------------------------|-------------------------------|
| **xray-core**         | Go              | Прокси-сервер VLESS+Reality, terminating TLS, маршрутизация трафика клиентов      | `0.0.0.0:443`, `0.0.0.0:2096`, `127.0.0.1:10085` |
| **nginx**             | C               | Reverse-proxy для веб-админки, TLS termination, HTTP→HTTPS redirect, HSTS-заголовки | `0.0.0.0:80`, `0.0.0.0:4443` |
| **uvicorn + FastAPI** | Python 3.12     | HTTP-сервер для админки: REST (`/auth/login`, `/api/stats`, `/api/users`), WebSocket (`/ws/stats`), статика (`/static/*`) | `127.0.0.1:8001` |
| **Telegram-бот**      | Python 3.12     | Обрабатывает команды юзеров в Telegram, выдаёт VPN-ключи, управляет `config.json` Xray | **не слушает** (исходящий long-poll) |
| **mtg**               | Go              | MTProto-прокси (чужой сервис, не наш)                                             | `0.0.0.0:8443`               |
| **sshd**              | C               | Административный SSH-доступ                                                       | `0.0.0.0:22`                 |
| **SQLite (aiosqlite)**| —               | Файловая БД для бота (`/opt/xray-bot/bot.db`)                                    | файл, не сокет                |

### 3.2 Кто с кем общается

```
     браузер (админ)            Telegram-клиенты (юзеры бота)         VPN-клиенты
           │                              │                                │
           │ HTTPS/WSS                    │ HTTPS                          │ TLS/Reality
           │ (TLS 1.2/1.3)                │                                │ SNI=<MASKED_SNI_HOST>
           ▼                              ▼                                ▼
        nginx                    api.telegram.org                      xray
           │                              ▲                              │  │
           │ HTTP/1.1                     │                              │  │  subprocess
           │ proxy_pass                   │ HTTPS long-poll              │  │  xray api
           │                              │                              │  │  statsquery
           ▼                              │                              │  ▼
       uvicorn ←──subprocess──────── xray-bot                          gRPC
       FastAPI    xray api            (python-                         StatsService
           │      statsquery          telegram-bot)                     (127.0.0.1:10085)
           │      (asyncio                │                                │
           │      .to_thread)             │ aiosqlite                      │
           ▼                              ▼                                │
        gRPC                         bot.db                                │
     StatsService      ┌──────────── (файл)                                │
      (127.0.0.1:10085)│                                                   │
           ▲           │         ┌─────────────────────────────────────────┘
           │           │         │
           │           │         │ fcntl.flock + json.dump
           │           ▼         ▼
           │     /usr/local/etc/xray/config.json
           │     (общий файл, редактирует БОТ; читает XRAY)
           │
           └───────── xray ─────────────
                     (пишет счётчики
                      после каждого
                      пакета)
```

### 3.3 Почему именно FastAPI + uvicorn + nginx

Альтернативы, которые были отклонены:

| Альтернатива               | Почему отклонено                                                 |
|----------------------------|------------------------------------------------------------------|
| Django + WSGI              | WSGI плохо дружит с WebSocket. Нужен Django Channels + Daphne — слишком большой стек ради двух страниц |
| Go + net/http              | Нам и так хватает Python-стека бота, экосистема одна — переиспользуем |
| Flask + Flask-SocketIO     | Flask — WSGI; Flask-SocketIO эмулирует через long-poll, это не полноценный WebSocket RFC 6455 |
| Прямо uvicorn без nginx    | Тогда надо uvicorn TLS termination внутри Python — медленнее, и некуда приклеить HSTS/HTTP2 на лету |
| caddy вместо nginx         | caddy управляет сертификатами автоматически, но на этом VPS LE уже исчерпан, нам всё равно self-signed |

Итого: FastAPI + uvicorn + nginx — это стандартная проверенная связка для веб-приложений с реал-тайм
по WebSocket, хорошо документированная и воспроизводимая на любом Linux-сервере.

---

## 4. Внешние endpoints (публичные порты)

### 4.1 Что видно из интернета

```
nmap <YOUR_VPS_IP>
───────────────────────────────────────────────
PORT     STATE SERVICE       
22/tcp   open  ssh           
80/tcp   open  http          
443/tcp  open  https         ← Xray, маскируется под TLS <MASKED_SNI_HOST>
2096/tcp open  (unknown TLS) ← Xray XHTTP, маскируется
4443/tcp open  https-alt     ← nginx для админки
8443/tcp open  https-alt     ← mtg, не наше
```

### 4.2 Детальное описание каждого публичного endpoint'а

#### `:80/tcp` — HTTP → HTTPS redirect

**Слушает:** nginx (`/etc/nginx/sites-enabled/lockdown`, блок `server { listen 80; ... }`)

**Что делает:**
1. Отдаёт challenge для certbot (`location /.well-known/acme-challenge/`).
2. На всё остальное возвращает 301 Redirect на `https://<YOUR_DOMAIN>:4443`.

**Зачем нужен:**
- Certbot для выпуска LE-сертификатов по HTTP-01 challenge требует именно 80 порт (standalone-режим в `setup_vps.sh` так и работает).
- Если юзер вводит в браузер просто `<YOUR_DOMAIN>`, браузер идёт на 80, и мы его редиректим
  на безопасный порт.

**Цитата из `deploy/nginx-lockdown.conf`, строки 1–13:**
```nginx
server {
    listen 80;
    listen [::]:80;
    server_name <YOUR_DOMAIN>;

    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }

    location / {
        return 301 https://$host:4443$request_uri;
    }
}
```

#### `:443/tcp` — Xray VLESS + Vision + Reality (основной VPN-inbound)

**Слушает:** xray-core (`server/xray-server.json`, inbound tag `vless-reality-vision`)

**Что делает (упрощённо):**
1. Принимает TCP-соединение, ждёт TLS ClientHello.
2. Если в ClientHello есть валидный `shortId`+`publicKey` (зашифрованы внутри Reality-механизма) — это
   мой клиент, начинается проксирование.
3. Иначе TLS-handshake пересылается на `<MASKED_SNI_HOST>:443` pass-through (клиент получает реальный сертификат
   <MASKED_SNI_HOST>, реальный контент). Для DPI такой «зонд» выглядит как настоящий поход на <MASKED_SNI_HOST>.

**Сетевые последствия:**
- Провайдер/DPI видит TLS-подключение к `<YOUR_VPS_IP>:443`.
- SNI в TLS ClientHello — `<MASKED_SNI_HOST>`.
- TLS-fingerprint — `chrome` (Client's cipher suites, extensions, curves — как у Chrome).
- Сертификат сервера (для не-клиента) — настоящий с `<MASKED_SNI_HOST>`, пересылается as-is.

**Связка протоколов (снаружи внутрь):**
```
TCP → TLS 1.3 (Reality) → VLESS (внутри Application Data) → XTLS Vision
```

**XTLS Vision** — оптимизация: если клиент за туннелем уже говорит TLS (например, внутри tunnel'а
HTTPS к YouTube), Xray не оборачивает этот TLS в ещё один слой шифрования, а пропускает байты
в стиле pass-through. Экономит CPU (на VPS с 1.8 GB RAM и одним vCPU это важно).

#### `:2096/tcp` — Xray VLESS + XHTTP + Reality (мобильный inbound)

**Слушает:** xray-core (inbound tag `vless-reality-xhttp`)

**Что делает:**
1. TLS-handshake, Reality-проверка — как на 443.
2. Внутри TLS — HTTP/2-поток. Клиент открывает один TCP-коннект, внутри него — множество
   обычных HTTP-запросов вида `POST /xhttp?session=abc` и `GET /xhttp?session=abc`.
3. Каждый запрос несёт кусок данных туннеля (upload/download stream).

**Зачем:** на мобильных сетях часто рвётся TCP, а внутри XHTTP каждый HTTP-request независимо
переотправляется без разрыва всего туннеля. Плюс для DPI паттерн «HTTPS + много коротких HTTP-запросов
к одному сайту» — неотличим от обычного веб-серфинга.

**Цитата из `server/xray-server.json`, строки 178–193 (inbound xhttp):**
```json
"streamSettings": {
    "network": "xhttp",
    "xhttpSettings": {
        "path": "/xhttp"
    },
    "security": "reality",
    "realitySettings": {
        "dest": "<MASKED_SNI_HOST>:443",
        "serverNames": ["<MASKED_SNI_HOST>"],
        ...
    }
}
```

#### `:4443/tcp` — nginx HTTPS для веб-админки

**Слушает:** nginx (`/etc/nginx/sites-enabled/lockdown`, блок `server { listen 4443 ssl http2; ... }`)

**Что делает:**
1. TLS termination (ssl_certificate / ssl_certificate_key).
2. HTTP/2 через ALPN (потому что listen-директива содержит `http2`).
3. proxy_pass на `http://127.0.0.1:8001` (uvicorn).
4. Для `/ws/*` — специальные заголовки `Upgrade` и `Connection: upgrade` для WebSocket-handshake.
5. Добавляет безопасные response-заголовки: HSTS (1 год), X-Frame-Options, X-Content-Type-Options, Referrer-Policy.

**Цитата из `deploy/nginx-lockdown.conf`, строки 15–54** — это весь блок `server { listen 4443 ... }`,
там же настройка TLS-протоколов (`ssl_protocols TLSv1.2 TLSv1.3;`), запрет аномальных cipher suites
(`ssl_ciphers HIGH:!aNULL:!MD5;`) и proxy_read_timeout 3600 секунд для WS.

#### `:8443/tcp` — mtg (MTProto-прокси Telegram)

**Это не наш сервис.** На VPS параллельно крутится `mtg` — прокси для Telegram-клиентов
(не путать с Telegram-ботом). Упоминается здесь ради полноты карты портов — чтобы на защите
не было вопросов «а что это за 8443 в nmap».

#### `:22/tcp` — SSH

Стандартный OpenSSH, управление сервером. Не входит в курсовую как предмет изучения, но важен
для эксплуатации.

### 4.3 Таблица «что видит снаружи человек, не знающий ничего»

| Порт  | Запрос            | Ответ                                      | Выглядит как               |
|-------|-------------------|--------------------------------------------|----------------------------|
| 22    | SSH banner        | `SSH-2.0-OpenSSH_9.6p1 Ubuntu-3ubuntu13.5` | типичный Ubuntu-сервер     |
| 80    | `curl -I`         | `301 Moved Permanently` → `:4443`          | простой редирект           |
| 443   | TLS ClientHello   | Cert `*.<MASKED_SNI_HOST>`, ALPN `h2`, TLS 1.3         | **настоящий <MASKED_SNI_HOST>**       |
| 2096  | TLS ClientHello   | Cert `*.<MASKED_SNI_HOST>`, ALPN `h2`, TLS 1.3         | второй TLS-сервер <MASKED_SNI_HOST>   |
| 4443  | `curl -k -I`      | `200 OK`, `text/html`, `content-length`    | обычный HTTPS-сайт         |
| 8443  | TLS ClientHello   | (зависит от настройки mtg)                  | (не наш)                   |

Именно поэтому 443 и 2096 нельзя отличить от реального <MASKED_SNI_HOST> — Reality даёт pass-through на настоящий
сервер для всех, кто не предъявил валидный ключ.

---

## 5. Внутренние endpoints (localhost)

### 5.1 Что слушает только на loopback

| Endpoint             | Кто слушает        | Кто ходит                        | Протокол              |
|----------------------|--------------------|----------------------------------|-----------------------|
| `127.0.0.1:8001`     | uvicorn (FastAPI)  | nginx                            | HTTP/1.1              |
| `127.0.0.1:10085`    | xray (api-in)      | xray-bot, web-admin              | gRPC (над HTTP/2)     |
| `/opt/xray-bot/bot.db` | aiosqlite        | bot (только свой процесс)        | (файл)                |
| `/usr/local/etc/xray/config.json` | — (файл) | bot (читает/пишет), xray (читает при старте/рестарте) | JSON |

### 5.2 Почему именно localhost, а не публично

**uvicorn на 127.0.0.1:8001:**
- Любой внешний клиент обязан идти через nginx. Это даёт нам TLS termination, HTTP/2, rate-limit
  (если понадобится), логирование access-логов nginx'а.
- Если бы uvicorn слушал `0.0.0.0`, атакующий мог бы запросить `http://<YOUR_VPS_IP>:8001/api/stats`
  и увидеть хотя бы 401 (а значит — подтвердить наличие FastAPI, снять fingerprint).
- `EnvironmentFile` в `deploy/web-admin.service` форсит `--host 127.0.0.1` через `ExecStart`.

**Цитата из `deploy/web-admin.service`, строка 11:**
```
ExecStart=/opt/lockdown-web/.venv/bin/uvicorn web.main:app --host 127.0.0.1 --port 8001
```

**xray gRPC на 127.0.0.1:10085:**
- Эта api служит для чтения статистики `xray api statsquery`. Она **нешифрованная** и **без
  аутентификации** — кто откроет, тот и видит счётчики. Если бы слушала на `0.0.0.0`, атакующий
  мог бы из интернета узнать все email'ы пользователей и их трафик.
- В `server/xray-server.json` явно `"listen": "127.0.0.1"` на inbound с tag `api-in`.

**Цитата из `server/xray-server.json`, строки 37–44:**
```json
{
  "tag": "api-in",
  "listen": "127.0.0.1",
  "port": 10085,
  "protocol": "dokodemo-door",
  "settings": {
    "address": "127.0.0.1"
  }
}
```

Здесь `dokodemo-door` — специальный протокол Xray «слушай на этом порту, принимай соединения, и
перенаправляй в outbound», плюс в routing описан routing rule: `"inboundTag": ["api-in"]` →
`"outboundTag": "api"` — это направляет запрос в модуль `StatsService`.

**bot.db:**
- SQLite не умеет в сетевой протокол, это файловая база.
- Читается и пишется через `aiosqlite` — асинхронная обёртка над sqlite3.
- Права на файл — стандартные юниксовые (`chmod 644`). SQLite сам обрабатывает параллельные
  connection'ы через file-level lock.

**config.json (конфиг Xray):**
- Это единственный файл, в который пишет бот и из которого читает Xray.
- **Гонки по записи решаются через fcntl.flock** в `bot/xray_manager.py` — перед записью бот берёт
  эксклюзивный лок на файл. Цитата из `xray_manager.py`, строки 12–20:
  ```python
  @contextmanager
  def _locked_config():
      """Exclusive lock on config file for concurrent safety."""
      with open(XRAY_CONFIG_PATH, "r+") as f:
          fcntl.flock(f.fileno(), fcntl.LOCK_EX)
          try:
              yield f
          finally:
              fcntl.flock(f.fileno(), fcntl.LOCK_UN)
  ```
- **Применение изменений в Xray** — только через `systemctl restart xray` (subprocess в `restart_xray()`,
  строка 86–92). Это жёсткое решение: Xray не поддерживает hot-reload без external control API
  (HandlerService), мы его не используем.

### 5.3 Карта зависимостей

```
     web-admin.service          xray-bot.service
           │                          │
           │ subprocess               │ subprocess
           │ asyncio.to_thread        │ (blocking)
           ▼                          ▼
       xray CLI                   xray CLI       fcntl.flock
    (api statsquery)           (api statsquery)   │
           │                          │           ▼
           │                          │      config.json
           ▼                          ▼           │
     127.0.0.1:10085   ◄──────────────┘           │
     gRPC StatsService                            │
                                                  │
     xray.service ────── читает при старте ───────┘
                         (и при restart)
                         перечитывает весь config
```

---

## 6. Путь запроса: открытие страницы дашборда

Это сценарий: админ в браузере вводит `https://<YOUR_DOMAIN>:4443/`, жмёт Enter, видит страницу.

Рассмотрим **каждый сетевой прыжок** с привязкой к кодовой базе.

### 6.1 DNS

Браузер делает DNS-запрос на `<YOUR_DOMAIN>`. Это поддомен fvds.ru, обслуживаемый хостером.
A-запись указывает на `<YOUR_VPS_IP>`. Это стандартный DNS через UDP/53 к настроенному в ОС резолверу
(обычно `1.1.1.1` или `8.8.8.8` или локальный NetworkManager).

### 6.2 TCP handshake

```
  Браузер                                           nginx (<YOUR_VPS_IP>:4443)
    │                                                    │
    │── TCP SYN  ───────────────────────────────────────►│
    │                                                    │
    │◄── TCP SYN-ACK ────────────────────────────────────│
    │                                                    │
    │── TCP ACK  ───────────────────────────────────────►│
    │                                                    │
    ▼                                                    ▼
  Соединение установлено
```

Браузер коннектится на порт 4443 — это важно, потому что по умолчанию HTTPS — 443, а у нас сдвинут.
В URL явно указан порт `:4443`.

### 6.3 TLS 1.3 handshake (1-RTT)

```
  Браузер                                           nginx
    │                                                    │
    │── TLS ClientHello ────────────────────────────────►│
    │    SNI: <YOUR_DOMAIN>                       │
    │    cipher_suites: [TLS_AES_256_GCM_SHA384, ...]    │
    │    ALPN: [h2, http/1.1]                            │
    │    key_share: x25519                               │
    │                                                    │
    │                                          [nginx выбирает cert]
    │                                          [из /etc/letsencrypt/live/...
    │                                           или self-signed]
    │                                                    │
    │◄─ TLS ServerHello ─────────────────────────────────│
    │    selected cipher: TLS_AES_256_GCM_SHA384         │
    │    ALPN: h2                                        │
    │    + Certificate (0 или 1 warning: self-signed)    │
    │    + CertificateVerify                             │
    │    + Finished                                      │
    │                                                    │
    │── Finished ───────────────────────────────────────►│
    │                                                    │
    ▼                                                    ▼
```

После этого все пакеты между браузером и nginx шифруются AES-256-GCM с ключом, выведенным из
ECDHE-ключей с эфемерными ключами (forward secrecy).

**TLS-настройки nginx из `nginx-lockdown.conf`:**
```nginx
ssl_protocols       TLSv1.2 TLSv1.3;      # никаких SSLv3/TLSv1.0/TLSv1.1
ssl_ciphers         HIGH:!aNULL:!MD5;     # только safe-suites, без anon и MD5
ssl_prefer_server_ciphers on;             # сервер выбирает, не клиент
```

### 6.4 HTTP/2 запрос от браузера

Через ALPN-выбор браузер и nginx договорились на `h2` (HTTP/2). Браузер открывает stream 1:

```
HEADERS (stream=1)
    :method: GET
    :path: /
    :scheme: https
    :authority: <YOUR_DOMAIN>:4443
    user-agent: Mozilla/5.0 ...
    accept: text/html,application/xhtml+xml,...
    accept-language: ru-RU,ru;q=0.9,en;q=0.8
    accept-encoding: gzip, deflate, br
```

### 6.5 nginx обрабатывает запрос

Nginx проверяет по `server { server_name <YOUR_DOMAIN>; }` — совпадает.
Ищет location: `/` → первый подходящий `location / { proxy_pass http://127.0.0.1:8001; }`.

**Что nginx делает дальше (строки 47–54 nginx-lockdown.conf):**
```nginx
location / {
    proxy_pass http://127.0.0.1:8001;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

Nginx **конвертит HTTP/2-запрос браузера в HTTP/1.1-запрос** к uvicorn'у. Это нормально: uvicorn
тоже умеет в HTTP/2, но связка «nginx frontend + uvicorn backend» традиционно ходит по HTTP/1.1 —
меньше overhead на loopback, и WebSocket-upgrade тоже идёт через HTTP/1.1.

### 6.6 HTTP/1.1 запрос nginx → uvicorn

```
GET / HTTP/1.1
Host: <YOUR_DOMAIN>
X-Real-IP: <real client IP>
X-Forwarded-For: <real client IP>
X-Forwarded-Proto: https
Connection: close
User-Agent: Mozilla/5.0 ...
```

### 6.7 uvicorn передаёт в FastAPI

uvicorn — это ASGI-сервер. Он принимает HTTP-запрос, формирует ASGI scope, и вызывает приложение
FastAPI (`web.main:app`). FastAPI прогоняет запрос через middleware (CORS), затем ищет роут.

**web/main.py, строки 50–52:**
```python
@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")
```

Это обработчик корневого пути. Он возвращает `FileResponse` — специальный response FastAPI, который
за кулисами использует `aiofiles` для асинхронного чтения файла и `ETag` по mtime.

### 6.8 FastAPI → uvicorn → nginx → браузер (обратный путь)

```
uvicorn ASGI → HTTP/1.1 response:
    HTTP/1.1 200 OK
    Content-Type: text/html; charset=utf-8
    Content-Length: 2731
    ETag: "7f8a9b-18a3"
    <тело index.html>

nginx превращает в HTTP/2:
    HEADERS frame stream=1:
        :status: 200
        content-type: text/html; charset=utf-8
        content-length: 2731
        + (добавленные nginx'ом заголовки):
        strict-transport-security: max-age=31536000
        x-content-type-options: nosniff
        x-frame-options: DENY
        referrer-policy: strict-origin-when-cross-origin
    DATA frame stream=1:
        <HTML-тело>
    DATA frame stream=1 (END_STREAM):
        (empty)

Браузер парсит HTML, встречает:
    <link rel="stylesheet" href="/static/style.css" />
    <script src="/static/app.js"></script>

Для каждого ресурса — отдельный HTTP/2-stream на том же TLS-соединении:
    stream=3: GET /static/style.css  → 200 OK, text/css
    stream=5: GET /static/app.js     → 200 OK, application/javascript
```

### 6.9 Как FastAPI раздаёт /static/*

**web/main.py, строка 47:**
```python
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
```

`StaticFiles` — встроенный ASGI-обработчик. Он читает файл, выставляет корректный `Content-Type`
(по расширению через `mimetypes`), добавляет `ETag` и `Last-Modified`, поддерживает `Range`.

`STATIC_DIR = Path(__file__).parent / "static"` (строка 20) — это `/opt/lockdown-web/web/static` в проде.

### 6.10 Полная диаграмма пути запроса

```
 Браузер                  nginx                 uvicorn           FastAPI
  │                        │                       │                 │
  │─ TCP SYN :4443 ───────►│                       │                 │
  │                        │                       │                 │
  │─ TLS ClientHello ─────►│                       │                 │
  │◄─ TLS ServerHello ─────│ (handshake, 1-RTT)    │                 │
  │                        │                       │                 │
  │─ HTTP/2 GET / ────────►│                       │                 │
  │   :authority:          │                       │                 │
  │    <your_subdomain>        │                       │                 │
  │    .fvds.ru:4443       │                       │                 │
  │                        │                       │                 │
  │                        │─ HTTP/1.1 GET / ─────►│                 │
  │                        │  Host: ...            │                 │
  │                        │  X-Forwarded-For: ... │                 │
  │                        │                       │                 │
  │                        │                       │─ ASGI scope ───►│
  │                        │                       │                 │
  │                        │                       │                 ├─ routing: match "/"
  │                        │                       │                 │
  │                        │                       │                 ├─ FileResponse(
  │                        │                       │                 │     STATIC_DIR /
  │                        │                       │                 │     "index.html")
  │                        │                       │                 │
  │                        │                       │◄─ ASGI response─│
  │                        │◄── 200 OK HTTP/1.1 ───│                 │
  │                        │  text/html, 2731 B    │                 │
  │                        │                       │                 │
  │                        │ (добавляет HSTS,      │                 │
  │                        │  X-Frame-Options,     │                 │
  │                        │  X-CTO, Referrer)     │                 │
  │                        │                       │                 │
  │◄─ HTTP/2 :status=200 ──│                       │                 │
  │  DATA frames:          │                       │                 │
  │  <html>...</html>      │                       │                 │
  │                        │                       │                 │
  (браузер парсит, шлёт     │                       │                 │
   ещё streams за CSS/JS)  │                       │                 │
```

**Итого по задержкам (RTT ~30ms до VPS):**
- TCP handshake: ~30 ms
- TLS handshake (1-RTT): ~30 ms (2-RTT для 1.2 = ~60 ms)
- HTTP-запрос + ответ: ~30 ms
- **Первая отрисовка HTML: ~90–120 ms**

После этого браузер выполняет `app.js`. Скрипт смотрит `localStorage['lockdown_token']`:
- если токена нет — показывает login-view (`bootLogin()`, строка 225);
- если есть — сразу показывает dash-view и открывает WebSocket (`bootDashboard(existing)`, строки 211–222).

---

## 7. Путь запроса: логин (POST /auth/login)

### 7.1 Сценарий

Админ в login-form вводит username/password, жмёт «Войти». JS отправляет `fetch('/auth/login')`.

### 7.2 JS-сторона

**web/static/app.js, строки 27–40:**
```javascript
async function login(username, password) {
  const res = await fetch("/auth/login", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `HTTP ${res.status}`);
  }
  const data = await res.json();
  localStorage.setItem(TOKEN_KEY, data.access_token);
  return data.access_token;
}
```

Ключевое:
- POST с JSON-телом `{"username": "admin", "password": "supersecret"}`.
- На ошибку — парсит JSON и кидает `Error` с `detail` (FastAPI стандартно кладёт сообщение в `detail`).
- На успех — кладёт `access_token` в `localStorage` и возвращает его.

### 7.3 Сетевой путь (такой же, как в п. 6)

```
Браузер → TLS → nginx (HTTP/2) → nginx (HTTP/1.1) → uvicorn → FastAPI /auth/login
```

HTTP-запрос:
```
POST /auth/login HTTP/2
:authority: <YOUR_DOMAIN>:4443
content-type: application/json
content-length: 52

{"username":"admin","password":"supersecret"}
```

### 7.4 Обработчик логина

**web/routes_api.py, строки 9–15:**
```python
@router.post("/auth/login", response_model=TokenResponse)
def login(payload: LoginPayload, settings: Settings = Depends(get_settings)) -> TokenResponse:
    if payload.username != settings.admin_username:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    if not verify_password(payload.password, settings.admin_password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    return issue_token(payload.username, settings)
```

Шаги:
1. **Валидация Pydantic:** FastAPI автоматически парсит JSON и валидирует через `LoginPayload`
   (из `web/auth.py`, `class LoginPayload(BaseModel): username: str; password: str`).
   Если не JSON или полей нет — возвращается 422 Unprocessable Entity.
2. **Проверка username:** `payload.username != settings.admin_username`. Сравнение строк
   (не `constant_time_compare`, но username всё равно известен заранее).
3. **Проверка пароля:** `verify_password(payload.password, settings.admin_password_hash)`.

### 7.5 bcrypt-проверка пароля

**web/auth.py, строки 26–32:**
```python
def verify_password(plain: str, hashed: str) -> bool:
    if not hashed:
        return False
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False
```

- `bcrypt.checkpw` — **constant-time** сравнение, не подвержено timing-атакам на уровне байт.
- `hashed` — это уже сгенерированный hash из `.env` вида `$2b$12$abc...`. Он содержит **соль внутри**
  (это свойство bcrypt: префикс содержит version + cost + salt + hash).
- bcrypt сам извлекает соль, хеширует `plain` с той же солью и сравнивает полученный hash с сохранённым.

**Cost factor:** bcrypt по умолчанию `gensalt(12)`, что означает 2^12 = 4096 итераций.
На современном CPU это ~100 ms. Это сознательно: чтобы брутфорс был дорогой.

### 7.6 JWT-issuance

**web/auth.py, строки 35–42:**
```python
def issue_token(username: str, settings: Settings) -> TokenResponse:
    expires = datetime.now(timezone.utc) + timedelta(hours=settings.jwt_expires_hours)
    token = jwt.encode(
        {"sub": username, "exp": expires},
        settings.jwt_secret,
        algorithm="HS256",
    )
    return TokenResponse(access_token=token, expires_in=settings.jwt_expires_hours * 3600)
```

**Что такое JWT (JSON Web Token, RFC 7519):**

JWT — это три base64url-секции, разделённые точкой:
```
header.payload.signature
```

**Header** — JSON вида:
```json
{"typ": "JWT", "alg": "HS256"}
```

**Payload** (claims) — JSON вида:
```json
{"sub": "admin", "exp": 1745434800}
```
- `sub` (subject) — чей токен. Стандартный registered claim из RFC 7519.
- `exp` (expiration time) — unix timestamp, после которого токен считается expired.

**Signature** — HMAC-SHA256 (алгоритм `HS256`) от строки `base64url(header) + "." + base64url(payload)`
с секретным ключом `settings.jwt_secret`. Без знания секрета подделать подпись нельзя.

**Почему HS256, а не RS256:**
- HS256 — симметричный (один секрет, используется и для подписи, и для проверки).
- RS256 — асимметричный (подпись приватным ключом, проверка публичным).
- В нашей архитектуре один бэкенд, нет распределённой валидации токенов — симметричный секрет проще.
- RS256 был бы нужен, если бы токен выпускался одним сервисом, а проверялся другим (и мы не хотели
  раздавать им симметричный секрет).

**Длина токена:** ~220 символов. Укладывается в `Authorization: Bearer ...` header без проблем.

**Хранение на клиенте:** `localStorage['lockdown_token']`. Это уязвимо к XSS (если злоумышленник
внедрит JS, он прочитает токен), но мы **не используем cookies** — значит, и CSRF невозможен по
определению. Это сознательный trade-off (см. секцию 11).

### 7.7 Response

```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIs...",
  "token_type": "bearer",
  "expires_in": 86400
}
```

- `token_type: bearer` — по стандарту OAuth 2.0, указывает схему заголовка.
- `expires_in` — секунды до протухания (в нашем случае `24 * 3600 = 86400`).

### 7.8 Полная диаграмма логина

```
Браузер                        nginx                 uvicorn         FastAPI          bcrypt
  │                              │                      │               │                │
  │ POST /auth/login             │                      │               │                │
  │ {user,pass}                  │                      │               │                │
  │─────────TLS───────────────── │                      │               │                │
  │                              │                      │               │                │
  │─ HTTP/2 stream=7 POST ─────► │                      │               │                │
  │                              │─ HTTP/1.1 POST ────► │               │                │
  │                              │                      │─ ASGI ───────►│                │
  │                              │                      │               │                │
  │                              │                      │               ├─ Pydantic      │
  │                              │                      │               │  parse JSON    │
  │                              │                      │               │  → LoginPayload│
  │                              │                      │               │                │
  │                              │                      │               ├─ username check│
  │                              │                      │               │                │
  │                              │                      │               ├─ checkpw ─────►│
  │                              │                      │               │                │ (100 ms)
  │                              │                      │               │◄─ True/False ──│
  │                              │                      │               │                │
  │                              │                      │               ├─ jwt.encode    │
  │                              │                      │               │  HS256         │
  │                              │                      │               │                │
  │                              │                      │◄─ 200 OK ─────│                │
  │                              │◄─ 200 OK ────────────│  {access_token}│               │
  │◄─ 200 OK ──────────────────  │                      │               │                │
  │  {access_token: "eyJ..."}    │                      │               │                │
  │                              │                      │               │                │
  ├─ localStorage.setItem        │                      │               │                │
  │                              │                      │               │                │
  ├─ bootDashboard(token)        │                      │               │                │
  │                              │                      │               │                │
```

### 7.9 Типичные ошибки и что в них идёт

| Сценарий                 | Код | detail                   | Где в коде                              |
|--------------------------|-----|--------------------------|----------------------------------------|
| Неверный JSON            | 422 | Pydantic-validation-error| FastAPI автоматически                   |
| Нет поля `password`      | 422 | `field required`         | Pydantic                                |
| Неверный логин           | 401 | "Invalid credentials"    | `routes_api.py:12`                      |
| Неверный пароль          | 401 | "Invalid credentials"    | `routes_api.py:14`                      |
| `admin_password_hash=""` | 401 | "Invalid credentials"    | `auth.py:27` (`if not hashed: return False`) |

Мы сознательно возвращаем одинаковое `"Invalid credentials"` и на неверный логин, и на неверный пароль —
чтобы атакующий не мог перечислять существующие username'ы.

### 7.10 Использование токена в последующих запросах

После логина `app.js` делает два других типа запросов:
1. **REST** (`fetch`) — шлёт `Authorization: Bearer <token>` заголовок.
   В нашем коде JS это пока не делает активно для `/api/stats` (мы ходим только через WS),
   но endpoint `GET /api/stats` требует `Depends(require_auth)` (см. `routes_api.py:19`).
2. **WebSocket** — токен передаётся как **query-параметр** `?token=...` в URL.
   Это потому что в браузерном WebSocket-API нельзя задать кастомные заголовки.
   Подробнее в секции 8.

---

## 8. Путь запроса: WebSocket /ws/stats

### 8.1 Сценарий

Дашборд хочет видеть живые счётчики трафика. Вместо опроса `/api/stats` каждые 2 секунды (polling)
используется WebSocket — одно постоянное соединение, сервер сам пушит снимки.

### 8.2 Handshake (HTTP Upgrade → 101 Switching Protocols)

WebSocket начинается как **обычный HTTP/1.1-запрос** с особым заголовком `Upgrade: websocket`.
Это RFC 6455.

**Клиент (JS, app.js строки 92–97):**
```javascript
const proto = location.protocol === "https:" ? "wss" : "ws";
const url = `${proto}://${location.host}/ws/stats?token=${encodeURIComponent(this.token)}`;
this.onMetric({ url, state: "CONNECTING" });
this.log(`connect → ${url}`);
const ws = new WebSocket(url);
```

Браузер формирует:
```
GET /ws/stats?token=eyJhbGc... HTTP/1.1
Host: <YOUR_DOMAIN>:4443
Upgrade: websocket
Connection: Upgrade
Sec-WebSocket-Key: <random 16 bytes base64>
Sec-WebSocket-Version: 13
Origin: https://<YOUR_DOMAIN>:4443
```

**Важно:** поскольку страница открылась по HTTPS, браузер использует **`wss://`** (WebSocket Secure),
а это значит — handshake идёт внутри TLS-туннеля, уже установленного. Ключ `Sec-WebSocket-Key`
нужен только для подтверждения «я понимаю WS-протокол», не для шифрования.

### 8.3 Nginx и handshake

В nginx есть специальный блок для WebSocket:

**deploy/nginx-lockdown.conf, строки 34–45:**
```nginx
location /ws/ {
    proxy_pass http://127.0.0.1:8001;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_read_timeout 3600s;
    proxy_send_timeout 3600s;
}
```

Ключевое:
- `proxy_http_version 1.1` — nginx к upstream идёт по HTTP/1.1 (HTTP/2 в nginx → upstream WebSocket не работает).
- `proxy_set_header Upgrade $http_upgrade` — пробрасываем `Upgrade: websocket`.
- `proxy_set_header Connection "upgrade"` — **ключевой заголовок**: по умолчанию nginx
  выставляет `Connection: close` на upstream, что убивает WS. Принудительно ставим "upgrade".
- `proxy_read_timeout 3600s` — таймаут чтения 1 час. Без этой настройки nginx закроет неактивное
  (в плане байтов) соединение через 60 секунд по умолчанию, и WS будет бесконечно реконнектиться.

### 8.4 Uvicorn и FastAPI принимают handshake

FastAPI видит эндпоинт `@router.websocket("/ws/stats")` (в `web/routes_ws.py:17`). uvicorn
не требует отдельной настройки для WS — он автоматически поддерживает `websockets` пакет
через `uvicorn[standard]` (см. `web/requirements.txt:2`).

**web/routes_ws.py, строки 17–28:**
```python
@router.websocket("/ws/stats")
async def ws_stats(
    ws: WebSocket,
    token: str | None = Query(default=None),
    settings: Settings = Depends(get_settings),
) -> None:
    try:
        authorize_ws_token(token, settings)
    except Exception:
        await ws.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await ws.accept()
```

Последовательность:
1. **Query-parameter `token`** парсится автоматически через `Query(default=None)`.
   Если `?token=` не пришёл — `token == None`, и `authorize_ws_token` кинет `HTTPException`.
2. `authorize_ws_token` валидирует токен. При неуспехе — `ws.close(code=1008)` (Policy Violation).
3. При успехе — `ws.accept()` отправляет HTTP 101 Switching Protocols:

```
HTTP/1.1 101 Switching Protocols
Upgrade: websocket
Connection: Upgrade
Sec-WebSocket-Accept: <SHA1(client_key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11") base64>
```

После этого соединение **больше не HTTP** — это сырой TCP-сокет, по которому идут WebSocket-фреймы
(RFC 6455).

**Почему отдельная функция `authorize_ws_token`, а не `Depends(require_auth)`:**

FastAPI не запускает зависимости `Depends(require_auth)` в WebSocket-endpoint'ах так же, как в
REST — зависимости типа `Request` не доступны (WS не имеет объекта `Request`), и для получения
токена нам надо вручную достать из `Query`.

**web/auth.py, строки 68–71:**
```python
def authorize_ws_token(token: str | None, settings: Settings) -> str:
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Missing token")
    return _decode_token(token, settings)
```

Обратите внимание: это не `Depends`, это обычная функция. Вызывается явно внутри `ws_stats`.

### 8.5 Три параллельных корутины

**web/routes_ws.py, строки 30–47:**
```python
await ws.accept()
poller = ws.app.state.poller
queue = poller.subscribe()

sender = asyncio.create_task(_sender_loop(ws, queue), name="ws-sender")
heartbeat = asyncio.create_task(_heartbeat_loop(ws), name="ws-heartbeat")
reader = asyncio.create_task(_reader_loop(ws), name="ws-reader")

done, pending = await asyncio.wait(
    [sender, heartbeat, reader],
    return_when=asyncio.FIRST_COMPLETED,
)
for task in pending:
    task.cancel()
poller.unsubscribe(queue)
```

Три корутины живут параллельно в рамках одного connection'а:

#### 8.5.1 `_sender_loop`

**web/routes_ws.py, строки 50–53:**
```python
async def _sender_loop(ws: WebSocket, queue: asyncio.Queue) -> None:
    while True:
        payload = await queue.get()
        await ws.send_text(json.dumps({"type": "snapshot", **payload}))
```

Ждёт на собственной `asyncio.Queue` (индивидуальная для каждого WS-клиента), когда туда положит
что-то `StatsPoller._broadcast()`. Как только приходит — пушит клиенту через `send_text`.

Формат сообщения:
```json
{
  "type": "snapshot",
  "snapshot": {"alice": {"uplink": 12345, "downlink": 67890}, ...},
  "ts": 1713523200.123,
  "mock": false
}
```

#### 8.5.2 `_heartbeat_loop`

**web/routes_ws.py, строки 56–59:**
```python
async def _heartbeat_loop(ws: WebSocket) -> None:
    while True:
        await asyncio.sleep(HEARTBEAT_INTERVAL_SEC)
        await ws.send_text(json.dumps({"type": "ping", "ts": time.time()}))
```

`HEARTBEAT_INTERVAL_SEC = 30.0` (строка 14). Каждые 30 секунд шлёт клиенту `{"type":"ping","ts":...}`.

**Зачем application-level ping, если есть WS control frames (`opcode=0x9` ping/pong)?**
- Control frames определены в RFC 6455, но **не все промежуточные прокси и middleboxes их пропускают**
  корректно.
- Через application-level JSON-сообщение мы гарантируем end-to-end проверку.
- Плюс мы **меряем RTT** (клиент отправляет `pong` с тем же `ts`, сервер сравнивает с текущим временем).

На стороне клиента (app.js, строки 131–140):
```javascript
if (msg.type === "ping") {
    this.lastPingSentAt = Date.now();
    ws.send(JSON.stringify({ type: "pong", ts: msg.ts }));
    this.log("ping → pong");
} else if (msg.type === "pong") {
    if (this.lastPingSentAt) {
        this.rttMs = Date.now() - this.lastPingSentAt;
        this.onMetric({ rtt: `${this.rttMs} ms` });
    }
}
```

**Важное наблюдение:** в коде есть небольшая неточность — клиент не инициирует ping сам, он только
отвечает на сервер-ping, поэтому `lastPingSentAt` помечается **в момент получения ping от сервера**,
не в момент отправки. То есть RTT измеряет время от получения серверного ping до получения pong-back —
что фактически равно времени на ответ клиента + туда-обратно. На практике это близко к RTT,
но формально это не классический RTT.

#### 8.5.3 `_reader_loop`

**web/routes_ws.py, строки 62–70:**
```python
async def _reader_loop(ws: WebSocket) -> None:
    while True:
        raw = await ws.receive_text()
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if msg.get("type") == "pong":
            continue
```

Читает входящие сообщения от клиента. На практике клиент шлёт только `{"type":"pong"}`,
что мы игнорируем (но RTT меряет клиент у себя на основе `ts`).

**Важная роль reader_loop:** если клиент закрывает WS (tab closed, network drop), `receive_text`
кидает `WebSocketDisconnect`. Это завершает корутину, триггерит `asyncio.wait(return_when=FIRST_COMPLETED)`,
что инициирует cleanup.

### 8.6 Как закрытие одной корутины завершает все

**web/routes_ws.py, строки 37–47:**
```python
done, pending = await asyncio.wait(
    [sender, heartbeat, reader],
    return_when=asyncio.FIRST_COMPLETED,
)
for task in pending:
    task.cancel()
poller.unsubscribe(queue)
for task in done:
    exc = task.exception()
    if exc and not isinstance(exc, (WebSocketDisconnect, asyncio.CancelledError)):
        log.warning("ws task %s ended with: %s", task.get_name(), exc)
```

Сценарии:
- **Клиент закрыл соединение:** `reader_loop` получает `WebSocketDisconnect`, завершается. `wait()`
  возвращается с первой done-таской. Мы отменяем оставшиеся две (`sender`, `heartbeat`),
  отписываемся от poller'а.
- **Сеть упала:** какая-то из `send_text` кинет `RuntimeError` или `WebSocketDisconnect`. Аналогично.
- **Сервер стопается (poller умер):** `queue.get()` будет бесконечно ждать — но тогда lifespan-shutdown
  FastAPI отменит весь connection handler через `asyncio.CancelledError`.

`poller.unsubscribe(queue)` — удаляет очередь из списка подписчиков, чтобы poller не слал в неё
снимки после ухода клиента. Если бы не удаляли — `queue.put_nowait` в будущем кинул бы
`QueueFull` (поскольку никто не читает), и poller бы увидел `dead` subscriber сам — но лучше
явно чистить.

### 8.7 Reconnect с exponential backoff

**web/static/app.js, строки 144–155:**
```javascript
ws.onclose = (ev) => {
    clearInterval(this._uptimeTimer);
    this.onMetric({ state: `CLOSED (${ev.code})`, uptime: "—" });
    this.log(`close code=${ev.code} reason=${ev.reason || "—"}`);
    if (this.closed) return;
    this.reconnectCount += 1;
    this.onMetric({ reconn: this.reconnectCount });
    const delay = this.retryDelay;
    this.retryDelay = Math.min(this.retryDelay * 2, 30000);
    this.log(`reconnect in ${delay}ms`);
    setTimeout(() => this.connect(), delay);
};
```

- Начальный `retryDelay = 1000` (строка 73).
- Каждый disconnect удваивает: `1s → 2s → 4s → 8s → 16s → 30s` (max capped).
- После успешного open: `this.retryDelay = 1000` (строка 103) — сбрасываем.
- `this.closed` — флаг «пользователь нажал logout», чтобы не реконнектились после выхода.

Зачем backoff: если VPS ушёл в down, не стоит долбиться в него раз в секунду 100 клиентов одновременно —
это DDoS самого себя при восстановлении. Exponential backoff — стандартная практика (RFC 7231 HTTP
тоже это упоминает в контексте Retry-After).

### 8.8 Полная диаграмма WebSocket

```
Браузер (JS)                    nginx                    uvicorn/FastAPI            StatsPoller
    │                              │                          │                          │
    │─── HTTP/1.1 GET /ws/stats? ─►│                          │                          │
    │    token=eyJ...              │                          │                          │
    │    Upgrade: websocket        │                          │                          │
    │    Sec-WebSocket-Key: abc    │                          │                          │
    │                              │                          │                          │
    │                              │─── GET /ws/stats? ──────►│                          │
    │                              │    Upgrade: websocket    │                          │
    │                              │    Connection: upgrade   │                          │
    │                              │                          │                          │
    │                              │                          ├─ Query.token = "eyJ..."   │
    │                              │                          ├─ authorize_ws_token       │
    │                              │                          │  → _decode_token (HS256) │
    │                              │                          │  → OK, "admin"           │
    │                              │                          │                          │
    │                              │                          ├─ ws.accept()             │
    │                              │                          │                          │
    │                              │◄── 101 Switching Proto ──│                          │
    │                              │    Sec-WebSocket-Accept  │                          │
    │◄── 101 Switching Protocols ──│                          │                          │
    │                              │                          │                          │
    │                              │                          ├─ poller.subscribe() ────►│
    │                              │                          │◄── asyncio.Queue(8) ─────│
    │                              │                          │                          │
    │                              │                          ├─ create_task(sender)     │
    │                              │                          ├─ create_task(heartbeat)  │
    │                              │                          ├─ create_task(reader)     │
    │                              │                          │                          │
    │                              │                          │  sender.await queue.get()│
    │                              │                          │                          ├── каждые 2с:
    │                              │                          │                          │   subprocess
    │                              │                          │                          │   xray api
    │                              │                          │                          │   statsquery
    │                              │                          │                          │   → snapshot
    │                              │                          │                          │
    │                              │                          │◄── queue.put_nowait ─────│  _broadcast()
    │                              │                          │                          │
    │                              │                          ├─ sender pops, send_text  │
    │                              │                          │                          │
    │◄── WS frame: {"type":        │                          │                          │
    │    "snapshot", ...} ─────────│                          │                          │
    │                              │                          │                          │
    │  (рендерим таблицу           │                          │                          │
    │   renderUsers)               │                          │                          │
    │                              │                          │                          │
    │                              │                          │  heartbeat: sleep 30s    │
    │◄── WS frame: {"type":"ping", │                          │                          │
    │    "ts": 1713523230} ────────│                          │                          │
    │                              │                          │                          │
    │─── WS frame: {"type":        │                          │                          │
    │    "pong", "ts": 1713523230}─│                          │                          │
    │                              │                          ├─ reader: игнор pong      │
    │                              │                          │                          │
    │                              │                          │  (RTT считается у клиента│
    │                              │                          │   как Date.now() -       │
    │                              │                          │   lastPingSentAt)        │
    │                              │                          │                          │
    │  (2с проходит, ещё один      │                          │                          │
    │   snapshot... и т.д.)        │                          │                          │
    │                              │                          │                          │
    │                              │                          │                          │
    │ (пользователь закрыл tab)    │                          │                          │
    │─── WS close frame ──────────►│                          │                          │
    │                              │                          │                          │
    │                              │                          ├─ reader: receive_text    │
    │                              │                          │  → WebSocketDisconnect   │
    │                              │                          │                          │
    │                              │                          ├─ done = {reader}         │
    │                              │                          ├─ cancel sender, heartbeat│
    │                              │                          ├─ poller.unsubscribe ───►│
    │                              │                          │                          │
    │                              │                          │  (функция ws_stats       │
    │                              │                          │   возвращается)          │
```

---

## 9. Путь запроса: фоновый опрос xray

Это не пользовательский путь запроса — это **фоновая задача**, которая крутится всё время жизни
приложения. Но важна для защиты курсовой, т.к. здесь происходит межпроцессное взаимодействие
(IPC) через subprocess + gRPC.

### 9.1 Старт poller'а

**web/main.py, строки 23–32:**
```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    poller = StatsPoller(interval_sec=settings.poll_interval_sec, mock=settings.mock_stats)
    await poller.start()
    app.state.poller = poller
    try:
        yield
    finally:
        await poller.stop()
```

`lifespan` — это ASGI-lifespan-protocol, встроенный в FastAPI. Это callback, который вызывается
**один раз при старте uvicorn** (до первого запроса) и **один раз при остановке** (перед выходом).

- `poller.start()` — создаёт бесконечную задачу.
- `app.state.poller = poller` — сохраняем в атрибуте `state`, чтобы другие хэндлеры могли получить
  доступ через `request.app.state.poller` (см. `routes_api.py:20`).
- `yield` — здесь начинается обработка запросов.
- `poller.stop()` — отменяет задачу перед шатдауном.

### 9.2 Цикл poller'а

**web/stats_poller.py, строки 109–125:**
```python
async def _run(self) -> None:
    log.info("StatsPoller started (mock=%s, interval=%ss)", self.mock, self.interval)
    while not self._stopped.is_set():
        try:
            if self.mock and self._mock_gen is not None:
                snap = self._mock_gen.tick()
            else:
                snap = await asyncio.to_thread(_query_xray)
            self._snapshot = snap
            self._last_ts = time.time()
            await self._broadcast()
        except Exception as e:
            log.exception("poller iteration failed: %s", e)
        try:
            await asyncio.wait_for(self._stopped.wait(), timeout=self.interval)
        except asyncio.TimeoutError:
            pass
```

Ключевые моменты:
- `self._stopped` — `asyncio.Event`, взводится в `stop()`.
- `asyncio.to_thread(_query_xray)` — запускает `_query_xray` в **потоке из пула**. Это важно: subprocess
  блокирующий, вызывать его в asyncio event loop'е напрямую нельзя (заморозит все другие корутины).
  `asyncio.to_thread` — стандартный способ обернуть синхронный код.
- Try/except: любая ошибка логируется, но цикл не умирает (subprocess может таймаутнуться, xray
  может быть не запущен — это нормально, через 2 секунды попробуем снова).
- Wait с timeout: это **прерываемый sleep**. Если `stop()` взвёл `_stopped`, `wait_for` не будет
  ждать до истечения timeout, а сразу вернётся — и цикл выйдет.

### 9.3 `_query_xray`: subprocess → gRPC

**web/stats_poller.py, строки 14–44:**
```python
def _query_xray(server: str = "127.0.0.1:10085", timeout: int = 10) -> Snapshot:
    try:
        result = subprocess.run(
            ["xray", "api", "statsquery", f"--server={server}"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        log.warning("xray subprocess failed: %s", e)
        return {}

    if result.returncode != 0:
        return {}

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}

    stats: Snapshot = {}
    for stat in data.get("stat", []):
        name = stat.get("name", "")
        value = int(stat.get("value", 0))
        parts = name.split(">>>")
        if len(parts) == 4 and parts[0] == "user" and parts[2] == "traffic":
            email, direction = parts[1], parts[3]
            stats.setdefault(email, {"uplink": 0, "downlink": 0})
            if direction in ("uplink", "downlink"):
                stats[email][direction] = value
    return stats
```

**Цепочка вызовов:**

```
_query_xray (Python)
    │
    ├─ subprocess.run(["xray", "api", "statsquery", "--server=127.0.0.1:10085"])
    │                                    │
    │                                    ▼
    │   [fork + exec "xray" бинарника]
    │                                    │
    │                                    ▼
    │   xray-core (Go): команда "api statsquery"
    │   из подкоманды — это gRPC-клиент к Xray-серверу
    │                                    │
    │                                    ▼
    │   gRPC-запрос по HTTP/2 к 127.0.0.1:10085
    │   method: v2ray.core.app.stats.command.StatsService/QueryStats
    │   request: pattern="", reset=false
    │                                    │
    │                                    ▼
    │   На 127.0.0.1:10085 отвечает xray.service (тот же процесс, что слушает 443 и 2096!)
    │                                    │
    │                                    ▼
    │   Ответ gRPC (protobuf) — список счётчиков
    │   десериализуется в JSON:
    │   {
    │     "stat": [
    │       {"name": "user>>>alice>>>traffic>>>uplink",   "value": 12345},
    │       {"name": "user>>>alice>>>traffic>>>downlink", "value": 67890},
    │       {"name": "user>>>bob>>>traffic>>>uplink",  "value": 44},
    │       ...
    │     ]
    │   }
    │                                    │
    │                                    ▼
    │   xray-cli пишет JSON на stdout
    │                                    │
    │                                    ▼
    │   subprocess возвращает result.stdout
    │
    ├─ json.loads → data
    │
    ├─ парсим каждую запись:
    │   "user>>>alice>>>traffic>>>uplink".split(">>>")
    │   → ["user", "alice", "traffic", "uplink"]
    │   → stats["alice"]["uplink"] = 12345
    │
    ▼
    Snapshot = {"alice": {"uplink": 12345, "downlink": 67890}, ...}
```

**Формат имени статистики:**
Xray генерирует имена вида `user>>>EMAIL>>>traffic>>>DIRECTION`, где:
- `user` — это prefix для user-level stats (есть ещё `inbound`, `outbound`).
- `EMAIL` — метка пользователя (поле `email` в `clients[]` в config'е).
- `traffic` — категория (может быть ещё connection, но это редко).
- `DIRECTION` — `uplink` (от клиента к целевому серверу) или `downlink` (обратно).

**Почему `>>>` как разделитель:** Xray по историческим причинам. Не `/` и не `_`, чтобы email может
содержать такие символы.

### 9.4 Pub-sub и broadcast

**web/stats_poller.py, строки 127–136:**
```python
async def _broadcast(self) -> None:
    msg = self.latest()
    dead: List[asyncio.Queue] = []
    for q in self._subscribers:
        try:
            q.put_nowait(msg)
        except asyncio.QueueFull:
            dead.append(q)
    for q in dead:
        self.unsubscribe(q)
```

- `self._subscribers` — список `asyncio.Queue(maxsize=8)`.
- `put_nowait` — **не блокирующая** вставка. Если очередь заполнилась (8 снимков накопилось) —
  кидает `asyncio.QueueFull`.
- Мы ловим `QueueFull` и помечаем такие очереди как dead, потом удаляем.

**Семантика maxsize=8:**
- При нормальной работе клиент читает снимки быстрее, чем мы их генерим (каждые 2 секунды).
  Очередь почти всегда пустая.
- Если клиент **тормозит** (задержка сети, приостановленная вкладка в background), очередь
  накапливается. При 16 секундах без чтения — переполнение.
- При переполнении мы делаем вывод: клиент сломан, лучше его отключить (unsubscribe). Клиент
  получит WS-close через закрытие очереди → обрыв → reconnect на клиенте.

**Почему `put_nowait`, а не `put` с таймаутом:**
- `put` блокирует корутину. Если один subscriber тормозит, весь broadcast задержится.
- `put_nowait` fast-fail.

### 9.5 Почему один poller, а не по одному на клиента

**Docstring `StatsPoller` (строки 65–69):**
```python
class StatsPoller:
    """Single background poller → pub-sub broadcasting to WS subscribers.

    Why single poller: avoid spawning an xray subprocess per WS client.
    """
```

Каждый вызов `xray api statsquery` — это:
- fork/exec нового процесса `xray` (несколько десятков MB памяти).
- TCP-соединение на localhost + gRPC handshake.
- ~50–200 мс CPU.

Если бы у нас было 5 WS-клиентов и каждый порождал свой поллер каждые 2 секунды — это 5 процессов
xray запускается параллельно в секунду. Нагрузка линейно растёт с клиентами.

**Pub-sub** снимает эту проблему: один poller на весь процесс, все WS-клиенты читают из своих
очередей. Память и CPU — константа от числа клиентов.

### 9.6 Диаграмма фонового опроса

```
lifespan start
     │
     ▼
StatsPoller.start()
     │
     ▼
create_task(_run)
     │
     │         ┌───────────────────────────────────────────────────┐
     │         │  while not stopped:                               │
     │         │                                                   │
     │         │    snap = await asyncio.to_thread(_query_xray)    │
     │         │           │                                       │
     │         │           ▼                                       │
     │         │    [thread pool worker]                           │
     │         │           │                                       │
     │         │           ▼                                       │
     │         │    subprocess.run(["xray","api","statsquery"])    │
     │         │           │                                       │
     │         │           │  fork + exec                          │
     │         │           ▼                                       │
     │         │    [new process: xray]                            │
     │         │           │                                       │
     │         │           │  gRPC call                            │
     │         │           ▼                                       │
     │         │    127.0.0.1:10085  ─── xray.service ─┐           │
     │         │           │                           │           │
     │         │           │ response: JSON             │           │
     │         │           ▼                           │           │
     │         │    parse >>> format                   │           │
     │         │    → Snapshot                         │           │
     │         │           │                           │           │
     │         │           ▼                           │           │
     │         │    self._snapshot = snap              │           │
     │         │    self._last_ts = time.time()        │           │
     │         │                                       │           │
     │         │    _broadcast()                       │           │
     │         │       │                               │           │
     │         │       ▼                               │           │
     │         │    for q in subscribers:              │           │
     │         │       q.put_nowait(msg)               │           │
     │         │                                       │           │
     │         │                                       │           │
     │         │    await wait_for(_stopped.wait(),    │           │
     │         │                   timeout=2s)         │           │
     │         │                                       │           │
     │         └───────────────────────────────────────┘           │
     │                                                             │
     │                              ┌──────────────────────────────┘
     │                              │
     │                              ▼
     │       ┌───────────────────────────────────────┐
     ▼       ▼                                       ▼
subscribe():                                    unsubscribe():
  q = Queue(8)                                    remove q
  subscribers.append(q)                           from subscribers
  if self._snapshot:                              (вызывается из ws_stats
    q.put_nowait(latest())                         при закрытии WS)
  return q
     │
     │
     │ (каждый WS-connect делает subscribe)
     │
     ▼
Очереди прочитываются в _sender_loop (web/routes_ws.py:50-53)
```

---

## 10. Отказоустойчивость

Что происходит, если какой-то компонент упал/завис. Для каждого сценария описано:
- **Симптом:** что видит админ.
- **Поведение системы:** что она делает.
- **Восстановление:** что должно произойти.

### 10.1 Xray упал (systemctl stop xray)

**Симптом для VPN-клиентов:** новые подключения на 443/2096 не проходят (connection refused),
существующие туннели зависают.

**Симптом для админки:** snapshot в дашборде показывает пустую таблицу (`stats = {}`), но WS
продолжает работать, просто с пустыми снимками.

**Поведение системы:**
- `_query_xray` вызывает `subprocess.run(["xray", ...])`.
  - Если бинарника нет — `FileNotFoundError`. Ловится в строках 22–24, возвращается `{}`.
  - Если xray запустился как CLI, но gRPC-порт (10085) не слушает — xray-cli ответит с ненулевым
    кодом. Проверка `if result.returncode != 0: return {}` (строки 26–27).
- Пустой snapshot транслируется через WS как `{"snapshot": {}, "ts": 1713..., ...}`.
- На дашборде JS рисует «Нет данных» (app.js:51).

**Восстановление:**
- Ручное: `sudo systemctl restart xray`.
- Автоматическое: systemd сам перезапустит xray, если в его unit прописано `Restart=on-failure`
  (это стандартно для xray.service). После рестарта статистика **начинается с нуля** — Xray не
  персистит счётчики.

### 10.2 nginx упал

**Симптом:** браузер не может открыть админку (ERR_CONNECTION_REFUSED на 4443, на 80 — тоже).

**Поведение:** uvicorn продолжает слушать 127.0.0.1:8001, но снаружи не доступен.

**Восстановление:**
- `sudo systemctl restart nginx`.
- systemd-unit для nginx обычно `Restart=always` или `Restart=on-failure`.

### 10.3 web-admin упал

**Симптом:** nginx отвечает `502 Bad Gateway` на `/` и на `/ws/*`.

**Поведение:** nginx пытается `proxy_pass http://127.0.0.1:8001`, получает connection refused, возвращает
502 с дефолтным HTML.

**WS на клиенте:** `ws.onclose` → `reconnect in 1000ms` → новая попытка → снова 502 (в случае WS это
может быть 502 на апгрейд или таймаут).

**Восстановление:**
- `deploy/web-admin.service` содержит `Restart=on-failure` и `RestartSec=3` (строки 12–13).
- systemd через 3 секунды перезапустит сервис. Клиент увидит один-два reconnect и продолжит работу.

### 10.4 WebSocket оборвался (сетевой разрыв)

**Симптом:** на дашборде pill `CLOSED (1006)` или `CLOSED (1001)`.

**Коды closure:**
- `1000` — normal (клиент или сервер явно закрыли).
- `1001` — going away (tab-close, server shutdown).
- `1006` — abnormal (обрыв TCP без proper close handshake; чаще всего — сеть).
- `1008` — policy violation (наш `authorize_ws_token` failure).

**Поведение клиента (app.js, строки 144–155):**
- Инкрементит `reconnectCount`.
- Ждёт `retryDelay` мс.
- Пытается открыть заново.
- При успехе — сбрасывает `retryDelay = 1000`.

**Поведение сервера:**
- `reader_loop` получает `WebSocketDisconnect`, завершается.
- `asyncio.wait` триггерится, отменяет sender и heartbeat.
- Poller.unsubscribe() удаляет очередь клиента из списка.

### 10.5 subprocess xray таймаут

**Сценарий:** xray-cli висит больше 10 секунд (скажем, зависший inode).

**Поведение:**
- `subprocess.run(..., timeout=10)` кидает `subprocess.TimeoutExpired`.
- Ловится в строках 22–24 `stats_poller.py`, возвращается `{}`.
- Лог: `"xray subprocess failed: ..."`.

**Последствия:**
- Один снимок пустой.
- Следующая итерация через 2 секунды.
- Если xray починился — получим нормальный снимок, статистика не пропадёт (xray сам накапливает
  счётчики в памяти).

### 10.6 Poller crashed (unexpected exception)

**Сценарий:** в `_run` произошёл необработанный exception (например, `MemoryError`).

**Поведение:** try/except внутри `while` (строки 112–121) ловит **все** exception'ы, логирует
через `log.exception` и продолжает цикл. То есть poller никогда не умирает пока `_stopped` не взведён.

Это сознательное решение: скорее показывать пустую таблицу, чем роняться.

Кроме того, внешний обработчик `lifespan` перехватывает unexpected exit: если `poller._task`
завершился с exception, `poller.stop()` в finally-части кидает этот exception далее. В большинстве
ASGI-серверов это приведёт к перезапуску процесса systemd'ом.

### 10.7 Database corruption (bot.db)

**Относится к боту, не к web-admin.** SQLite файл может повредиться при:
- Отключении питания во время commit (редко с журналами WAL).
- Дедлоке нескольких процессов (у нас только бот пишет, но для надёжности — стоит WAL).
- Удалении пользователем.

**Поведение бота:** `aiosqlite` кидает `sqlite3.DatabaseError`. Бот в текущем коде это не ловит
глобально — упадёт.

**Восстановление:** бот restart (systemd), если файл повреждён — `db.init_db()` не сможет; нужен
ручной `rm bot.db` + бот заново создаст схему из `SCHEMA` в `bot/db.py:8-46`. Все данные теряются.

**Это известная слабость** — см. секцию 11 «чего нет».

### 10.8 TLS certificate expired

**Сценарий:** LE-сертификат истёк (90 дней).

**Поведение:** браузер показывает `NET::ERR_CERT_DATE_INVALID`, не пускает.

**Восстановление:** `certbot renew` (в cron), перезапуск nginx.
У нас фактически self-signed, истечёт через 10 лет — не проблема.

### 10.9 Массовая таблица «Что если»

| Компонент упал       | Что видит админ           | Что видят пользователи VPN  | Время восстановления (auto) |
|----------------------|---------------------------|------------------------------|-----------------------------|
| xray                 | Пустая таблица в дашборде | VPN не работает              | ~2 с (systemd restart)      |
| nginx                | 502/connection refused    | —                            | ~3 с (systemd restart)      |
| uvicorn/web-admin    | 502 через nginx           | —                            | ~3 с (RestartSec=3)         |
| Telegram-бот         | —                         | Новых ключей не получить     | ~3 с (systemd restart)      |
| WebSocket            | CLOSED pill, reconnect    | —                            | 1–30 с (exp backoff)        |
| VPS (hard reboot)    | Всё лежит                 | Всё лежит                    | ~30 с (systemd boots all)   |
| DNS fvds.ru          | Сайт не открывается по име| —                            | Внешний SLA хостера         |

---

## 11. Чего нет и почему (чтобы не врасплох)

Преподаватель-сетевик любит задавать «а почему нет X?». Готовим честные ответы.

### 11.1 Нет персистентной БД для статистики

**Что отсутствует:** `StatsPoller._snapshot` живёт **только в оперативной памяти** процесса
uvicorn. После перезапуска сервиса счётчики обнуляются.

**Почему:**
1. **Xray сам не персистит счётчики.** После его рестарта `xray api statsquery` возвращает пустой
   список, пока не пройдёт трафик. Если бы мы сохраняли снимки — они бы рассинхронизировались с
   реальностью после xray-рестарта.
2. **Для задач дашборда достаточно live-данных.** Админ открывает страницу — видит текущие
   значения за последние ~2 секунды.
3. **Не нужно учётное биллинг-приложение.** Это курсовой по сетям, а не ISP-провайдер. Если бы
   нужна была история, завели бы, например, Prometheus + Grafana (это стандарт).

**Альтернативы, которые не использовали:**
- SQLite с таблицей `snapshots(ts, email, uplink, downlink)` — тянуло бы диск за 100 kB в день на
  клиента, потребовало бы cleanup-задачу. Избыточно.
- Redis — отдельная зависимость, отдельный сервис, зачем в одно-пользовательском приложении.

### 11.2 Нет rate-limiting на уровне приложения

**Что отсутствует:** FastAPI не ограничивает количество запросов к `/auth/login` или `/api/stats`
с одного IP.

**Почему:**
1. **Дашборд — для одного админа.** Мы не ожидаем 100 rps.
2. **Brute-force логина защищается bcrypt'ом.** При cost=12 один verify_password = ~100 ms.
   На одно соединение (stream) — не больше ~10 попыток в секунду. На сутки — ~860k попыток.
   Для пароля из 12+ символов (с числами и спецсимволами) — не угадывается за разумное время.
3. **Сам сервер (nginx) ограничивает `client_max_body_size 1m`** — не даст слать гигантские JSON.

**Если бы добавляли:**
- `slowapi` (библиотека для FastAPI).
- Или в nginx — `limit_req_zone $binary_remote_addr zone=one:10m rate=10r/s; limit_req zone=one;`.

### 11.3 Нет аутентификации в Telegram-боте на уровне HTTP

**Что это значит:** бот общается с Telegram API через HTTPS, но **не использует Telegram Login Widget**
или OAuth. Админы определяются просто по `telegram_id in ADMIN_IDS` (жёстко прошит в
`bot/config.py`).

**Почему:**
1. **Этой проверки достаточно:** `telegram_id` приходит от Telegram-серверов, Telegram их не
   подделывает. Никакой HTTP-запрос от моего имени к боту без Telegram-посредника не возможен
   (бот вообще не имеет HTTP-endpoint'а — он long-poll'ит исходящий, не слушает входящие).
2. **Web-admin и бот — отдельные системы.** Web-admin имеет свой JWT-логин, Telegram — свой
   Telegram-login (встроенный в протокол Telegram).

Формально это не «нет аутентификации», а «аутентификация делегирована Telegram».

### 11.4 Нет кеша между запросами

**Что отсутствует:** каждый HTTP-запрос (`GET /api/stats`, например) вызывает `poller.latest()`,
который просто возвращает актуальный `_snapshot`. Нет `@lru_cache` или Redis между запросами.

**Почему:**
1. **`poller.latest()` и так возвращает cached dict.** Это фактически и есть кеш: снимок обновляется
   раз в 2 секунды фоновой задачей, а HTTP-запросы читают его.
2. **Статика (`/static/*`) кешируется nginx'ом/браузером** по `ETag` и `Last-Modified` (встроено
   в `StaticFiles` FastAPI).

**Лишний кеш был бы вредом:** usernames не вычисляются, пароли не хешируются на каждый запрос
(только на login).

### 11.5 Нет CSRF-защиты

**Что отсутствует:** нет CSRF-токенов в формах, нет SameSite-cookie-политик.

**Почему:**
1. **Мы не используем cookies.** Токен хранится в `localStorage` и передаётся как
   `Authorization: Bearer ...` header.
2. **CSRF-атака требует, чтобы браузер автоматически прикладывал к cross-origin запросам
   аутентификацию.** Cookies прикладываются автоматически. `Authorization` header — **нет**.
3. **Таким образом, CSRF невозможен по определению.** Злоумышленник с другого сайта не сможет
   заставить мой браузер отправить `Authorization: Bearer ...`, потому что он не имеет доступа к
   `localStorage['lockdown_token']` (same-origin policy).
4. **XSS остаётся угрозой**, но это уже другой класс атаки (требует внедрения JS на сам сайт).
   Мы митигируем его через `X-Content-Type-Options: nosniff` и избегание `innerHTML` с
   user-supplied данными (в `renderUsers` есть `${email}` без экранирования, но email валидируется
   xray-сервером — там строгий формат).

### 11.6 CORS разрешён отовсюду (`allow_origins=["*"]`)

**web/main.py, строки 37–42:**
```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
```

**Это не риск** потому что:
1. **Нет cookies** — CORS с `*` разрешает чтение ответа только если не слать credentials.
   Мы не слать credentials.
2. **JWT в заголовке** — атакующий сайт не может добавить `Authorization: Bearer`, не имея токена.
3. **Функционально нужно `*`**, так как в проде сайт открывается по IP/доменy с портом 4443, а
   разработка — с localhost:8001. Ограничивать до конкретных origin'ов — усложнит dev.

**Альтернативы:** явный список `allow_origins=["https://<YOUR_DOMAIN>:4443"]` в проде,
но тогда local dev ломается.

### 11.7 Нет 2FA для админа

**Почему:** TOTP (Google Authenticator) требовал бы отдельной библиотеки (`pyotp`),
хранения seed'а в БД, QR-кода для первичной регистрации. Один пользователь (я), надёжный пароль,
сетевая изоляция (порт только через TLS) — решил, что избыточно.

Если бы система выросла до нескольких админов — добавили бы 2FA.

### 11.8 Нет healthcheck на компонентах

**Есть:** `GET /healthz` в web/main.py (строки 55–57):
```python
@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}
```

**Что не проверяется:** реальная доступность xray (subprocess может падать, poller может зависать).
Полноценный healthcheck сделал бы:
```python
@app.get("/healthz")
async def healthz(request: Request):
    poller = request.app.state.poller
    if time.time() - poller._last_ts > 30:
        raise HTTPException(503, "poller stale")
    return {"ok": True, "last_snapshot_age": time.time() - poller._last_ts}
```

Не сделано, так как у нас нет load-balancer'а, который бы этим пользовался. systemd сам перезапускает
по crash'у процесса.

### 11.9 Нет HTTPS-сертификата от «нормального» CA

**Что:** Let's Encrypt не выдаёт сертификаты на `<YOUR_DOMAIN>`, т.к. общий домен `fvds.ru`
rate-limit'ится (множество клиентов FirstVDS уже взяли на своих поддоменах LE).

**Решение:** self-signed сертификат. Браузер показывает warning, но TLS работает.

**Это не означает**, что шифрование хуже — криптография та же (TLS 1.3 + AES-256-GCM). Разница только
в chain-of-trust: self-signed нет в CA Store, браузер не верит по умолчанию.

**Для курсовой это нормально**, т.к. TLS-аспект мы продемонстрируем независимо от CA. На защите:
«я попробовал LE, но был упёрт в rate-limit fvds.ru, поэтому показываю self-signed».

### 11.10 Нет отдельного пользователя (user) для web-admin

**deploy/web-admin.service, строка 8:** `User=root`.

**Почему root:** на VPS `/opt/lockdown-web/.env` содержит чувствительные данные (jwt_secret,
admin_password_hash). Под non-root'ом нужно было бы `chown` этого файла на отдельного user'а,
плюс права на `xray api statsquery` (хотя это можно дать через sudoers NOPASSWD).

**Мы сознательно не оптимизируем под multi-user**, т.к. VPS принадлежит одному админу. Если бы это
был shared-хостинг — конечно, отдельный systemd-user с минимальными правами.

### 11.11 Суммарный список «чего нет»

| Отсутствует                      | Где было бы нужно                | Наш обход                   |
|----------------------------------|----------------------------------|-----------------------------|
| БД для истории трафика           | SLA-мониторинг                   | Live-снимки в памяти        |
| Rate-limit                       | Публичное API                    | bcrypt тормозит brute-force |
| HTTP-аутент в боте               | Веб-интерфейс бота               | Нет HTTP у бота             |
| Cache на запросах                | Высоконагруженный сайт           | Poller кеширует раз в 2с    |
| CSRF-токены                      | Форма с cookie-auth              | JWT в header, не cookie     |
| Явный allow_origins              | Мульти-сайтовый сетап            | CORS open: безопасно без cookie |
| 2FA                              | Много админов / чувств.опер.     | Пока один админ             |
| Продвинутый healthz              | load-balancer                    | systemd restart             |
| CA-signed TLS                    | Публичное доверие                | self-signed (LE rate-limit) |
| Non-root user сервиса            | Shared-хостинг                   | VPS одного владельца        |

---

## Итог

Проект Lockdown в части веб-админки демонстрирует:

1. **Полный стек сетевых протоколов**: TCP, TLS (1.2/1.3 с HSTS), HTTP/1.1 (upstream), HTTP/2
   (frontend), WebSocket (RFC 6455), gRPC (над HTTP/2).
2. **TLS termination на двух уровнях**: Xray на 443/2096 (Reality+SNI masking), nginx на 4443
   (стандартный HTTPS).
3. **Authentication**: bcrypt для пароля, JWT HS256 для сессий, отдельная схема для WebSocket
   (token в query).
4. **Реал-тайм-пуш**: единый фоновый poller, pub-sub через `asyncio.Queue`, WebSocket-broadcast,
   heartbeat и reconnect с exponential backoff.
5. **IPC**: subprocess → gRPC → между двумя процессами на loopback (`xray` CLI ↔ `xray` daemon),
   Python asyncio ↔ thread pool через `asyncio.to_thread`.
6. **Системная интеграция**: systemd-service, EnvironmentFile для секретов, nginx reverse-proxy.

Для защиты курсовой этот файл — архитектурная карта. Второй файл (`CODE_WALKTHROUGH.md`) содержит
построчный разбор самого кода.
