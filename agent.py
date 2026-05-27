"""
agent.py — Агент-шукач лідів по модульних будинках
Запускається через GitHub Actions кожні 6 годин.
Шукає у Google + OLX RSS, відправляє знайдені ліди у /leads.
"""

import os, time, logging, hashlib, json, re
import feedparser
import requests
from datetime import datetime
from pathlib import Path
from googlesearch import search as google_search

# ── Налаштування ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("lead-agent")

API_URL    = os.getenv("API_URL", "http://localhost:8000")   # URL твого Render
SEEN_FILE  = Path(__file__).parent / "seen_leads.json"       # кеш вже знайдених

# ── Ключові слова для пошуку ──────────────────────────────────────────────────
GOOGLE_QUERIES = [
    "хочу купити модульний будинок Україна",
    "шукаю модульний будинок ціна",
    "купити збірний будинок під ключ",
    "модульний дім замовити",
    "де купити модульний будинок Україна 2024",
    "купити каркасний будинок недорого",
    "будинок під ключ бюджет",
]

OLX_RSS_FEEDS = [
    "https://www.olx.ua/uk/nedvizhimost/doma/q-модульний-будинок/?search%5Border%5D=created_at:desc&view=list&format=rss",
    "https://www.olx.ua/uk/nedvizhimost/doma/q-збірний-будинок/?search%5Border%5D=created_at:desc&view=list&format=rss",
    "https://www.olx.ua/uk/nedvizhimost/doma/q-будинок-під-ключ/?search%5Border%5D=created_at:desc&view=list&format=rss",
]

# Слова що вказують що це ПОКУПЕЦЬ (не продавець)
BUYER_SIGNALS = [
    "хочу купити", "хочу придбати", "шукаю", "потрібен",
    "порадьте", "де купити", "хто продає", "планую купити",
    "розглядаю", "цікавить", "бюджет є", "готовий",
    "скільки коштує", "ціна питання", "де замовити",
]

# Слова що вказують що це ПРОДАВЕЦЬ — ігноруємо
SELLER_SIGNALS = [
    "продаємо", "пропонуємо", "наша компанія", "виготовляємо",
    "від виробника", "продаю", "здаємо", "зв'яжіться з нами",
    "наші будинки", "замовляйте у нас",
]

# ── Кеш вже оброблених ───────────────────────────────────────────────────────
def load_seen() -> set:
    if SEEN_FILE.exists():
        try:
            return set(json.loads(SEEN_FILE.read_text()))
        except Exception:
            return set()
    return set()

def save_seen(seen: set):
    SEEN_FILE.write_text(json.dumps(list(seen)))

def make_hash(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()

# ── Класифікатор ─────────────────────────────────────────────────────────────
def is_buyer(text: str) -> bool:
    """Повертає True якщо текст схожий на покупця, а не продавця."""
    txt = text.lower()
    has_buyer  = any(s in txt for s in BUYER_SIGNALS)
    has_seller = any(s in txt for s in SELLER_SIGNALS)
    return has_buyer and not has_seller

def extract_phone(text: str) -> str:
    """Витягує телефон з тексту або повертає заглушку."""
    pattern = r"(\+?38)?[\s\-]?\(?0\d{2}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}"
    match = re.search(pattern, text)
    if match:
        phone = re.sub(r"[\s\-\(\)]", "", match.group())
        if not phone.startswith("+"):
            phone = "+38" + phone.lstrip("38")
        return phone
    return "+380000000000"  # заглушка якщо телефон не знайдено

# ── Відправка ліда ────────────────────────────────────────────────────────────
def send_lead(name: str, phone: str, email: str,
              product: str, msg: str, source_url: str = "") -> bool:
    """Відправляє лід у FastAPI через POST /leads."""
    payload = {
        "name":     name,
        "phone":    phone,
        "email":    email,
        "product":  product,
        "priority": "medium",
        "msg":      msg[:500],
        "source":   "agent",
    }
    try:
        r = requests.post(f"{API_URL}/leads", json=payload, timeout=10)
        if r.status_code == 201:
            log.info(f"✅ Лід надіслано: {name} | {product[:40]}")
            return True
        else:
            log.warning(f"⚠️  /leads відповів {r.status_code}: {r.text[:100]}")
            return False
    except Exception as e:
        log.error(f"❌ Помилка відправки: {e}")
        return False

# ── Джерело 1: Google Search ──────────────────────────────────────────────────
def search_google(seen: set) -> int:
    found = 0
    for query in GOOGLE_QUERIES:
        log.info(f"🔍 Google: {query}")
        try:
            results = list(google_search(query, num_results=5, lang="uk"))
            time.sleep(2)  # пауза щоб не блокували
        except Exception as e:
            log.warning(f"Google помилка: {e}")
            continue

        for url in results:
            uid = make_hash(url)
            if uid in seen:
                continue

            try:
                resp = requests.get(url, timeout=8, headers={
                    "User-Agent": "Mozilla/5.0 (compatible; LeadBot/1.0)"
                })
                text = resp.text[:3000]
            except Exception:
                seen.add(uid)
                continue

            if not is_buyer(text):
                seen.add(uid)
                continue

            phone = extract_phone(text)
            msg   = f"Знайдено через Google за запитом: '{query}'. Джерело: {url}"

            ok = send_lead(
                name    = "Знайдений лід (Google)",
                phone   = phone,
                email   = "agent@lead.local",
                product = "Модульний будинок",
                msg     = msg,
                source_url = url,
            )
            if ok:
                found += 1
            seen.add(uid)
            time.sleep(1)

    return found

# ── Джерело 2: OLX RSS ───────────────────────────────────────────────────────
def search_olx_rss(seen: set) -> int:
    found = 0
    for feed_url in OLX_RSS_FEEDS:
        log.info(f"📡 OLX RSS: {feed_url[:60]}...")
        try:
            feed = feedparser.parse(feed_url)
        except Exception as e:
            log.warning(f"RSS помилка: {e}")
            continue

        for entry in feed.entries[:10]:
            title   = getattr(entry, "title", "")
            summary = getattr(entry, "summary", "")
            link    = getattr(entry, "link", "")
            text    = title + " " + summary

            uid = make_hash(link or text)
            if uid in seen:
                continue

            # OLX RSS — це оголошення продавців, але шукаємо "куплю"
            # Якщо в заголовку є слово "куплю" або "шукаю" — це покупець
            if not any(s in text.lower() for s in ["куплю", "шукаю", "потрібен", "хочу купити"]):
                seen.add(uid)
                continue

            phone = extract_phone(text)
            msg   = f"Оголошення OLX: {title}. {summary[:200]}. Посилання: {link}"

            ok = send_lead(
                name    = "Покупець з OLX",
                phone   = phone,
                email   = "agent@lead.local",
                product = "Модульний будинок",
                msg     = msg,
                source_url = link,
            )
            if ok:
                found += 1
            seen.add(uid)

    return found

# ── Головна функція ───────────────────────────────────────────────────────────
def run():
    log.info("🚀 Агент запущено: " + datetime.now().strftime("%Y-%m-%d %H:%M"))
    seen  = load_seen()
    total = 0

    total += search_google(seen)
    total += search_olx_rss(seen)

    save_seen(seen)
    log.info(f"✅ Агент завершив роботу. Знайдено нових лідів: {total}")

if __name__ == "__main__":
    run()
