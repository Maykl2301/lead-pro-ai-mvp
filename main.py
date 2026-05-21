import os, time, csv, io, logging, requests
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict
from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.security import HTTPBearer
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from passlib.context import CryptContext
from jose import jwt, JWTError
BASE = Path(__file__).parent.resolve()
SECRET = os.getenv("APP_SECRET", "prod")
TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID")
ADMIN_HASH = os.getenv("ADMIN_PASS_HASH", "")

# Validate Telegram config on startup
if TG_CHAT and not str(TG_CHAT).isdigit():
    log.warning(f"⚠️ Invalid TELEGRAM_CHAT_ID: {TG_CHAT} (must be numeric)")

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("lead-pro")
engine = create_engine("sqlite:///{}/leads.db".format(BASE), connect_args={"check_same_thread": False, "timeout": 10})
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()
class LeadDB(Base):
    __tablename__ = "leads"
    id = Column(Integer, primary_key=True)
    lid = Column(String, unique=True)
    created = Column(DateTime, default=datetime.utcnow)
    name = Column(String)
    phone = Column(String)
    email = Column(String)
    product = Column(String)
    priority = Column(String, default="medium")
    msg = Column(Text)
    category = Column(String, default="warm")
    ai_summary = Column(Text)
Base.metadata.create_all(bind=engine)
pwd_ctx = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")
security = HTTPBearer()
rate_limits = defaultdict(list)
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
def verify_token(creds=Depends(security)):
    if not creds: raise HTTPException(401, "Missing token")
    try: return jwt.decode(creds.credentials, SECRET, algorithms=["HS256"])
    except JWTError: raise HTTPException(401, "Invalid token")
def make_token():
    return jwt.encode({"sub": "admin", "exp": datetime.utcnow() + timedelta(hours=24)}, SECRET, algorithm="HS256")
def check_rate_limit(req):
    ip = req.client.host
    now = time.time()
    rate_limits[ip] = [t for t in rate_limits[ip] if now - t < 60]
    if len(rate_limits[ip]) >= 10: raise HTTPException(429, "Limit")
    rate_limits[ip].append(now)
def ai_process(lead):
    txt = (lead.product + " " + lead.msg).lower()
    hot = ["терміново", "купити", "бюджет", "high"]
    cold = ["потім", "дізнатись", "low"]
    cat = "hot" if any(k in txt for k in hot) else ("cold" if any(k in txt for k in cold) else "warm")
    summ = "Клієнт " + lead.name + " шукає " + lead.product + ". Деталі: " + lead.msg[:40] + "..."
    return cat, summ
app = FastAPI(title="Lead AI MVP")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"])
class LeadIn(BaseModel):
    name: str = Field(..., min_length=2)
    phone: str = Field(..., pattern=r"^\+?[0-9\s\-\(\)]{10,20}$")
    email: EmailStr
    product: str = Field(..., min_length=3)
    priority: str = Field("medium")
    msg: str = Field(..., min_length=5)
    @field_validator("name")
    @classmethod
    def fmt(cls, v): return v.strip().title()
class LoginIn(BaseModel):
    user: str
    password: str
def notify(lid, l, cat, summ):
    if not TG_TOKEN or not TG_CHAT: return
    # Hardcoded Unicode escapes to bypass terminal encoding issues
    icons = {"hot": "\U0001F525", "warm": "\U0001F324", "cold": "\U0001F340"}
    icon = icons.get(cat, "\u26AA")
    txt = f"{icon} #{lid} [{cat.upper()}]\n\U0001F464 {l.name}\n\U0001F4E6 {l.product}\n\U0001F4DD {summ}\n\U0001F4DE {l.phone}"
    try:
        r = requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage", json={"chat_id": TG_CHAT, "text": txt}, timeout=5)
        if r.status_code == 200:
            log.info(f"✅ TG sent: {lid}")
    except Exception as e:
        log.error(f"TG error: {e}")


@app.post("/login")
def login(d: LoginIn):
    if d.user != "admin": raise HTTPException(401, "Bad user")
    if not ADMIN_HASH and d.password != "admin123": raise HTTPException(401, "Bad pass")
    if ADMIN_HASH and not pwd_ctx.verify(d.password, ADMIN_HASH): raise HTTPException(401, "Bad pass")
    return {"token": make_token(), "type": "bearer"}
@app.post("/leads", status_code=201)
def create(l: LeadIn, db: Session = Depends(get_db)):
    lid = "LD-" + str(int(time.time()*1000))
    cat, summ = ai_process(l)
    entry = LeadDB(lid=lid, name=l.name, phone=l.phone, email=l.email, product=l.product, priority=l.priority, msg=l.msg, category=cat, ai_summary=summ)
    db.add(entry); db.commit()
    notify(lid, l, cat, summ)
    return {"id": lid, "status": "ok", "category": cat, "ai_summary": summ}
@app.get("/leads")
def list_leads(db: Session = Depends(get_db)):
    items = db.query(LeadDB).order_by(LeadDB.created.desc()).limit(20).all()
    return [{"id": i.lid, "name": i.name, "product": i.product, "category": i.category} for i in items]
@app.get("/export/csv")
def export(db: Session = Depends(get_db)):
    items = db.query(LeadDB).all()
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["ID","Name","Product","Category"])
    for i in items: w.writerow([i.lid, i.name, i.product, i.category])
    out.seek(0)
    return StreamingResponse(iter([out.read()]), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=leads.csv"})
@app.get("/health")
def health(): return {"status": "ok", "v": "3.1.0"}
