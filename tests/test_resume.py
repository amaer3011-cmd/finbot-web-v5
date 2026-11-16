"""اختبار إعادة الاتصال بالحل بعد قفل الصفحة — بيستخدم محرّك مزيّف (مفيش متصفح ولا منصة).
يحتاج مجلد البوت: BOT_DIR=/path/to/finbot_v4_1 python -m pytest tests/test_resume.py
"""
import json
import os
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BOT = Path(os.getenv("BOT_DIR", ROOT / "bot"))
if not (BOT / "core").exists():
    pytest.skip("BOT_DIR غير موجود", allow_module_level=True)

os.environ["BOT_DIR"] = str(BOT)
os.environ["DB_PATH"] = str(ROOT / "tests" / "_test_web.db")
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402
import main  # noqa: E402

SID1 = "a" * 32
SID2 = "b" * 32
SID3 = "c" * 32


def fake_run_job(username, password, entries, hook=None, cancel=None, headless=True, report_consent=False):
    hook("login_ok", "")
    hook("plan_todo", "وحدة ١|0|3")
    for i in range(3):
        if cancel.wait(0.4):
            return {"status": "stopped", "last": "درس"}
        hook("q_ok", "")
        hook("done", f"درس {i}")
    assert password == "secret-pass"
    return {"status": "stuck", "error": "توقف:\n• سؤال ناقص ١\n• سؤال ناقص ٢", "last": "درس 2"}


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(main.core, "run_job", fake_run_job)
    main._state["jobs"].clear(); main._state["by_user"].clear(); main._state["queue"].clear()
    main.ws_limiter.hits.clear()
    with TestClient(main.app) as c:
        yield c
    try:
        os.remove(os.environ["DB_PATH"])
    except OSError:
        pass


def recv_until(ws, kinds, limit=60):
    seen = []
    for _ in range(limit):
        m = json.loads(ws.receive_text())
        seen.append(m)
        if m["type"] in kinds:
            return m, seen
    raise AssertionError(f"لم تصل {kinds}: {[s['type'] for s in seen]}")


def start(ws, user="u1", key="k" * 16):
    ws.send_text(json.dumps({"username": user, "password": "secret-pass", "client_key": key}))


def test_job_survives_disconnect_and_resume_gets_result(client):
    with client.websocket_connect(f"/ws/{SID1}") as ws:
        start(ws)
        _, seen = recv_until(ws, {"progress"})
    # المتصفح اتقفل — الحل لازم يكمّل
    time.sleep(0.2)
    assert SID1 in main._state["jobs"]
    with client.websocket_connect(f"/ws/{SID1}") as ws2:
        ws2.send_text(json.dumps({"resume": True}))
        done, seen = recv_until(ws2, {"done"})
    assert done["status"] == "stuck"
    assert done["summary"]["questions"] == 3
    assert done["unmatched"] == ["سؤال ناقص ١", "سؤال ناقص ٢"]
    assert "secret-pass" not in json.dumps(seen, ensure_ascii=False)
    # بعد الانتهاء الحساب يتفك
    assert "u1" not in main._state["by_user"]


def test_resume_unknown_session_is_gone(client):
    with client.websocket_connect(f"/ws/{SID2}") as ws:
        ws.send_text(json.dumps({"resume": True}))
        assert json.loads(ws.receive_text())["type"] == "gone"


def test_same_browser_reopening_attaches_other_browser_rejected(client):
    with client.websocket_connect(f"/ws/{SID1}") as ws:
        start(ws, key="k" * 16)
        recv_until(ws, {"progress"})
        # نفس المتصفح بـ session جديد → attached
        with client.websocket_connect(f"/ws/{SID2}") as ws2:
            start(ws2, key="k" * 16)
            m, _ = recv_until(ws2, {"attached", "error"})
            assert m["type"] == "attached" and m["session_id"] == SID1
        # متصفح تاني بنفس الحساب → مرفوض
        with client.websocket_connect(f"/ws/{SID3}") as ws3:
            start(ws3, key="z" * 16)
            m, _ = recv_until(ws3, {"error", "attached"})
            assert m["type"] == "error"
        client.post(f"/cancel/{SID1}")
        recv_until(ws, {"done"})


def test_cancel_and_status_endpoint(client):
    with client.websocket_connect(f"/ws/{SID1}") as ws:
        start(ws)
        recv_until(ws, {"progress"})
        assert client.get(f"/api/job/{SID1}").json()["state"] == "running"
        assert client.post(f"/cancel/{SID1}").json() == {"ok": True}
        done, _ = recv_until(ws, {"done"})
    assert done["status"] == "stopped"
    assert client.get(f"/api/job/{SID1}").json()["state"] == "done"
    assert client.get(f"/api/job/{'d' * 32}").json()["state"] == "gone"


def test_progress_not_spammed_when_nothing_changes(client):
    """الرسائل بتتبعت عند التغيير + heartbeat كل ٥ ثواني، مش كل ثانية."""
    with client.websocket_connect(f"/ws/{SID1}") as ws:
        start(ws)
        t0 = time.monotonic()
        n = 0
        while True:
            m = json.loads(ws.receive_text())
            if m["type"] == "progress":
                n += 1
            if m["type"] == "done":
                break
        elapsed = time.monotonic() - t0
    assert n <= 12, (n, elapsed)
