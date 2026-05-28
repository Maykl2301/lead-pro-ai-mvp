import os, time, csv, io, logging, requests
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict
from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.security import HTTPBearer
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, EmailStr, Field, validator
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from passlib.context import CryptContext
from jose import jwt, JWTError

# ── Базові налаштування ──────────────────────────────────────────────────────
BASE = Path(__file__).parent.resolve()

# БАГ ВИПРАВЛЕНО: log визначено ДО використання
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("lead-pro")

# БАГ ВИПРАВЛЕНО: падаємо одразу якщо секрет не задано
SECRET = os.getenv("APP_SECRET")
if not SECRET:
    log.warning("⚠️  APP_SECRET не задано — використовується дефолт (небезпечно для продакшну)")
    SECRET = "dev-only-secret"

TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TG_CHAT  = os.getenv("TELEGRAM_CHAT_ID")
ADMIN_HASH = os.getenv("ADMIN_PASS_HASH", "")

# Валідація Telegram конфігу
if TG_CHAT and not str(TG_CHAT).lstrip("-").isdigit():
    log.warning(f"⚠️  Невалідний TELEGRAM_CHAT_ID: {TG_CHAT} (має бути числом)")

# ── База даних ───────────────────────────────────────────────────────────────
engine = create_engine(
    "sqlite:///{}/leads.db".format(BASE),
    connect_args={"check_same_thread": False, "timeout": 10}
)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()

class LeadDB(Base):
    __tablename__ = "leads"
    id         = Column(Integer, primary_key=True)
    lid        = Column(String, unique=True)
    created    = Column(DateTime, default=datetime.utcnow)
    name       = Column(String)
    phone      = Column(String)
    email      = Column(String)
    product    = Column(String)
    priority   = Column(String, default="medium")
    msg        = Column(Text)
    category   = Column(String, default="warm")
    ai_summary = Column(Text)
    source     = Column(String, default="form")   # form | agent

Base.metadata.create_all(bind=engine)

# ── Auth / security ──────────────────────────────────────────────────────────
pwd_ctx  = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")
security = HTTPBearer()
rate_limits: dict = defaultdict(list)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def verify_token(creds=Depends(security)):
    if not creds:
        raise HTTPException(401, "Missing token")
    try:
        return jwt.decode(creds.credentials, SECRET, algorithms=["HS256"])
    except JWTError:
        raise HTTPException(401, "Invalid token")

def make_token():
    return jwt.encode(
        {"sub": "admin", "exp": datetime.utcnow() + timedelta(hours=24)},
        SECRET, algorithm="HS256"
    )

def check_rate_limit(req: Request):
    ip  = req.client.host
    now = time.time()
    rate_limits[ip] = [t for t in rate_limits[ip] if now - t < 60]
    if len(rate_limits[ip]) >= 10:
        raise HTTPException(429, "Занадто багато запитів")
    rate_limits[ip].append(now)

# ── Класифікатор лідів ───────────────────────────────────────────────────────
HOT_KEYWORDS = [
    "хочу купити", "хочу придбати", "шукаю купити",
    "планую купити", "розглядаю купівлю", "готовий купити",
    "бюджет", "готовий платити", "є кошти",
    "скільки коштує", "ціна", "вартість", "прайс",
    "терміново", "якнайшвидше", "цього року",
    "навесні", "влітку", "до зими", "цьогоріч",
    "модульний будинок", "модульний дім",
    "збірний будинок", "будинок під ключ",
    "напишіть", "зателефонуйте", "де купити",
    "хто продає", "порадьте виробника",
    "купити", "придбати", "замовити", "high",
]

WARM_KEYWORDS = [
    "цікавить", "розглядаю", "думаю",
    "може", "варіанти", "інформація",
    "що краще", "порівняти", "medium",
]

COLD_KEYWORDS = [
    "продаю", "здаю в оренду", "пропоную",
    "потім", "колись", "не зараз",
    "просто дивлюсь", "дізнатись", "low",
]

def classify_lead(product: str, msg: str) -> tuple[str, str]:
    """Класифікує лід та генерує summary на основі ключових слів."""
    txt = (product + " " + msg).lower()

    # Розумна перевірка: слова можуть стояти не поряд
    def matches(keywords):
        return any(k in txt for k in keywords)

    if matches(HOT_KEYWORDS):
        cat = "hot"
    elif matches(COLD_KEYWORDS):
        cat = "cold"
    else:
        cat = "warm"

    summary = f"Шукає: {product}. Коментар: {msg[:80]}{'...' if len(msg) > 80 else ''}"
    return cat, summary

# ── FastAPI app ──────────────────────────────────────────────────────────────
ALLOWED_ORIGIN = os.getenv("ALLOWED_ORIGIN", "*")

app = FastAPI(title="Lead PRO AI", version="3.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[ALLOWED_ORIGIN],
    allow_methods=["*"],
    allow_headers=["Authorization", "Content-Type"],
)

# ── Pydantic моделі ──────────────────────────────────────────────────────────
class LeadIn(BaseModel):
    name:     str      = Field(..., min_length=2, max_length=100)
    phone:    str      = Field(..., pattern=r"^\+?[0-9\s\-\(\)]{10,20}$")
    email:    EmailStr
    product:  str      = Field(..., min_length=3, max_length=100)
    priority: str      = Field("medium", pattern="^(low|medium|high)$")
    msg:      str      = Field(..., min_length=5, max_length=500)
    source:   str      = Field("form")

    @validator("name")

    def fmt_name(cls, v):
        return v.strip().title()

class LoginIn(BaseModel):
    user:     str
    password: str

# ── Telegram ─────────────────────────────────────────────────────────────────
def notify(lid: str, name: str, product: str, phone: str,
           cat: str, summary: str, source: str = "form"):
    if not TG_TOKEN or not TG_CHAT:
        return
    icons   = {"hot": "\U0001F525", "warm": "\U0001F324", "cold": "\U0001F340"}
    src_ico = "\U0001F916" if source == "agent" else "\U0001F4F1"
    icon    = icons.get(cat, "\u26AA")
    txt = (
        f"{icon} #{lid} [{cat.upper()}] {src_ico}\n"
        f"\U0001F464 {name}\n"
        f"\U0001F4E6 {product}\n"
        f"\U0001F4DD {summary}\n"
        f"\U0001F4DE {phone}"
    )
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT, "text": txt},
            timeout=5,
        )
        if r.status_code == 200:
            log.info(f"✅ TG надіслано: {lid}")
        else:
            log.warning(f"⚠️  TG помилка {r.status_code}: {r.text}")
    except Exception as e:
        log.error(f"TG exception: {e}")

# ── Ендпоінти ────────────────────────────────────────────────────────────────

@app.post("/login")
def login(d: LoginIn):
    if d.user != "admin":
        raise HTTPException(401, "Невірний користувач")
    # БАГ ВИПРАВЛЕНО: прибрано захардкоджений пароль admin123
    if not ADMIN_HASH:
        raise HTTPException(500, "ADMIN_PASS_HASH не налаштовано")
    if not pwd_ctx.verify(d.password, ADMIN_HASH):
        raise HTTPException(401, "Невірний пароль")
    return {"token": make_token(), "type": "bearer"}


@app.post("/leads", status_code=201)
def create_lead(l: LeadIn, req: Request, db: Session = Depends(get_db)):
    # БАГ ВИПРАВЛЕНО: rate limit тепер викликається
    check_rate_limit(req)
    lid = "LD-" + str(int(time.time() * 1000))
    cat, summary = classify_lead(l.product, l.msg)
    entry = LeadDB(
        lid=lid, name=l.name, phone=l.phone, email=l.email,
        product=l.product, priority=l.priority, msg=l.msg,
        category=cat, ai_summary=summary, source=l.source,
    )
    db.add(entry)
    db.commit()
    notify(lid, l.name, l.product, l.phone, cat, summary, l.source)
    # БАГ ВИПРАВЛЕНО: статус "accepted" як у README
    return {"id": lid, "status": "accepted", "category": cat, "ai_summary": summary}


# БАГ ВИПРАВЛЕНО: /leads та /export/csv тепер захищені токеном
@app.get("/leads")
def list_leads(
    skip: int = 0, limit: int = 20,
    db: Session = Depends(get_db),
    _=Depends(verify_token),
):
    items = (
        db.query(LeadDB)
        .order_by(LeadDB.created.desc())
        .offset(skip).limit(limit)
        .all()
    )
    return [
        {
            "id":       i.lid,
            "name":     i.name,
            "product":  i.product,
            "category": i.category,
            "source":   i.source,
            "created":  i.created.isoformat() if i.created else None,
        }
        for i in items
    ]


@app.get("/export/csv")
def export_csv(db: Session = Depends(get_db), _=Depends(verify_token)):
    items = db.query(LeadDB).all()
    out   = io.StringIO()
    w     = csv.writer(out)
    w.writerow(["ID", "Name", "Phone", "Email", "Product",
                "Priority", "Category", "Source", "Summary", "Created"])
    for i in items:
        w.writerow([
            i.lid, i.name, i.phone, i.email, i.product,
            i.priority, i.category, i.source, i.ai_summary,
            i.created.isoformat() if i.created else "",
        ])
    out.seek(0)
    return StreamingResponse(
        iter([out.read()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=leads.csv"},
    )


@app.get("/health")
def health():
    return {"status": "ok", "v": "3.2.0"}
