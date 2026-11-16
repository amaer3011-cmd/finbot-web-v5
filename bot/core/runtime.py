"""وحدة runtime — جزء من حزمة core (مقسومة من core.py)."""
from PIL import Image, ImageDraw
from datetime import datetime, timezone
from urllib.parse import urlsplit
import functools
import io
import re
import time

from .config import (
    Stopped,
    _cancel,
    _hook,
    _log,
    _report_consent,
    _timing,
)


def _redact_screenshot_bytes(image_bytes):
    with Image.open(io.BytesIO(image_bytes)) as image:
        image = image.convert("RGB")
        ImageDraw.Draw(image).rectangle(
            (0, 0, image.width, min(125, image.height)), fill="black"
        )
        result = io.BytesIO()
        image.save(result, format="PNG")
        return result.getvalue()

def _redacted_screenshot(page):
    """Capture a viewport screenshot while masking login fields and the account header."""
    try:
        image_bytes = page.screenshot(
            full_page=False,
            type="png",
            timeout=5000,
            mask=[page.locator("input, textarea"), page.locator("header, [role='banner']")],
            mask_color="#000000",
        )
        return _redact_screenshot_bytes(image_bytes)
    except Exception as exc:  # noqa: BLE001
        say_debug(f"تعذر إنشاء لقطة تشخيص منقحة ({type(exc).__name__}).")
        return None

def _diagnostic_report(status, error, last, screenshot, page):
    page_url = urlsplit(page) if page else None
    page_location = ""
    if page_url:
        safe_path = re.sub(r"(?i)[0-9a-f-]{16,}", "[id]", page_url.path)
        safe_path = re.sub(r"\d+", "[id]", safe_path)
        page_location = f"{page_url.netloc}{safe_path}"[:200]
    safe_error = str(error or "لم يكتمل النشاط المتوقع.")
    safe_error = re.sub(r"(?i)(password|token|authorization|cookie)=\S+", r"\1=[redacted]", safe_error)
    safe_error = re.sub(r"(https?://[^\s?]+)\?[^\s]+", r"\1?[redacted]", safe_error)
    report = {
        "id": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        "status": status,
        "error": safe_error[:500],
        "activity": str(last or "غير محدد")[:150],
        "page": page_location or "غير متاح",
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "screenshot": screenshot,
    }
    return report

def _send_diagnostic(report):
    if _report_consent.get():
        say(report, "diagnostic")

def say(msg, kind="log"):
    hook = _hook.get()
    if hook:
        try:
            hook(kind, msg)
        except Exception as exc:
            _log.warning("progress hook failed (%s)", type(exc).__name__)
    else:
        print(f"[{kind}] {msg}")

def say_debug(*args):
    say(" ".join(str(a) for a in args), "debug")

def check_cancel():
    event = _cancel.get()
    if event is not None and event.is_set():
        raise Stopped("stopped")

def _timed(fn):
    """يسجّل عدد مرات وزمن كل خطوة (يظهر في اللوج آخر الحل لتحديد أبطأ مرحلة)."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        started = time.perf_counter()
        try:
            return fn(*args, **kwargs)
        finally:
            table = _timing.get()
            if table is not None:
                row = table.setdefault(fn.__name__, [0, 0.0])
                row[0] += 1
                row[1] += time.perf_counter() - started
    return wrapper
