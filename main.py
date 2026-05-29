import os, time, csv, io, logging, requests
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict
from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.security import HTTPBearer
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, EmailStr
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from passlib.context import CryptContext
from jose import jwt, JWTError

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("lead-pro")

BASE   = Path(__file__).parent.resolve()
SECRET = os.getenv("APP_SECRET", "dev-secret-change-me")
TG_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN")
TG_CHAT   = os.getenv("TELEGRAM_CHAT_ID")
ADMIN_HASH = os.getenv("ADMIN_PASS_HASH", "")

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
    source     = Column(String, default="form")

Base.metadata.create_all(bind=engine)

pwd_ctx  = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer()
rate_limits = defaultdict(list)

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

HOT_KEYWORDS = [
    "хочу купити", "хочу придбати", "шукаю купити",
    "планую купити", "бюджет", "готовий платити",
    "скільки коштує", "ціна", "вартість", "прайс",
    "терміново", "модульний будинок", "модульний дім",
    "збірний будинок", "будинок під ключ",
    "купити", "придбати", "замовити", "high",
]
COLD_KEYWORDS = [
    "продаю", "здаю", "пропоную", "потім",
    "колись", "не зараз", "просто дивлюсь", "low",
]

def classify_lead(product, msg):
    txt = (product + " " + msg).lower()
    if any(k in txt for k in HOT_KEYWORDS):
        cat = "hot"
    elif any(k in txt for k in COLD_KEYWORDS):
        cat = "cold"
    else:
        cat = "warm"
    summary = "Шукає: {}. Коментар: {}{}".format(
        product, msg[:80], "..." if len(msg) > 80 else ""
    )
    return cat, summary

app = FastAPI(title="Lead PRO AI", version="3.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["Authorization", "Content-Type"],
)

class LeadIn(BaseModel):
    name:     str
    phone:    str
    email:    EmailStr
    product:  str
    priority: str = "medium"
    msg:      str
    source:   str = "form"

class LoginIn(BaseModel):
    user:     str
    password: str

def notify(lid, name, product, phone, cat, summary, source="form"):
    if not TG_TOKEN or not TG_CHAT:
        return
    icons   = {"hot": "\U0001F525", "warm": "\U0001F324", "cold": "\U0001F340"}
    src_ico = "\U0001F916" if source == "agent" else "\U0001F4F1"
    icon    = icons.get(cat, "\u26AA")
    txt = "{} #{} [{}] {}\n\U0001F464 {}\n\U0001F4E6 {}\n\U0001F4DD {}\n\U0001F4DE {}".format(
        icon, lid, cat.upper(), src_ico, name, product, summary, phone
    )
    try:
        r = requests.post(
            "https://api.telegram.org/bot{}/sendMessage".format(TG_TOKEN),
            json={"chat_id": TG_CHAT, "text": txt},
            timeout=5,
        )
        if r.status_code == 200:
            log.info("TG надіслано: {}".format(lid))
        else:
            log.warning("TG помилка {}: {}".format(r.status_code, r.text))
    except Exception as e:
        log.error("TG exception: {}".format(e))

@app.post("/login")
def login(d: LoginIn):
    if d.user != "admin":
        raise HTTPException(401, "Невірний користувач")
    if not ADMIN_HASH:
        raise HTTPException(500, "ADMIN_PASS_HASH не налаштовано")
    if not pwd_ctx.verify(d.password, ADMIN_HASH):
        raise HTTPException(401, "Невірний пароль")
    return {"token": make_token(), "type": "bearer"}

@app.post("/leads", status_code=201)
def create_lead(l: LeadIn, req: Request, db: Session = Depends(get_db)):
    check_rate_limit(req)
    l.name = l.name.strip().title()
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
    return {"id": lid, "status": "accepted", "category": cat, "ai_summary": summary}

@app.get("/leads")
def list_leads(skip: int = 0, limit: int = 20,
               db: Session = Depends(get_db), _=Depends(verify_token)):
    items = db.query(LeadDB).order_by(LeadDB.created.desc()).offset(skip).limit(limit).all()
    return [
        {"id": i.lid, "name": i.name, "product": i.product,
         "category": i.category, "source": i.source,
         "created": i.created.isoformat() if i.created else None}
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
        w.writerow([i.lid, i.name, i.phone, i.email, i.product,
                    i.priority, i.category, i.source, i.ai_summary,
                    i.created.isoformat() if i.created else ""])
    out.seek(0)
    return StreamingResponse(
        iter([out.read()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=leads.csv"},
    )

@app.get("/health")
def health():
    return {"status": "ok", "v": "3.2.0"}
