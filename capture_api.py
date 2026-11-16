#!/usr/bin/env python3
"""
capture_api.py — يسجّل طلبات الـ API اللي المنصة بتبعتها وإنت بتشغّل البوت الحالي نفسه.

الهدف: نعرف المنصة بتتكلم مع السيرفر إزاي (login / محتوى النشاط / إرسال الإجابة / إكمال النشاط)
عشان نكتب نسخة HTTP سريعة بدل المتصفح. مفيش DevTools ولا HAR ولا أي خطوات يدوية.

الاستخدام:
    1) انسخ الملف ده جوه مجلد البوت (جنب core.py)
    2) من نفس المجلد:
           python capture_api.py                 # بيسأل عن الكود والباسورد
           python capture_api.py --activities 3  # يقف بعد 3 أنشطة (الافتراضي 3)
           python capture_api.py --seconds 300   # أو بعد 5 دقايق (الافتراضي 240)
           python capture_api.py --show          # يفتح المتصفح قدامك
    3) هيطلع api_capture.json — افتحه وراجعه، وابعته.

الخصوصية:
    - الباسورد والكود وأي token/cookie/Authorization بيتمسحوا (بالاسم وبالقيمة)
    - في الآخر فيه فحص نهائي بيستبدل الكود والباسورد الحقيقيين بـ <USER>/<PASS> في كل الملف
    - استخدم حساب تجريبي لو تقدر
"""
import argparse
import collections
import getpass
import json
import os
import re
import sys
import threading
import time
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.getenv("BOT_DIR") or HERE)

RED = "<REDACTED>"
SENSITIVE_HEADERS = {"cookie", "set-cookie", "authorization", "proxy-authorization",
                     "x-csrf-token", "x-xsrf-token", "x-api-key", "x-auth-token"}
SENSITIVE_KEY = re.compile(
    r"(pass(word|wd)?|pwd|secret|token|jwt|session|sess|auth|cookie|csrf|xsrf|signature|sig|"
    r"api[_-]?key|otp|pin|user(name)?|login|email|phone|mobile|national|ssn|code)$", re.I)
JWT = re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]*")
LONG_TOKEN = re.compile(r"\b[A-Fa-f0-9]{32,}\b|\b[A-Za-z0-9_-]{40,}\b")
SKIP_HEADERS = {"user-agent", "accept-language", "sec-ch-ua", "sec-ch-ua-platform", "sec-ch-ua-mobile",
                "sec-fetch-site", "sec-fetch-mode", "sec-fetch-dest", "accept-encoding", "connection"}
MAX_BODY = 12000        # الردود الكبيرة (زي المنهج) بتتقص
SAMPLES_PER_ENDPOINT = 3


# ── تنضيف ────────────────────────────────────────────────────────────

def scrub_text(s):
    return LONG_TOKEN.sub(RED, JWT.sub(RED, s))


def scrub_json(o):
    if isinstance(o, dict):
        return {k: (RED if SENSITIVE_KEY.search(k) and not isinstance(v, (dict, list)) else scrub_json(v))
                for k, v in o.items()}
    if isinstance(o, list):
        return [scrub_json(x) for x in o]
    return scrub_text(o) if isinstance(o, str) else o


def scrub_body(text, mime=""):
    if not text:
        return text
    t = text.strip()
    if t[:1] in "{[":
        try:
            out = json.dumps(scrub_json(json.loads(t)), ensure_ascii=False)
            return out if len(out) <= MAX_BODY else out[:MAX_BODY] + "…[truncated]"
        except ValueError:
            pass
    if "x-www-form-urlencoded" in (mime or "") or re.fullmatch(r"[^=&\s]+=[^&]*(&[^=&\s]+=[^&]*)*", t):
        pairs = [(k, RED if SENSITIVE_KEY.search(k) else scrub_text(v)) for k, v in parse_qsl(t, keep_blank_values=True)]
        return urlencode(pairs, safe="<>")
    out = scrub_text(t)
    return out if len(out) <= MAX_BODY else out[:MAX_BODY] + "…[truncated]"


def scrub_url(url):
    p = urlsplit(url)
    q = [(k, RED if SENSITIVE_KEY.search(k) else scrub_text(v)) for k, v in parse_qsl(p.query, keep_blank_values=True)]
    return urlunsplit((p.scheme, p.netloc, scrub_text(p.path), urlencode(q, safe="<>"), ""))


def scrub_headers(headers):
    out = {}
    for name, value in (headers or {}).items():
        low = name.lower()
        if low in SKIP_HEADERS:
            continue
        out[name] = RED if (low in SENSITIVE_HEADERS or SENSITIVE_KEY.search(low)) else scrub_text(str(value))
    return out


_ID_SEG = re.compile(r"/(\d+|[0-9a-fA-F]{8}-[0-9a-fA-F-]{27})")


def endpoint_key(method, url):
    p = urlsplit(url)
    path = _ID_SEG.sub("/{id}", p.path)
    return method + " " + p.netloc + path


# ── المسجِّل ─────────────────────────────────────────────────────────

class Recorder:
    def __init__(self, max_activities, cancel):
        self.t0 = time.monotonic()
        self.calls, self.events = [], []
        self.seen = collections.Counter()
        self.max_activities, self.cancel = max_activities, cancel
        self.activities = 0
        self.last_event = ""
        self.lock = threading.Lock()

    def now(self):
        return round(time.monotonic() - self.t0, 2)

    # يتنادى لكل رد من المتصفح
    def on_response(self, resp):
        try:
            req = resp.request
            if req.resource_type not in ("fetch", "xhr"):
                return
            key = endpoint_key(req.method, req.url)
            with self.lock:
                self.seen[key] += 1
                n = self.seen[key]
            if n > SAMPLES_PER_ENDPOINT:      # عينات كفاية من كل endpoint
                return
            try:
                body = resp.text()
            except Exception as e:             # noqa: BLE001
                body = f"<unreadable: {type(e).__name__}>"
            mime = (resp.headers or {}).get("content-type", "")
            try:
                post = req.post_data
            except Exception:                  # noqa: BLE001
                post = None
            rec = {
                "t": self.now(),
                "after_event": self.last_event,
                "endpoint": key,
                "method": req.method,
                "url": scrub_url(req.url),
                "status": resp.status,
                "request_headers": scrub_headers(req.headers),
                "request_body": scrub_body(post, (req.headers or {}).get("content-type", "")) if post else None,
                "response_mime": mime,
                "response_body": scrub_body(body, mime),
                "sample": n,
            }
            with self.lock:
                self.calls.append(rec)
        except Exception:                      # noqa: BLE001
            pass                               # التسجيل ما يوقفش الحل أبداً

    # hook بيتبعت لـ core.run_job
    def hook(self, kind, text):
        try:
            t = str(text or "")
            if kind == "debug" and not t.startswith("⏱️"):
                return
            with self.lock:
                if len(self.events) < 600:
                    self.events.append({"t": self.now(), "kind": kind, "text": t[:300 if kind == "debug" else 80]})
                if kind != "debug":
                    self.last_event = f"{kind}:{t[:40]}"
            if kind == "done":
                self.activities += 1
                if self.activities >= self.max_activities:
                    self.cancel.set()
        except Exception:                      # noqa: BLE001
            pass

    def endpoint_summary(self):
        return [{"endpoint": k, "count": c} for k, c in self.seen.most_common()]

    def dump(self, path, meta, secrets_to_hide=()):
        data = {"meta": meta, "endpoints": self.endpoint_summary(), "events": self.events, "calls": self.calls}
        text = json.dumps(data, ensure_ascii=False, indent=1)
        for secret, label in secrets_to_hide:          # فحص نهائي على النص كله
            if secret:
                text = text.replace(secret, label)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return text


def install_context_listener(recorder):
    """يضيف مستمع للردود على أي context يتعمل — من غير ما نعدّل core.py."""
    from playwright.sync_api import Browser
    orig = Browser.new_context

    def patched(self, *a, **kw):
        ctx = orig(self, *a, **kw)
        ctx.on("response", recorder.on_response)
        return ctx

    Browser.new_context = patched
    return orig


def main():
    ap = argparse.ArgumentParser(description="تسجيل طلبات API المنصة من تشغيل البوت")
    ap.add_argument("--activities", type=int, default=3, help="يقف بعد كام نشاط (افتراضي 3)")
    ap.add_argument("--seconds", type=int, default=240, help="أقصى مدة بالثواني (افتراضي 240)")
    ap.add_argument("--show", action="store_true", help="افتح المتصفح بدل headless")
    ap.add_argument("--out", default="api_capture.json")
    args = ap.parse_args()

    import core
    entries = core.parse_answer_file(core.locate_answer_file())
    print(f"تم تحميل {len(entries)} سؤالًا من بنك الإجابات")
    username = os.getenv("SITE_USER") or input("كود الدخول: ").strip()
    password = os.getenv("SITE_PASS") or getpass.getpass("الباسورد: ")
    if not username or not password:
        sys.exit("الكود والباسورد مطلوبين")

    cancel = threading.Event()
    rec = Recorder(args.activities, cancel)
    install_context_listener(rec)
    threading.Timer(args.seconds, cancel.set).start()

    print(f"▶️ بشغّل البوت — هيقف بعد {args.activities} أنشطة أو {args.seconds} ثانية…")
    result = core.run_job(username, password, entries, hook=rec.hook, cancel=cancel,
                          headless=not args.show, report_consent=False)
    meta = {"status": result.get("status"), "seconds": rec.now(), "activities_done": rec.activities,
            "calls_recorded": len(rec.calls), "unique_endpoints": len(rec.seen)}
    # كود رقمي → تسعات بنفس الطول (رقم صالح في JSON) عشان شكل الـ JSON ما يتكسرش
    user_label = "9" * len(username) if username.isdigit() else "<USER>"
    rec.dump(args.out, meta, secrets_to_hide=[(password, "<PASS>"), (username, user_label)])

    print(f"\n✅ اتسجّل {meta['calls_recorded']} طلب من {meta['unique_endpoints']} endpoint → {args.out}")
    for row in rec.endpoint_summary()[:25]:
        print(f"   {row['count']:>4} ×  {row['endpoint']}")
    print("\n⚠️  افتح الملف وراجعه بعينك قبل ما تبعته.")


if __name__ == "__main__":
    main()
