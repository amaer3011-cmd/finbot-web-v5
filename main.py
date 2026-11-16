"""
واجهة ويب لبوت حل الثقافة المالية — v5
FastAPI + WebSocket لمتابعة التقدم live
"""
import asyncio
import copy
import hashlib
import json
import os
import re
import sys
import threading
import time
from collections import deque, defaultdict
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

load_dotenv()

BOT_DIR = Path(os.getenv("BOT_DIR", "./bot")).resolve()
if not ((BOT_DIR / "core.py").exists() or (BOT_DIR / "core" / "__init__.py").exists()):
    raise SystemExit(
        f"❌ مجلد البوت مش موجود أو ناقص: {BOT_DIR}\n"
        f"   انسخ مجلد البوت (فيه core و db.py و answers.txt) باسم bot جنب main.py، أو اظبط BOT_DIR."
    )
sys.path.insert(0, str(BOT_DIR))

import core            # noqa: E402  (من مجلد البوت)
import db as botdb     # noqa: E402

from webprogress import JobProgress   # noqa: E402

try:
    import pw_speed    # noqa: E402  (أدوات تسريع/قياس Playwright)
except Exception:      # pragma: no cover
    pw_speed = None

ANSWERS_FILE    = BOT_DIR / os.getenv("ANSWERS_FILE", "answers.txt")
MAX_JOBS        = int(os.getenv("MAX_JOBS", "2"))
MAX_QUEUE       = int(os.getenv("MAX_QUEUE", "10"))
JOB_TIMEOUT_MIN = int(os.getenv("JOB_TIMEOUT_MIN", "240"))
DB_PATH         = os.getenv("DB_PATH", str(BOT_DIR / "web.db"))   # منفصلة عن bot.db بتاع التليجرام
# دومينات إضافية مسموح لها (مفصولة بفاصلة). فاضية = نفس الدومين بس.
ALLOWED_ORIGINS = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "").split(",") if o.strip()]
# شغّلها 1 على Railway/Nginx عشان نقرأ الـ IP الحقيقي من X-Forwarded-For
TRUST_PROXY     = os.getenv("TRUST_PROXY", "1") == "1"
# PROFILE_SLEEPS=1 يطبع بعد كل حل كام ثانية ضاعت في sleep وفي أنهي سطر (شغّله مع MAX_JOBS=1)
PROFILE_SLEEPS  = os.getenv("PROFILE_SLEEPS", "0") == "1"
# يضيف --disable-dev-shm-usage لأي chromium.launch. مقفول افتراضياً: البوت v4.1 بيمرّره أصلاً.
PW_LAUNCH_FIX   = os.getenv("PW_LAUNCH_FIX", "0") == "1"

KEY_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
SID_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")


# ── Helpers ──────────────────────────────────────────────────────────

def make_uid(key: str) -> int:
    """معرّف ثابت بين كل مرات التشغيل (hash() بتاع بايثون بيتغير كل run)."""
    return int(hashlib.sha256(key.encode("utf-8")).hexdigest()[:12], 16) % (10 ** 9)


_AR = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")

def ar_num(n):
    return f"{n:,}".replace(",", "٬").translate(_AR)

def format_duration(seconds):
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{ar_num(h)}س {ar_num(m)}د"
    if m:
        return f"{ar_num(m)}د {ar_num(s)}ث"
    return f"{ar_num(s)}ث"


def client_ip(conn) -> str:
    if TRUST_PROXY:
        xff = conn.headers.get("x-forwarded-for")
        if xff:
            return xff.split(",")[-1].strip()   # آخر عنصر = اللي أضافه البروكسي
    return conn.client.host if conn.client else "unknown"


def origin_ok(conn) -> bool:
    """يمنع صفحة من دومين تاني إنها تفتح WebSocket على السيرفر ده."""
    origin = conn.headers.get("origin")
    if not origin:                       # مش متصفح
        return True
    if origin in ALLOWED_ORIGINS:
        return True
    netloc = urlparse(origin).netloc
    return netloc in (conn.headers.get("host", ""), conn.headers.get("x-forwarded-host", ""))


class RateLimiter:
    """Sliding window بسيط في الذاكرة."""
    def __init__(self, limit: int, window: float):
        self.limit, self.window = limit, window
        self.hits = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        q = self.hits[key]
        while q and now - q[0] > self.window:
            q.popleft()
        if len(q) >= self.limit:
            return False
        q.append(now)
        if len(self.hits) > 5000:        # تنظيف دوري
            for k in [k for k, v in self.hits.items() if not v or now - v[-1] > self.window]:
                self.hits.pop(k, None)
        return True


ws_limiter      = RateLimiter(int(os.getenv("WS_RATE_LIMIT", "6")), 60)   # محاولات حل/دقيقة لكل IP
salawat_limiter = RateLimiter(200, 60)
api_limiter     = RateLimiter(60, 60)


def check_rate(limiter: RateLimiter, request: Request):
    if not limiter.allow(client_ip(request)):
        raise HTTPException(429, "طلبات كتير، استنى شوية")


def check_key(key: str):
    if not KEY_RE.match(key):
        raise HTTPException(400, "bad key")


def fetch_history(uid: int, limit: int = 10):
    """يفضّل دالة get_jobs في db.py (شوف README). لو مش موجودة يرجع للاستعلام المباشر."""
    fn = getattr(botdb, "get_jobs", None)
    if callable(fn):
        return fn(uid, limit)
    with getattr(botdb, "_lock", threading.Lock()):     # نفس lock الـ db.py
        return botdb._conn.execute(
            "SELECT started, finished, status, questions, activities FROM jobs "
            "WHERE tg_id=? ORDER BY started DESC LIMIT ?", (uid, limit)
        ).fetchall()


# ── App ──────────────────────────────────────────────────────────────

# كام ثانية نفضل محتفظين بنتيجة الحل بعد ما يخلص (عشان اللي قفل الصفحة يرجع يلاقيها)
RESULT_TTL      = int(os.getenv("RESULT_TTL_MIN", "30")) * 60
HEARTBEAT_SEC   = 5

_state = {
    "sem": None,
    "jobs": {},             # session_id -> Job (شغّالة + اللي خلصت حديثاً)
    "by_user": {},          # ukey -> Job الشغّالة حالياً (مفيش جلستين لنفس الحساب)
    "queue": [],            # session_ids المنتظرة بالترتيب
    "entries": [],
    "index": "",
}


class Job:
    """حالة حل واحد. بتعيش على السيرفر مستقلة عن اتصال المتصفح، فتقدر ترجع لها بعد قفل الصفحة."""

    def __init__(self, sid: str, ukey: str, uid: int):
        self.sid, self.ukey, self.uid = sid, ukey, uid
        self.cancel = threading.Event()
        self.timed_out = threading.Event()
        self.progress = JobProgress(core.normalize_text, format_duration)
        self.header = "⚙️ جاري الحل…"
        self.show_stop = True
        self.snapshot = None            # آخر snapshot (يتحدّث كل ثانية)
        self._snap_key = None
        self.version = 0                # يزيد لما يتغير شيء يستاهل الإرسال
        self.result_msg = None          # رسالة done النهائية
        self.waiters = set()            # asyncio.Event لكل متصفح متابع
        self.task = None

    def _wake(self):
        for ev in list(self.waiters):
            ev.set()

    def refresh(self, header=None, show_stop=None):
        """يحدّث الـ snapshot، ويصحّي المتابعين فقط لو تغيّر شيء غير الوقت."""
        if header is not None:
            self.header = header
        if show_stop is not None:
            self.show_stop = show_stop
        snap = self.progress.snapshot()
        snap.update({"type": "progress", "header": self.header, "show_stop": self.show_stop})
        key = json.dumps({k: v for k, v in snap.items() if k not in ("elapsed", "elapsed_s")},
                         sort_keys=True, ensure_ascii=False)
        self.snapshot = snap
        if key != self._snap_key:
            self._snap_key = key
            self.version += 1
            self._wake()

    def finish(self, msg: dict):
        self.result_msg = msg
        self.version += 1
        self._wake()


def _forget_job(sid: str):
    _state["jobs"].pop(sid, None)


async def run_session(job: Job, username: str, password: str):
    """بيشغّل الحل كله (الطابور ← الدخول ← الحل) كـ task مستقل عن الـ WebSocket."""
    sem = _state["sem"]
    t0 = time.monotonic()
    progress = job.progress
    acquired = in_queue = False
    ticker = timer = None
    result = {"status": "error", "error": "unknown"}
    started_ts = int(time.time())

    def mask(s):
        s = str(s or "")
        for secret in (password, username):
            if secret:
                s = s.replace(secret, "***")
        return s[:300]

    def hook(kind, text):
        # بيتنادى من thread البوت: بنحدّث الحالة بس، والـ ticker هو اللي بيصحّي المتابعين
        try:
            if kind == "debug":
                t = str(text or "")[:600]
                if t.startswith("⏱️"):         # جدول زمن الخطوات في آخر الحل → لوج السيرفر
                    print(t, flush=True)
                return
            if kind == "diagnostic":
                return
            progress.handle(kind, str(text or "")[:300])
        except Exception:
            pass

    async def _tick():
        while True:
            await asyncio.sleep(1.0)
            job.refresh()

    try:
        # ── الانتظار في الطابور (ترتيب حقيقي + إمكانية الإلغاء) ──
        if sem.locked():
            _state["queue"].append(job.sid)
            in_queue = True
        try:
            while True:
                try:
                    await asyncio.wait_for(sem.acquire(), timeout=3)
                    acquired = True
                    break
                except asyncio.TimeoutError:
                    if job.cancel.is_set():
                        break
                    pos = _state["queue"].index(job.sid) + 1 if in_queue else 1
                    progress.handle("phase", f"في الطابور — رقمك {ar_num(pos)}")
                    job.refresh(f"⏳ في الانتظار — رقمك {ar_num(pos)}")
        finally:
            if in_queue:
                in_queue = False
                if job.sid in _state["queue"]:
                    _state["queue"].remove(job.sid)

        if not acquired:
            job.finish({"type": "done", "status": "stopped",
                        "summary": {"questions": 0, "activities": 0,
                                    "elapsed": format_duration(time.monotonic() - t0), "last": "—"},
                        "error": "", "test": "", "unmatched": []})
            return

        # ── التشغيل ──
        progress.mark_started()                 # السرعة والتقدير من لحظة البدء الفعلية
        progress.handle("phase", "بيسجّل الدخول للمنصة")
        job.refresh("⚙️ جاري الحل…")
        ticker = asyncio.create_task(_tick())

        def _on_timeout():
            job.timed_out.set()
            job.cancel.set()
        timer = threading.Timer(JOB_TIMEOUT_MIN * 60, _on_timeout)
        timer.daemon = True
        timer.start()

        if PROFILE_SLEEPS and pw_speed:
            pw_speed.reset()
        try:
            result = await asyncio.to_thread(
                core.run_job, username, password, _state["entries"],
                hook, job.cancel, True, False,
            )
        except Exception as ex:
            result = {"status": "error", "error": str(ex)}
        finally:
            sem.release()
            acquired = False
        if PROFILE_SLEEPS and pw_speed:
            print(pw_speed.report(), flush=True)

        status = result.get("status", "error")
        if job.timed_out.is_set():
            status = "timeout"
        err_txt = mask(result.get("error"))
        q, a = progress.questions, progress.activities
        elapsed = progress.elapsed()
        summary = {
            "questions":  q,
            "activities": a,
            "elapsed":    format_duration(elapsed),
            "last":       mask(result.get("last")) or "—",
            "sec_per_q":  round(elapsed / q, 1) if q else None,
        }
        unmatched = [mask(l[1:].strip()) for l in str(result.get("error") or "").splitlines()
                     if l.startswith("•")][:20]
        try:
            botdb.log_job(job.uid, started_ts, status, q, a, err_txt)
        except Exception:
            pass
        job.refresh()
        job.finish({"type": "done", "status": status, "summary": summary,
                    "error": err_txt, "test": mask(result.get("test")), "unmatched": unmatched})

    except Exception as ex:
        job.finish({"type": "done", "status": "error",
                    "summary": {"questions": progress.questions, "activities": progress.activities,
                                "elapsed": format_duration(progress.elapsed()), "last": "—"},
                    "error": mask(ex), "test": "", "unmatched": []})
    finally:
        password = None
        if ticker:
            ticker.cancel()
        if timer:
            timer.cancel()
        if acquired:
            sem.release()
        if in_queue and job.sid in _state["queue"]:
            _state["queue"].remove(job.sid)
        if _state["by_user"].get(job.ukey) is job:
            _state["by_user"].pop(job.ukey, None)
        asyncio.get_running_loop().call_later(RESULT_TTL, _forget_job, job.sid)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not ANSWERS_FILE.exists():
        raise RuntimeError(f"ملف الإجابات مش موجود: {ANSWERS_FILE}")
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    botdb.init(DB_PATH)
    if PW_LAUNCH_FIX and pw_speed:
        pw_speed.patch_launch_args()
    _state["sem"] = asyncio.Semaphore(MAX_JOBS)
    _state["entries"] = core.parse_answer_file(ANSWERS_FILE)
    _state["index"] = (Path(__file__).parent / "static" / "index.html").read_text(encoding="utf-8")
    if PROFILE_SLEEPS and pw_speed:
        pw_speed.profile_sleeps(core)
    yield
    for job in list(_state["jobs"].values()):       # إيقاف نظيف عند إعادة تشغيل السيرفر
        job.cancel.set()


app = FastAPI(title="حل الثقافة المالية", lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=800)
if ALLOWED_ORIGINS:
    app.add_middleware(CORSMiddleware, allow_origins=ALLOWED_ORIGINS,
                       allow_methods=["GET", "POST"], allow_headers=["Content-Type"])


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    if request.url.path.startswith(("/api", "/cancel")):
        resp.headers["Cache-Control"] = "no-store"
    return resp


# ── API endpoints ────────────────────────────────────────────────────

class UsernameBody(BaseModel):
    username: str = Field("", max_length=100)


@app.get("/api/salawat")
def get_salawat():
    return {"count": botdb.get_salawat()}


@app.post("/api/salawat")
def add_salawat(request: Request):
    check_rate(salawat_limiter, request)
    return {"count": botdb.add_salawat()}


@app.get("/api/username/{key}")
def get_username(key: str, request: Request):
    check_key(key)
    check_rate(api_limiter, request)
    return {"username": botdb.get_username(make_uid(key)) or ""}


@app.post("/api/username/{key}")
def save_username(key: str, body: UsernameBody, request: Request):
    check_key(key)
    check_rate(api_limiter, request)
    uid = make_uid(key)
    username = body.username.strip()
    botdb.touch_user(uid, key)
    if username:
        botdb.save_username(uid, username)
    return {"ok": True}


@app.get("/api/history/{key}")
def get_history(key: str, request: Request):
    check_key(key)
    check_rate(api_limiter, request)
    now = int(time.time())
    result = []
    for started, finished, status, q, a in fetch_history(make_uid(key), 10):
        result.append({
            "started":    started,
            "elapsed":    format_duration((finished or now) - (started or now)),
            "status":     status,
            "questions":  q,
            "activities": a,
        })
    return {"history": result}


@app.get("/api/job/{session_id}")
def job_status(session_id: str, request: Request):
    """حالة الحل بدون WebSocket (احتياطي لو الشبكة بتقطع WebSocket)."""
    if not SID_RE.match(session_id):
        raise HTTPException(400, "bad session")
    check_rate(api_limiter, request)
    job = _state["jobs"].get(session_id)
    if not job:
        return {"state": "gone"}
    if job.result_msg is not None:
        return {"state": "done", "result": job.result_msg}
    return {"state": "running", "progress": job.snapshot}


@app.post("/cancel/{session_id}")
def cancel_job(session_id: str, request: Request):
    # الـ session_id عشوائي 128-bit فهو نفسه السر؛ ومن POST عشان مايتفعلش بالغلط من لينك
    if not SID_RE.match(session_id):
        raise HTTPException(400, "bad session")
    check_rate(api_limiter, request)
    job = _state["jobs"].get(session_id)
    if job and job.result_msg is None:
        job.cancel.set()
        return {"ok": True}
    return {"ok": False}


@app.get("/health")
def health():
    running = len(_state["by_user"]) - len(_state["queue"])
    return {"ok": True, "running": max(0, running), "queued": len(_state["queue"]),
            "max_jobs": MAX_JOBS}


# ── WebSocket ────────────────────────────────────────────────────────

async def _stream(ws: WebSocket, job: Job, send):
    """يبعت التقدم للمتصفح لحد ما الحل يخلص. لو المتصفح اتفصل نخرج والحل يكمّل على السيرفر."""
    await send({"type": "started", "session_id": job.sid})
    waiter = asyncio.Event()
    job.waiters.add(waiter)
    recv = asyncio.create_task(ws.receive_text())      # بس عشان نكتشف الفصل فوراً
    seen = -1
    try:
        while True:
            if job.result_msg is not None:
                if job.snapshot:
                    await send(job.snapshot)
                await send(job.result_msg)
                return
            if job.snapshot and (job.version != seen):
                seen = job.version
                if not await send(job.snapshot):
                    return
            waiter.clear()
            wait_t = asyncio.create_task(waiter.wait())
            done, _ = await asyncio.wait({recv, wait_t}, timeout=HEARTBEAT_SEC,
                                         return_when=asyncio.FIRST_COMPLETED)
            wait_t.cancel()
            if recv in done:
                if recv.exception() is not None:        # المتصفح قفل
                    return
                recv = asyncio.create_task(ws.receive_text())
            elif not done and job.snapshot and job.result_msg is None:
                if not await send(job.snapshot):         # heartbeat (الوقت بيتحدّث + كشف الفصل)
                    return
    finally:
        job.waiters.discard(waiter)
        recv.cancel()


@app.websocket("/ws/{session_id}")
async def ws_endpoint(ws: WebSocket, session_id: str):
    if not SID_RE.match(session_id) or not origin_ok(ws):
        await ws.close(code=1008)
        return
    await ws.accept()

    async def send(msg: dict) -> bool:
        try:
            await ws.send_text(json.dumps(msg, ensure_ascii=False))
            return True
        except Exception:
            return False     # العميل قفل الصفحة — الحل بيكمل على السيرفر عادي

    password = ""
    try:
        try:
            raw = await asyncio.wait_for(ws.receive_text(), timeout=20)
        except (asyncio.TimeoutError, Exception):
            return
        if len(raw) > 4096:
            await send({"type": "error", "msg": "طلب غير صالح"})
            return
        try:
            data = json.loads(raw)
        except ValueError:
            data = None
        if not isinstance(data, dict):
            await send({"type": "error", "msg": "طلب غير صالح"})
            return

        job = _state["jobs"].get(session_id)

        # ── رجوع لحل شغّال أو خالص (بعد قفل الصفحة/انقطاع الشبكة) ──
        if data.get("resume"):
            if not job:
                await send({"type": "gone"})
                return
            await _stream(ws, job, send)
            return

        # ── حل جديد ──
        if job:                                   # نفس الـ session_id مرتين = متابعة
            await _stream(ws, job, send)
            return
        if not ws_limiter.allow(client_ip(ws)):
            await send({"type": "error", "msg": "⚠️ محاولات كتير، استنى دقيقة وجرّب تاني."})
            return

        username   = str(data.get("username", "")).strip()
        password   = str(data.get("password", "")).strip()
        client_key = str(data.get("client_key") or session_id)
        if not KEY_RE.match(client_key):
            client_key = session_id

        if not username or not password:
            await send({"type": "error", "msg": "الكود أو الباسورد فاضل!"})
            return
        if len(username) > 100 or len(password) > 200:
            await send({"type": "error", "msg": "البيانات طويلة بشكل غير طبيعي"})
            return

        ukey = username.lower()
        uid = make_uid(client_key)
        running = _state["by_user"].get(ukey)
        if running:
            if running.uid == uid:               # نفس المتصفح فتح الصفحة تاني → نكمل معاه
                await send({"type": "attached", "session_id": running.sid})
                await _stream(ws, running, send)
            else:
                await send({"type": "error",
                            "msg": "⚠️ فيه جلسة شغالة على الحساب ده بالفعل، استنى لما تخلص."})
            return
        if _state["sem"].locked() and len(_state["queue"]) >= MAX_QUEUE:
            await send({"type": "error", "msg": "⚠️ الطابور ممتلئ، جرّب بعد شوية."})
            return

        botdb.touch_user(uid, "web")
        botdb.save_username(uid, username)

        job = Job(session_id, ukey, uid)
        _state["jobs"][session_id] = job
        _state["by_user"][ukey] = job
        job.refresh()
        job.task = asyncio.create_task(run_session(job, username, password))
        password = ""                             # الـ task ماسك نسخته، ومش محتاجينه هنا
        await _stream(ws, job, send)

    except Exception as ex:
        msg = str(ex)
        if password:
            msg = msg.replace(password, "***")
        await send({"type": "error", "msg": msg[:300]})


# ── Static ───────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse(_state["index"])


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
