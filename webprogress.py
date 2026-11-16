"""
webprogress.py — نموذج التقدم الحي للموقع (نفس منطق progress.py في البوت v4.1).

بياخد أحداث الـ hook من core.run_job ويطلّع snapshot جاهز للواجهة:
المرحلة، النشاط الحالي، تقدم الاختبار، خطة الوحدات، النسبة الكلية، السرعة، والوقت المتوقع.

مفيش هنا أي اعتماد على FastAPI أو core: normalize و fmt_duration بيتمرّروا من برّه، فيتختبر لوحده.
"""
import threading
import time
from collections import deque

MAX_UNIT_ROWS = 12
RECENT_LINES = 6


class JobProgress:
    def __init__(self, normalize, fmt_duration, clock=time.monotonic):
        self._norm = normalize
        self._fmt = fmt_duration
        self._clock = clock
        self._lock = threading.Lock()
        self.t0 = clock()
        self.lines = deque(maxlen=10)
        self.completed = set()
        self.plan_units = {}            # normalized name -> (name, done, total)
        self.questions = 0
        self.activities = 0
        self.phase = "بيجهّز الاتصال بالمنصة"
        self.current_activity = ""
        self.quiz_position = None       # (current, total)

    # ── بداية الحل الفعلية (بعد الطابور) — السرعة والتقدير بيتحسبوا من هنا ──
    def mark_started(self):
        with self._lock:
            self.t0 = self._clock()

    def elapsed(self):
        return self._clock() - self.t0

    # ── استقبال أحداث المحرك (بتتنادى من thread الحل) ──
    def _record_plan_unit(self, text):
        parts = (text or "").rsplit("|", 2)
        if len(parts) != 3:
            return
        name, done, total = parts
        try:
            done, total = int(done), int(total)
        except ValueError:
            return
        key = self._norm(name)
        if key and total > 0:
            self.plan_units[key] = (name, done, total)

    def handle(self, kind, text):
        raw = str(text or "")
        with self._lock:
            if kind == "q_ok":
                self.questions += 1
            elif kind == "phase":
                self.phase = raw
            elif kind == "login_ok":
                self.phase = "تم تسجيل الدخول؛ بيفحص خطة المنهج"
            elif kind == "open":
                self.current_activity = raw
                self.phase = "بيحل النشاط الحالي"
                self.quiz_position = None
            elif kind == "quiz_solving":
                self.current_activity = raw
                self.phase = "بيحل الاختبار"
                self.quiz_position = None
            elif kind == "quiz_progress":
                parts = raw.split("|", 1)
                self.quiz_position = None
                if len(parts) == 2:
                    try:
                        self.quiz_position = (int(parts[0]), int(parts[1]))
                    except ValueError:
                        pass
                self.phase = "بيحل الاختبار"
            elif kind == "log":
                self.phase = raw
            elif kind == "plan_todo":
                self._record_plan_unit(raw)
            elif kind in ("done", "quiz_done"):
                key = self._norm(raw)
                if not key or key in self.completed:
                    return
                self.completed.add(key)
                self.activities += 1
                label = "اختبار" if kind == "quiz_done" else "نشاط"
                self.lines.append(f"✅ اكتمل {label}: {raw}")
                if self._norm(self.current_activity) == key:
                    self.current_activity = ""
                    self.quiz_position = None
                    self.phase = "بيحدّث حالة المنهج"
                elif not self.current_activity:
                    self.phase = "بيحدّث حالة المنهج"
            elif kind == "group_done":
                key = self._norm(raw)
                if not key or key in self.completed:
                    return
                self.completed.add(key)
                self.lines.append(f"🏁 اكتملت الوحدة: {raw}")
            elif kind == "plan_done":
                self._record_plan_unit(raw)
                name = raw.split("|")[0]
                key = self._norm(name)
                if not key or key in self.completed:
                    return
                self.completed.add(key)
                self.lines.append(f"✅ مكتملة مسبقًا: {name}")
            elif kind == "group_skipped":
                key = self._norm(raw)
                if not key or key in self.completed:
                    return
                self.completed.add(key)
                self.lines.append(f"✅ مكتملة مسبقًا: {raw}")

    # ── حسابات (نفس معادلات البوت) ──
    def _overall(self):
        total = sum(u[2] for u in self.plan_units.values())
        if total <= 0:
            return 0, 0
        base = sum(max(0, min(u[1], u[2])) for u in self.plan_units.values())
        return min(total, base + self.activities), total

    def _unit_rows(self):
        """توزيع الأنشطة المنجزة بالترتيب على الوحدات غير المكتملة."""
        extra = self.activities
        rows = []
        for name, done, total in self.plan_units.values():
            done = max(0, min(done, total))
            take = min(extra, total - done)
            done += take
            extra -= take
            rows.append((name, done, total))
        return rows

    def _speed(self, elapsed):
        if self.questions < 1 or elapsed < 30:
            return None
        return max(1, round(self.questions * 60 / elapsed))

    def _eta(self, elapsed, done, total):
        if self.activities < 2 or total <= 0 or done >= total or elapsed < 30:
            return ""
        rate = self.activities / elapsed
        if rate <= 0:
            return ""
        remaining = (total - done) / rate
        return self._fmt(round(remaining, -1) or remaining)

    def snapshot(self):
        with self._lock:
            elapsed = self.elapsed()
            done, total = self._overall()
            rows = self._unit_rows()
            current = next((i for i, (_n, d, t) in enumerate(rows) if d < t), None)
            units = [{
                "name": name, "done": d, "total": t,
                "state": "done" if d >= t else ("current" if i == current else "todo"),
            } for i, (name, d, t) in enumerate(rows[:MAX_UNIT_ROWS])]
            return {
                "phase": self.phase[:120],
                "current": self.current_activity[:120],
                "quiz": ({"cur": self.quiz_position[0], "total": self.quiz_position[1]}
                         if self.quiz_position else None),
                "overall": {"done": done, "total": total,
                            "percent": int(done * 100 / total) if total else 0},
                "units": units,
                "units_hidden": max(0, len(rows) - MAX_UNIT_ROWS),
                "units_done": sum(1 for _n, d, t in rows if d >= t),
                "units_total": len(rows),
                "questions": self.questions,
                "activities": self.activities,
                "elapsed_s": int(elapsed),
                "elapsed": self._fmt(elapsed),
                "speed": self._speed(elapsed),
                "eta": self._eta(elapsed, done, total),
                "lines": list(self.lines)[-RECENT_LINES:],
            }
