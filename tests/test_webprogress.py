"""اختبار نموذج التقدم (webprogress.py) — مستقل، مش محتاج البوت. شغّله: python -m pytest tests  أو  python tests/test_webprogress.py
الأرقام المتوقعة اتأكدت إنها نفس اللي بيطلّعه Progress الأصلي في بوت v4.1 على نفس الأحداث."""
import os, sys, threading
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from webprogress import JobProgress

norm = lambda s: " ".join(str(s).split())

class Clock:
    t = 1000.0
    def __call__(self): return self.t

EVENTS = [
    ("login_ok", ""), ("plan_done", "وحدة ١|28|28"), ("plan_todo", "وحدة ٢|16|23"),
    ("plan_todo", "وحدة ٣|0|23"), ("plan_todo", "وحدة ٤|0|23"),
    ("open", "درس ١"), ("q_ok", ""), ("q_ok", ""), ("q_ok", ""),
    ("done", "درس ١"), ("done", "درس ١"),                       # مكرر
    ("quiz_solving", "اختبار ١"), ("quiz_progress", "7|10"), ("q_ok", ""), ("q_ok", ""),
    ("quiz_done", "اختبار ١"), ("group_done", "وحدة ٢"),
    ("open", "درس ٢"), ("done", "درس ٢"), ("done", "درس ٣"),
    ("quiz_progress", "bad|data"), ("plan_todo", "شكل غلط"),
]

def build():
    clk = Clock(); p = JobProgress(norm, lambda s: int(s), clock=clk)
    for k, t in EVENTS: p.handle(k, t)
    return clk, p

def test_counters_and_dedupe():
    _, p = build()
    assert (p.questions, p.activities) == (5, 4)

def test_overall_and_units():
    _, p = build(); s = p.snapshot()
    assert s["overall"] == {"done": 48, "total": 97, "percent": 49}
    assert [(u["done"], u["total"], u["state"]) for u in s["units"]] == [
        (28, 28, "done"), (20, 23, "current"), (0, 23, "todo"), (0, 23, "todo")]
    assert (s["units_done"], s["units_total"]) == (1, 4)

def test_speed_and_eta():
    clk, p = build()
    clk.t = p.t0 + 29;  s = p.snapshot(); assert s["speed"] is None and s["eta"] == ""     # أقل من 30 ثانية
    clk.t = p.t0 + 95;  s = p.snapshot(); assert s["speed"] == 3 and s["eta"] == 1160      # ≈ ١٩ د ٢٠ ث

def test_quiz_resets_on_new_activity_and_bad_input_is_ignored():
    p = JobProgress(norm, str)
    p.handle("quiz_progress", "7|10"); assert p.snapshot()["quiz"] == {"cur": 7, "total": 10}
    p.handle("open", "درس"); assert p.snapshot()["quiz"] is None
    p.handle("quiz_progress", "x|y"); p.handle("plan_todo", "بدون أرقام"); p.handle("غريب", "؟")   # ما يكسرش

def test_more_than_12_units_hidden_count():
    p = JobProgress(norm, str)
    for i in range(15): p.handle("plan_todo", f"وحدة {i}|0|5")
    s = p.snapshot(); assert len(s["units"]) == 12 and s["units_hidden"] == 3

def test_thread_safety():
    p = JobProgress(norm, str); stop = []; errs = []
    def w():
        for i in range(2000): p.handle("done", f"n{i}"); p.handle("q_ok", "")
    def r():
        try:
            while not stop: p.snapshot()
        except Exception as e: errs.append(e)
    a, b = threading.Thread(target=w), threading.Thread(target=r); b.start(); a.start(); a.join(); stop.append(1); b.join()
    assert not errs and p.activities == 2000

if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"): f(); print("ok", n)
