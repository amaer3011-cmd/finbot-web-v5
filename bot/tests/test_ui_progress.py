"""اختبارات الواجهة التفاعلية وبطاقة التقدم (بلا شبكة)."""
import asyncio
import sys
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bot  # noqa: E402
import progress as progress_mod  # noqa: E402
import ui  # noqa: E402


def _buttons(markup):
    return [b.callback_data for row in markup.inline_keyboard for b in row if b.callback_data]


def test_fine_bar_has_fixed_width_and_is_monotonic():
    widths = {len(ui.fine_bar(i, 40, 18)) for i in range(0, 41)}
    assert widths == {18}
    assert ui.fine_bar(0, 40, 18) == "░" * 18
    assert ui.fine_bar(40, 40, 18) == "█" * 18
    assert ui.fine_bar(5, 0, 6) == "░" * 6
    filled = [ui.fine_bar(i, 40, 18).count("█") for i in range(41)]
    assert filled == sorted(filled)


def test_progress_keyboard_switches_between_normal_and_confirm():
    normal = _buttons(ui.progress_kb())
    assert normal == ["p_refresh", "p_details", "stop"]
    assert _buttons(ui.progress_kb(confirm_stop=True)) == ["stop_yes", "stop_no"]
    labels = [b.text for row in ui.progress_kb(show_details=True).inline_keyboard for b in row]
    assert any("إخفاء" in label for label in labels)


def test_unit_rows_spread_finished_activities_in_order_and_toggle_details():
    p = progress_mod.Progress(None, 1, 1)
    p.handle("plan_todo", "الأولى|4|4")
    p.handle("plan_todo", "الثانية|1|3")
    p.handle("plan_todo", "الثالثة|0|2")
    p.handle("done", "نشاط ١")
    p.handle("done", "نشاط ٢")
    p.handle("done", "نشاط ٣")

    assert p.unit_rows() == [("الأولى", 4, 4), ("الثانية", 3, 3), ("الثالثة", 1, 2)]
    assert "الوحدات" not in p.render()

    p.show_details = True
    rendered = p.render()
    assert "🗂 <b>الوحدات</b>" in rendered
    assert "✅ الأولى" in rendered and "✅ الثانية" in rendered
    assert "🔄 الثالثة" in rendered


def test_details_block_caps_rows_and_escapes_names():
    p = progress_mod.Progress(None, 1, 1)
    for i in range(15):
        p.handle("plan_todo", f"وحدة <{i}>|0|2")
    p.show_details = True
    rendered = p.render()
    assert "&lt;0&gt;" in rendered and "<0>" not in rendered
    assert "و٣ وحدات أخرى" in rendered
    assert len(rendered) < 4000


def test_speed_line_needs_enough_data_and_header_spinner_only_while_running():
    p = progress_mod.Progress(None, 1, 1)
    assert p.speed_line(10) == ""
    p.questions = 30
    p.t0 = time.monotonic() - 60
    assert "٣٠</b> سؤال/دقيقة" in p.speed_line(60)
    running = p.render().splitlines()[0]
    assert any(ch in running for ch in ui.SPINNER)
    p.show_stop = False
    assert not any(ch in p.render().splitlines()[0] for ch in ui.SPINNER)
    assert p.keyboard() is None


def test_snapshot_fits_telegram_popup_limit():
    p = progress_mod.Progress(None, 1, 1)
    p.handle("plan_todo", "وحدة|2|8")
    p.handle("open", "ا" * 300)
    assert len(p.snapshot()) <= 200


class _Query:
    def __init__(self, uid):
        self.from_user = SimpleNamespace(id=uid)
        self.answers = []

    async def answer(self, text=None, show_alert=False):
        self.answers.append((text, show_alert))


def _ctx(cancels=None, progress=None):
    data = {"cancels": cancels or {}, "progress": progress or {}}
    return SimpleNamespace(application=SimpleNamespace(bot_data=data))


class _FlushRecorder(progress_mod.Progress):
    def __init__(self):
        super().__init__(None, 1, 1)
        self.flushes = 0

    async def flush(self, force=False):
        self.flushes += 1


def test_stop_needs_confirmation_and_yes_sets_cancel_event():
    import threading

    event = threading.Event()
    prog = _FlushRecorder()
    ctx = _ctx({7: event}, {7: prog})
    q = _Query(7)
    update = SimpleNamespace(callback_query=q)

    asyncio.run(bot.on_stop(update, ctx))
    assert prog.confirm_stop is True and not event.is_set()

    asyncio.run(bot.on_stop_no(update, ctx))
    assert prog.confirm_stop is False and not event.is_set()

    asyncio.run(bot.on_stop(update, ctx))
    asyncio.run(bot.on_stop_yes(update, ctx))
    assert event.is_set() and prog.confirm_stop is False


def test_stop_without_progress_card_cancels_directly_and_idle_answers_politely():
    import threading

    event = threading.Event()
    q = _Query(8)
    asyncio.run(bot.on_stop(SimpleNamespace(callback_query=q), _ctx({8: event})))
    assert event.is_set()

    idle = _Query(9)
    asyncio.run(bot.on_stop(SimpleNamespace(callback_query=idle), _ctx()))
    assert idle.answers[0][0] == "مفيش حل شغّال."


def test_details_refresh_and_status_handlers():
    prog = _FlushRecorder()
    prog.handle("plan_todo", "وحدة|1|4")
    ctx = _ctx({}, {5: prog})
    q = _Query(5)
    update = SimpleNamespace(callback_query=q)

    asyncio.run(bot.on_progress_details(update, ctx))
    assert prog.show_details is True and prog.flushes == 1
    asyncio.run(bot.on_progress_details(update, ctx))
    assert prog.show_details is False

    asyncio.run(bot.on_progress_refresh(update, ctx))
    assert prog.flushes == 3

    asyncio.run(bot.on_status(update, ctx))
    text, alert = q.answers[-1]
    assert alert is True and "٢٥٪" in text

    nobody = _Query(6)
    asyncio.run(bot.on_status(SimpleNamespace(callback_query=nobody), _ctx()))
    assert nobody.answers[0][1] is True


def test_owner_chat_id_is_optional(monkeypatch):
    monkeypatch.setenv("TEST_OPTIONAL_ID", "")
    assert bot._optional_int("TEST_OPTIONAL_ID") is None
    monkeypatch.setenv("TEST_OPTIONAL_ID", "??_???")
    assert bot._optional_int("TEST_OPTIONAL_ID") is None
    monkeypatch.setenv("TEST_OPTIONAL_ID", " 12345 ")
    assert bot._optional_int("TEST_OPTIONAL_ID") == 12345


def test_diagnostics_are_skipped_without_owner(monkeypatch):
    sent = []

    class _Bot:
        async def send_message(self, **kwargs):
            sent.append(kwargs)

        async def send_photo(self, **kwargs):
            sent.append(kwargs)

    app = SimpleNamespace(bot=_Bot())
    monkeypatch.setattr(bot, "OWNER_CHAT_ID", None)
    asyncio.run(bot.send_owner_diagnostic(app, {"id": "x", "status": "s", "timestamp": "t"}))
    assert sent == []


def test_main_menu_has_status_button_and_completion_message_shows_speed():
    assert "status" in _buttons(bot.main_menu(3))
    message = ui.completion_message(60, 4, 120, "x")
    assert "١٠٠٪" in message
    assert ui.job_summary(60, 4, 120).count("سؤال/دقيقة") == 1
