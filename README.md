# FreeClaude — Claude Code to Google Gemini Proxy

Высокопроизводительный локальный шлюз-эмулятор Anthropic Messages API (`/v1/messages`), маршрутизирующий вызовы утилиты **Claude Code CLI** (`claude`) в **Google Gemini API** с умной ротацией ключей, контролем квот, кэшированием криптографических подписей `thoughtSignature`, каскадным переключением моделей (Fallback), автоматическим включением **Antigravity Agent Engine** для сборки проектов и поддержкой стриминга SSE (`text/event-stream`).

---

## 🚀 Возможности и архитектура

1. **Совместимость с Claude Code**:
   - Эмуляция эндпоинтов `POST /v1/messages`, `GET/HEAD /api/hello` и `POST /v1/messages/count_tokens`.
   - Полная поддержка Server-Sent Events (SSE) стриминга в реальном времени.
   - Двусторонняя трансляция системных инструкций, мульти-терн сообщений, изображений и инструментов (`tool_use` ⇄ `functionCall`, `tool_result` ⇄ `functionResponse`).
   - Кэширование и проброс криптографических подписей `thoughtSignature` в Gemini Thinking моделях (Gemini 3.x / 2.5) для непрерывной работы при итеративном выполнении команд и tool calls.
   - Глубокая санитаризация JSON-схем инструментов: очистка неподдерживаемых Protobuf-полей (`exclusiveMinimum`, `const`, `anyOf`) для устранения ошибок 400.

2. **Интеллектуальная ротация API-ключей Gemini**:
   - Чтение пула ключей из `.env` (`GEMINI_API_KEYS=key1,key2,key3,...`).
   - Round-robin распределение нагрузки между активными ключами.
   - При получении **HTTP 429 (Resource Exhausted)** ключ автоматически отправляется в кулдаун на **60 секунд**.
   - Бесшовный повтор запроса со следующим доступным ключом без прерывания сессии Claude Code.

3. **Специальный режим Antigravity для сборки проектов ("собрать проект")**:
   - При обнаружении задач генерации/сборки проекта (*"собери проект"*, *"создай проект"*, *"сборка приложения"*, *"build project"*, *"scaffold project"*) прокси автоматически активирует **Antigravity Agent Engine**.
   - Antigravity использует отдельный лимит **Antigravity (Agents)**: **60 RPM / 100 RPD / 100K TPM**, что защищает обычные Flash-модели от исчерпания их суточных лимитов (20 RPD).
   - Выполняется в изолированной песочнице с безопасным режимом генерации.

4. **Оптимизированный каскадный Fallback моделей Gemini**:
   1. **`gemini-3.8-flash`** (5 RPM / 20 RPD) — флагманская модель по умолчанию
   2. **`gemini-3.7-flash`** (5 RPM / 20 RPD) — высокоскоростной резерв
   3. **`gemini-3.6-flash`** (5 RPM / 20 RPD) — стабильный резерв
   4. **`gemini-3.5-flash-lite`** (15 RPM / **500 RPD**) — мощный объемный резерв (500 запросов в день!)
   5. **`gemini-3.1-flash-lite`** (15 RPM / **500 RPD**) — дополнительный объемный резерв
   - При перегрузке модели (HTTP 503) прокси выставляет временный кулдаун на 30 секунд и мгновенно переключается на следующую доступную модель.

5. **Интеграция с Arch Linux, systemd и FreeDesktop (XDG)**:
   - Автозапуск через фоновый `systemd --user` юнит (`freeclaude.service`).
   - Изолированное окружение Python `venv`.
   - Скрипт-обертка `launch_claude.sh` (автопроверка/запуск прокси и запуск `claude`).
   - Автоматически сгенерированные и проверенные `.desktop` ярлыки для `konsole` на рабочем столе (`~/Desktop/Claude-Code.desktop`) и в меню приложений (`~/.local/share/applications/claude-code.desktop`).

---

## 📁 Структура каталога

```
FreeClaude/
├── app/
│   ├── main.py              # FastAPI сервер, эндпоинты /v1/messages, /api/hello, /health
│   ├── config.py            # Настройки и чтение .env
│   ├── key_manager.py       # Менеджер ротации ключей и 60s cooldown
│   ├── model_fallback.py    # Координатор каскадного фолбэка моделей
│   ├── thought_signatures.py# Кэш и инъектор криптографических подписей thoughtSignature
│   ├── antigravity_engine.py# Детектор сборки проекта и движок Antigravity Agent
│   ├── translator.py        # Двусторонняя трансляция протоколов и валидация схем
│   └── gemini_client.py     # HTTP-клиент к Google Gemini API (SSE & JSON)
├── assets/
│   └── claude-icon.svg      # Векторная иконка приложения
├── scripts/
│   ├── start_proxy.sh       # Управление сервером (start, --daemon, --stop, --status)
│   ├── launch_claude.sh     # Обертка для запуска Claude Code CLI
│   └── create_desktop_shortcut.sh # Генератор .desktop ярлыка для Arch Linux
├── tests/
│   ├── test_antigravity_build.py # Тесты детектора сборки проектов
│   ├── test_translator.py   # Тесты транслятора протоколов, thoughtSignatures и SSE
│   ├── test_key_rotation.py # Тесты ротации и кулдауна ключей
│   └── test_e2e.py          # E2E тесты эндпоинтов и каскадного переключения
├── .env.example             # Пример конфигурации без секретов
├── requirements.txt         # Зависимости Python
├── pytest.ini               # Конфигурация тестового фреймворка
└── README.md
```

---

## ⚙️ Управление и запуск

### Запуск через ярлык
Двойной клик по ярлыку **Claude Code (Gemini)** на рабочем столе открывает терминал (`konsole`) и сразу запускает сессию.

### Запуск из любого терминала
```bash
/home/koksik/.gemini/antigravity-ide/scratch/claude-gemini-proxy/scripts/launch_claude.sh
```

### Управление прокси-сервером вручную
```bash
cd /home/koksik/.gemini/antigravity-ide/scratch/claude-gemini-proxy

# Проверка статуса (показывает активные ключи и кулдауны)
./scripts/start_proxy.sh --status

# Запуск в фоне
./scripts/start_proxy.sh --daemon

# Запуск в текущем терминале с живым логом переключений
./scripts/start_proxy.sh

# Остановка
./scripts/start_proxy.sh --stop
```

---

## 🧪 Запуск тестов

```bash
./venv/bin/pytest -v
```
Все 18 модульных и интеграционных тестов проходят успешно.
