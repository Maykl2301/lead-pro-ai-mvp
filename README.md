# 🚀 Lead PRO AI MVP v3.1.0

![Python](https://img.shields.io/badge/Python-3.11-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-0.109-green)
![License](https://img.shields.io/badge/License-MIT-yellow)


Система прийому та обробки **лідів** (потенційних клієнтів) з AI‑класифікацією та Telegram‑сповіщеннями.

---

## ✨ Функціонал

- 📥 `POST /leads` — прийом заявки з валідацією через Pydantic v2  
- 🧹 Нормалізація: email (RFC), phone (regex), name (trim + title)  
- 🤖 AI‑summary + класифікація (`hot` / `warm` / `cold`) на основі ключових слів  
- 🔐 JWT auth + rate limiting (10 запитів/хв на IP)  
- 📱 Telegram‑сповіщення з емодзі та структурованим текстом  
- 📊 Authenticated CSV‑експорт (`GET /export/csv`)  
- 🗄 SQLite + SQLAlchemy (auto‑migration)  
- 🐳 Docker‑ready (локальний запуск або Docker)

---

## 🛠 Tech Stack

| Компонент     | Технологія |
|--------------|-----------|
| Framework    | FastAPI 0.109.2 |
| Валідація    | Pydantic v2 |
| База даних   | SQLite + SQLAlchemy 2.0 |
| Auth         | JWT + HS256 (python‑jose) |
| Password     | pbkdf2_sha256 (passlib) |
| Telegram     | Bot API (requests) |
| DevOps       | Docker, docker‑compose |

---

## 🚀 Швидкий старт

### 1. Локальний запуск

```bash
# Встановлення залежностей
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Налаштування .env
cp .env.example .env
# Відкрийте .env і вкажіть:
#   TELEGRAM_BOT_TOKEN
#   TELEGRAM_CHAT_ID
#   APP_SECRET
#   ADMIN_PASS_HASH (або залиште пустим для паролю admin123)

# Запуск серверу
uvicorn main:app --host 0.0.0.0 --port 8000
```

### 2. Docker‑запуск

```bash
docker-compose up -d
curl http://localhost:8000/health
```

---

## 📡 API

### Публічні ендпоінти

| Method | Endpoint     | Auth | Опис |
|--------|--------------|------|------|
| `POST` | `/leads`     | ❌   | Прийом нової заявки (AI‑класифікованої) |
| `GET`  | `/health`    | ❌   | Health‑чек статусу |

### Захищені ендпоінти (JWT‑token)

| Method | Endpoint        | Auth | Опис |
|--------|-----------------|------|------|
| `POST` | `/login`        | ❌   | Отримання JWT (`user: admin`, `pass: admin123`) |
| `GET`  | `/leads`        | ✅   | Список лідів |
| `GET`  | `/export/csv`   | ✅   | Експорт всіх лідів у CSV |

---

## 📌 Приклад payload

```bash
curl -X POST http://localhost:8000/leads \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Іван Петренко",
    "phone": "+380671234567",
    "email": "ivan@example.com",
    "product": "CRM Система",
    "priority": "high",
    "msg": "Потрібно терміново інтегрувати з 1С"
  }'
```

**Відповідь:**

```json
{
  "id": "LD-1779308810501",
  "status": "accepted",
  "category": "hot",
  "ai_summary": "Клієнт Іван Петренко шукає CRM Система. Деталі: Потрібно терміново інтегрувати з 1С"
}
```

---

## 📦 Payload schema

| Поле      | Тип    | Обов’язковий | Опис |
|-----------|--------|--------------|------|
| `name`    | string | так          | Ім’я клієнта (2–100 символів, trim + title) |
| `phone`   | string | так          | Номер у форматі `+380...` або без `+` |
| `email`   | string | так          | RFC‑email |
| `product` | string | так          | Продукт / послуга (3–50 символів) |
| `priority`| string | ні           | `low`, `medium`, `high` (за замовчанням `medium`) |
| `msg`     | string | так          | Текст‑коментар клієнта (5–500 символів) |

---

## 🤖 AI‑логіка

**AI‑класифікатор на основі ключових слів:**

- 🔥 **Hot**: виявлення слів `терміново`, `купити`, `бюджет`, `заказати`, `ціна`, `high`, `urg` в `product` або `msg`.  
- ❄️ **Cold**: `потім`, `дізнатись`, `цікавить`, `low`, `майбут`.  
- 🌤 **Warm**: усі інші випадки.  

`ai_summary` генерується як відформатований однорядковий текст на основі `name`, `product` та частини `msg`.

**Архітектурна перевага:**  
AI‑компонент **ізольований у функції `ai_process_lead`** — його можна замінити на OpenAI / Anthropic‑виклик, не змінюючи решту архітектури.

---

## 🔐 Безпека

- Zero secrets у коді: всі токени через `os.getenv()` з `.env`.  
- JWT‑авторизація за `HS256` (24‑годинний TTL).  
- Паролі хешуються через `pbkdf2_sha256`.  
- Rate limiting: 10 запитів/хв з одного IP (in‑memory sliding window).  
- CORS: `allow_origins=["*"]` для MVP.

---

## 🧠 Архітектурний підхід

- Stateless сервер: немає сесій‑залежності, легко масштабувати.  
- Чітке розділення шарів: HTTP → Domain → AI → Persistence.  
- AI‑компонент ізольовано → заміна на OpenAI займе ~5 рядків коду.  
- Good‑enough‑now: вистачає для MVP‑демонстрації, але просто розширюється до продакшн‑рівня.

---

## 📦 Структура проєкту

- `main.py` — основний FastAPI‑додаток, ендпоінти, JWT, rate limit, Telegram‑сповіщення  
- `requirements.txt` — залежності  
- `Dockerfile` — Docker‑імідж для FastAPI  
- `.env.example` / `.env` — змінні оточення  
- `.gitignore` — виключення `__pycache__`, `*.pyc`, `.venv`, `.env`, `*.db`, `*.csv`  
- `README.md` — цей файл

---

## 📝 Опис логіки рішення

1. Клієнт надсилає заявку з лендингу як `POST /leads` з JSON‑об’єктом.  
2. FastAPI приймає запит і відразу валідує/нормалізує його через `LeadIn` (Pydantic):  
   - перевірка `phone` regex, `email` RFC, `name` trim + title.  
3. `ai_process_lead` аналізує `product` + `msg` за ключовими словами, повертає `category` та `ai_summary`.  
4. Об’єкт `LeadDB` зберігається в SQLite‑таблиці `leads`.  
5. `notify_tg` формує структуроване повідомлення з емодзі та відправляє через Telegram Bot API.  
6. За потреби адмін може:  
   - отримати JWT через `/login`,  
   - переглянути ліди через `/leads`,  
   - експортувати CSV через `/export/csv`.

---


---

## ⚠️ Відомі обмеження MVP

- Rate limiter in-memory (не масштабується на кілька інстансів)
- SQLite (не для високого навантаження)
- AI-класифікатор на ключових словах (не LLM)
- CORS `allow_origins=["*"]` (для локального тесту)

*Усі обмеження легко усуваються при масштабуванні.*


## 📄 Відповідність тестовому завданню

Цей проєкт:

- приймає заявку (`POST /leads`),  
- обробляє та нормалізує JSON,  
- робить AI‑summary та класифікацію (`hot`/`warm`/`cold`),  
- зберігає результат у SQLite,  
- відправляє сповіщення в Telegram,  
- має JWT/auth, rate limit, CSV‑експорт,  
- передає чіткий підхід до MVP‑архітектури.

**Тобто відповідає усім пунктам тестового завдання.**
