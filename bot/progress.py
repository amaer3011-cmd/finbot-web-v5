"""رسالة التقدم الحية: بطاقة تتحدث أثناء الحل بشريط ناعم وأزرار تفاعلية."""
import asyncio
import html
import logging
import time
from collections import deque

from telegram.constants import ParseMode
from telegram.error import BadRequest, RetryAfter, TelegramError

import core
from ui import (
    CLOCKS,
    SPINNER,
    ar_num,
    ar_percent,
    fine_bar,
    format_duration,
    progress_bar,
    progress_kb,
    short,
)

log = logging.getLogger("finbot")

MAX_UNIT_ROWS = 12
RECENT_LINES = 6
REFRESH_SECONDS = 3  # أقل فاصل لتحريك المؤشر والساعة (يحترم حدود تيليجرام)

_PHASE_ICONS = (
    ("الدخول", "🔐"),
    ("الطابور", "🚦"),
    ("الاختبار", "🧪"),
    ("الاستعادة", "🔁"),
    ("المنهج", "🧭"),
)


def phase_icon(phase):
    for needle, icon in _PHASE_ICONS:
        if needle in phase:
            return icon
    return "⚡"


class Progress:
    def __init__(self, bot, chat_id, message_id):
        self.bot, self.chat_id, self.message_id = bot, chat_id, message_id
        self.lines = deque(maxlen=10)
        self.completed = set()
        self.plan_units = {}
        self.questions = 0
        self.activities = 0
        self.header = "🚀 <b>جاري حل المنصة</b>"
        self.phase = "بيجهّز الاتصال بالمنصة"
        self.current_activity = ""
        self.quiz_position = None
        self.show_stop = True
        self.show_details = False
        self.confirm_stop = False
        self.dirty = True
        self.t0 = time.monotonic()
        self.last_edit_at = 0.0
        self.last_rendered = None
        self.retry_not_before = 0.0
        self.edit_failures = 0

    # ───────────── استقبال أحداث المحرك ─────────────

    def _record_plan_unit(self, text):
        parts = (text or "").rsplit("|", 2)
        if len(parts) != 3:
            return
        name, done, total = parts
        try:
            done, total = int(done), int(total)
        except ValueError:
            return
        normalized = core.normalize_text(name)
        if normalized and total > 0:
            self.plan_units[normalized] = (name, done, total)

    def handle(self, kind, text):
        raw = text or ""
        e = html.escape(raw)
        if kind == "q_ok":
            self.questions += 1
            self.dirty = True
            return
        elif kind == "phase":
            self.phase = raw
            self.dirty = True
            return
        elif kind == "login_ok":
            self.phase = "تم تسجيل الدخول؛ بيفحص خطة المنهج"
            self.dirty = True
            return
        elif kind == "open":
            self.current_activity = raw
            self.phase = "بيحل النشاط الحالي"
            self.quiz_position = None
            self.dirty = True
            return
        elif kind == "quiz_solving":
            self.current_activity = raw
            self.phase = "بيحل الاختبار"
            self.quiz_position = None
            self.dirty = True
            return
        elif kind == "quiz_progress":
            parts = raw.split("|", 1)
            if len(parts) == 2:
                try:
                    self.quiz_position = (int(parts[0]), int(parts[1]))
                except ValueError:
                    self.quiz_position = None
            self.phase = "بيحل الاختبار"
            self.dirty = True
            return
        elif kind == "log":
            self.phase = raw
            self.dirty = True
            return
        elif kind == "plan_todo":
            self._record_plan_unit(raw)
            self.dirty = True
            return
        elif kind in ("done", "quiz_done"):
            normalized = core.normalize_text(text or "")
            if not normalized or normalized in self.completed:
                return
            self.completed.add(normalized)
            self.activities += 1
            label = "اختبار" if kind == "quiz_done" else "نشاط"
            self.lines.append(f"✅ اكتمل {label}: <b>{e}</b>")
            if core.normalize_text(self.current_activity) == normalized:
                self.current_activity = ""
                self.quiz_position = None
                self.phase = "بيحدّث حالة المنهج"
            elif not self.current_activity:
                self.phase = "بيحدّث حالة المنهج"
        elif kind == "group_done":
            normalized = core.normalize_text(text or "")
            if not normalized or normalized in self.completed:
                return
            self.completed.add(normalized)
            self.lines.append(f"🏁 اكتملت الوحدة: <b>{e}</b>")
        elif kind == "plan_done":
            self._record_plan_unit(raw)
            name = (e.split("|") + [""])[0]
            normalized = core.normalize_text(name)
            if not normalized or normalized in self.completed:
                return
            self.completed.add(normalized)
            self.lines.append(f"✅ مكتملة مسبقًا: <b>{name}</b>")
        elif kind == "group_skipped":
            normalized = core.normalize_text(text or "")
            if not normalized or normalized in self.completed:
                return
            self.completed.add(normalized)
            self.lines.append(f"✅ مكتملة مسبقًا: <b>{e}</b>")
        else:
            return
        self.dirty = True

    # ───────────── حسابات التقدم ─────────────

    def overall_items(self):
        total = sum(unit[2] for unit in self.plan_units.values())
        if total <= 0:
            return 0, 0
        base = sum(max(0, min(unit[1], unit[2])) for unit in self.plan_units.values())
        return min(total, base + self.activities), total

    def unit_rows(self):
        """حالة كل وحدة بعد توزيع الأنشطة المنجزة بالترتيب على الوحدات غير المكتملة."""
        extra = self.activities
        rows = []
        for name, done, total in self.plan_units.values():
            done = max(0, min(done, total))
            take = min(extra, total - done)
            done += take
            extra -= take
            rows.append((name, done, total))
        return rows

    def eta_line(self, elapsed):
        done, total = self.overall_items()
        if self.activities < 2 or total <= 0 or done >= total or elapsed < 30:
            return ""
        rate = self.activities / elapsed
        if rate <= 0:
            return ""
        remaining = (total - done) / rate
        estimate = round(remaining, -1) or remaining
        return (
            f"⏳ المتوقع للانتهاء: <b>~{format_duration(estimate)}</b> "
            "<i>(تقدير تقريبي)</i>\n"
        )

    def speed_line(self, elapsed):
        if self.questions < 1 or elapsed < 30:
            return ""
        per_minute = max(1, round(self.questions * 60 / elapsed))
        return f"🏎️ السرعة: <b>{ar_num(per_minute)}</b> سؤال/دقيقة\n"

    def snapshot(self):
        """ملخص قصير للنافذة المنبثقة (زر حالة الحل)."""
        elapsed = time.monotonic() - self.t0
        done, total = self.overall_items()
        lines = [f"📊 {ar_percent(done, total)} ({ar_num(done)}/{ar_num(total)} عنصر)" if total else "📊 بيحسب خطة المنهج…"]
        lines.append(f"📝 {ar_num(self.questions)} سؤال • 📚 {ar_num(self.activities)} نشاط")
        lines.append(f"⏱️ {format_duration(elapsed)}")
        if self.current_activity:
            lines.append("🎯 " + short(self.current_activity, 60))
        return "\n".join(lines)[:190]

    # ───────────── العرض ─────────────

    def _units_block(self):
        rows = self.unit_rows()
        if not rows:
            return "🗂 <i>الوحدات: لسه بيقرأ خطة المنهج…</i>\n"
        current = next((i for i, (_n, d, t) in enumerate(rows) if d < t), None)
        out = ["🗂 <b>الوحدات</b>"]
        for index, (name, done, total) in enumerate(rows[:MAX_UNIT_ROWS]):
            if done >= total:
                icon = "✅"
            elif index == current:
                icon = "🔄"
            else:
                icon = "⏳"
            label = html.escape(short(name, 26))
            out.append(
                f"{icon} {label} <code>{progress_bar(done, total, 6)}</code> "
                f"{ar_num(done)}/{ar_num(total)}"
            )
        hidden = len(rows) - MAX_UNIT_ROWS
        if hidden > 0:
            out.append(f"<i>… و{ar_num(hidden)} وحدات أخرى</i>")
        return "\n".join(out) + "\n"

    def render(self):
        body = "\n".join(list(self.lines)[-RECENT_LINES:]) or "…"
        elapsed = time.monotonic() - self.t0
        spinner = SPINNER[int(elapsed // REFRESH_SECONDS) % len(SPINNER)] if self.show_stop else ""

        items_done, items_total = self.overall_items()
        if items_total > 0:
            overall = (
                "📊 <b>التقدم الكلي</b>\n"
                f"<code>{fine_bar(items_done, items_total, 18)}</code> "
                f"<b>{ar_percent(items_done, items_total)}</b> "
                f"({ar_num(items_done)}/{ar_num(items_total)} عنصر)\n"
            )
            rows = self.unit_rows()
            finished_units = sum(done >= total for _name, done, total in rows)
            overall += (
                f"🧭 تقدم المنهج: <b>{ar_num(finished_units)}/"
                f"{ar_num(len(rows))}</b> وحدات مكتملة\n"
            )
        else:
            overall = (
                "📊 <b>التقدم الكلي</b>\n"
                f"<code>{fine_bar(0, 0, 18)}</code> <i>بانتظار خطة المنهج…</i>\n"
            )

        current_line = ""
        if self.current_activity:
            current_line = (
                f"🎯 النشاط الحالي: <b>{html.escape(self.current_activity[:120])}</b>\n"
            )
        quiz_line = ""
        if self.quiz_position:
            current, total = self.quiz_position
            quiz_line = (
                f"🧪 سؤال الاختبار: <b>{ar_num(current)}/{ar_num(total)}</b> "
                f"<code>{progress_bar(current, total, 8)}</code>\n"
            )
        details = ""
        if self.show_details:
            details = "┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n" + self._units_block()

        clock = CLOCKS[int(elapsed // 10) % len(CLOCKS)]
        phase = self.phase[:120]
        return (
            f"{self.header} {spinner}".rstrip() + "\n"
            "━━━━━━━━━━━━━━\n"
            f"{phase_icon(phase)} المرحلة: <b>{html.escape(phase)}</b>\n"
            f"{current_line}"
            f"{overall}{quiz_line}"
            f"{details}"
            "┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n"
            f"📚 الأنشطة المكتملة: <b>{ar_num(self.activities)}</b>\n"
            f"📝 الأسئلة المجابة: <b>{ar_num(self.questions)}</b>\n"
            f"{clock} الوقت المنقضي: <b>{format_duration(elapsed)}</b>\n"
            f"{self.speed_line(elapsed)}"
            f"{self.eta_line(elapsed)}"
            "━━━━━━━━━━━━━━\n"
            f"📌 <b>آخر التحديثات</b>\n{body}"
        )

    def keyboard(self):
        if not self.show_stop:
            return None
        return progress_kb(self.show_details, self.confirm_stop)

    async def flush(self, force=False):
        now = time.monotonic()
        if not force and not self.dirty:
            return
        if now < self.retry_not_before:
            return
        if not force and self.last_edit_at and now - self.last_edit_at < 1.5:
            return
        rendered = self.render()
        keyboard_key = (self.show_stop, self.show_details, self.confirm_stop)
        if rendered == self.last_rendered and keyboard_key == getattr(self, "_last_keyboard", None):
            self.dirty = False
            return
        try:
            await self.bot.edit_message_text(
                rendered,
                chat_id=self.chat_id,
                message_id=self.message_id,
                parse_mode=ParseMode.HTML,
                reply_markup=self.keyboard(),
            )
            self.dirty = False
            self.last_rendered = rendered
            self._last_keyboard = keyboard_key
            self.last_edit_at = time.monotonic()
            self.retry_not_before = 0.0
            self.edit_failures = 0
        except RetryAfter as exc:
            self.dirty = True
            self.retry_not_before = time.monotonic() + exc.retry_after
            log.warning("progress edit rate-limited; retrying after %.1fs", exc.retry_after)
        except BadRequest as exc:
            if "message is not modified" in str(exc).casefold():
                self.dirty = False
                self.last_rendered = rendered
                self._last_keyboard = keyboard_key
                self.last_edit_at = time.monotonic()
            else:
                self.dirty = False
                log.warning("progress edit rejected: %s", exc)
        except TelegramError as exc:
            self.dirty = True
            self.edit_failures += 1
            delay = min(30, 2 ** min(self.edit_failures - 1, 5))
            self.retry_not_before = time.monotonic() + delay
            log.warning("progress edit failed; retrying in %ss: %s", delay, exc)


async def ticker(progress, stop):
    """يحدّث البطاقة كل بضع ثوانٍ حتى لو لم يصل حدث جديد (مؤشر + ساعة + وقت)."""
    last_refresh = progress.t0
    while not stop.is_set():
        if time.monotonic() - last_refresh >= REFRESH_SECONDS:
            progress.dirty = True
            last_refresh = time.monotonic()
        try:
            await asyncio.wait_for(stop.wait(), timeout=1)
        except asyncio.TimeoutError:
            pass
        if progress.dirty:
            await progress.flush()
