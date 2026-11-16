"""بوت تيليجرام — حل منصة الثقافة المالية (المرحلة 1: واجهة الطالب)."""
import asyncio
import html
import io
import logging
import os
import random
import threading
import time
from pathlib import Path

from dotenv import load_dotenv
from telegram import InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, RetryAfter, TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

load_dotenv()

import core
import db
from progress import Progress, ticker
from ui import (  # noqa: F401  (بعضها يُعاد تصديره للاختبارات)
    after_kb,
    ar_num,
    ar_percent,
    btn,
    cancel_kb,
    completion_message,
    format_duration,
    job_summary,
    progress_bar,
    report_consent_kb,
    stop_kb,
)
from ui import main_menu as _main_menu
from verses import VERSES

BASE = Path(__file__).resolve().parent
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
DEV_USERNAME = os.getenv("DEV_USERNAME", "A7med19_7").lstrip("@")


def _int_setting(name, default, minimum):
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer.") from exc
    if value < minimum:
        raise RuntimeError(f"{name} must be at least {minimum}.")
    return value


MAX_JOBS = _int_setting("MAX_JOBS", 1, 1)
MAX_QUEUE = _int_setting("MAX_QUEUE", 10, 0)
JOB_TIMEOUT_MIN = _int_setting("JOB_TIMEOUT_MIN", 240, 1)
def _optional_int(name):
    """معرّف اختياري: فارغ أو placeholder أو غير رقمي يعني (غير مفعّل)."""
    raw = os.getenv(name, "").strip()
    try:
        return int(raw)
    except ValueError:
        return None


OWNER_CHAT_ID = _optional_int("OWNER_CHAT_ID")  # بدونه تُعطَّل تقارير الأعطال
ANSWERS_FILE = BASE / os.getenv("ANSWERS_FILE", "answers.txt")
ASSETS_DIR = BASE / "assets"
WELCOME_IMAGES = sorted(ASSETS_DIR.glob("welcome_*.jpg"))  # يتولّد بـ make_assets.py
_FILE_IDS = {}  # بعد أول رفع نبعت الصورة بالـ file_id (أسرع وأخف)

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
# httpx بيكتب رابط الـ API (فيه التوكن) في اللوج — نسكّته
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("finbot")

def queue_full(app):
    """Reject work beyond the combined running-job and waiting-queue capacity."""
    data = app.bot_data
    return len(data["cancels"]) >= MAX_JOBS + MAX_QUEUE


async def acquire_job_slot(semaphore, cancel, poll_interval=0.25):
    """Wait for a worker slot without making queued jobs unresponsive to stop."""
    while not cancel.is_set():
        try:
            acquired = await asyncio.wait_for(
                semaphore.acquire(),
                timeout=poll_interval,
            )
        except asyncio.TimeoutError:
            continue
        if cancel.is_set():
            semaphore.release()
            return False
        return acquired
    return False


QUEUE_FULL_TEXT = "🚦 البوت مشغول دلوقتي والطابور ممتلئ. جرّب تاني بعد شوية 🌿"


# ───────────────────────── الرسائل الأساسية ─────────────────────────

def main_menu(total):
    return _main_menu(total, DEV_USERNAME)


async def send_welcome(chat, name):
    idx = random.randrange(len(VERSES))
    v = VERSES[idx]
    caption = (
        f"✦ ━━━━━━━━━━━━ ✦\n"
        f"<b>أهلاً بيك يا {html.escape(name)}</b>\n\n"
        f"<blockquote>{html.escape(v['shatr1'])}\n{html.escape(v['shatr2'])}\n"
        f"<i>{html.escape(v['poet'])}</i></blockquote>\n\n"
        "⏱️ وقتك أغلى من إنك تقعد تدوس «التالي» ساعات.\n"
        "اضغط الزر اللي تحت وسيب الباقي عليّ 👇\n\n"
        "🤍 <b>صلِّ على النبي ﷺ</b>"
    )
    markup = main_menu(db.get_salawat())
    image = WELCOME_IMAGES[idx] if idx < len(WELCOME_IMAGES) else None
    if image:
        photo = _FILE_IDS.get(image.name) or image.read_bytes()
        try:
            sent = await chat.send_photo(photo, caption=caption, parse_mode=ParseMode.HTML, reply_markup=markup)
            _FILE_IDS[image.name] = sent.photo[-1].file_id
            return
        except TelegramError as exc:
            log.warning("welcome photo failed (%s) — fallback to text", exc)
            _FILE_IDS.pop(image.name, None)
    await chat.send_message(caption, parse_mode=ParseMode.HTML, reply_markup=markup)


async def on_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db.touch_user(user.id, user.full_name)
    ctx.user_data.clear()
    await send_welcome(update.effective_chat, user.first_name or "يا غالي")


async def on_cancel_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    await update.message.reply_text("تمام، اتلغى. اكتب /start عشان ترجع للقائمة.")


HELP_TEXT = (
    "📖 <b>طريقة الاستخدام</b>\n<i>أربع خطوات وخلاص</i>\n\n"
    "١) اضغط «حل منصة الثقافة المالية».\n"
    "٢) ابعت كود الدخول بتاعك (بيتحفظ عشان متكتبوش كل مرة).\n"
    "٣) ابعت الباسورد.\n"
    "٤) البوت يدخل حسابك ويحل الدروس بالترتيب، وبيبعتلك تقدّمه في رسالة واحدة بتتحدّث.\n\n"
    "• الدروس اللي كنت مخلصها قبل كده بيعدّي عليها.\n"
    "• تقدر توقف الحل في أي وقت بزر «إيقاف».\n"
    "• ممكن تقفل تيليجرام — الحل بيكمّل على السيرفر."
)

PRIVACY_TEXT = (
    "🔒 <b>الأمان والخصوصية</b>\n<i>شفافية كاملة</i>\n\n"
    "• <b>الكود</b> بيتحفظ عشان متكتبوش كل مرة.\n"
    "• <b>الباسورد</b> بيتستخدم مرة واحدة بس لتسجيل الدخول، ومبيتكتبش في أي ملف أو قاعدة بيانات، "
    "وبيتشال من الذاكرة لما الحل يخلص. والبوت بيمسح رسالتك اللي فيها الباسورد أول ما يستلمها.\n"
    "• مع ذلك الباسورد بيعدّي على سيرفر البوت أثناء الدخول، فلو عايز تطمّن زيادة غيّره بعد ما تخلص.\n"
    "• قبل كل مهمة، تقدر توافق اختياريًا على إرسال تقرير عطل ولقطة شاشة منقّحة لمطوّر البوت. "
    "تُحجب المنطقة العلوية وحقول الإدخال، لكن قد يظهر محتوى المنصة. رفضك لا يمنع تشغيل الحل."
)


async def on_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.message.reply_html(HELP_TEXT)


async def on_privacy(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.message.reply_html(PRIVACY_TEXT)


async def on_help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_html(HELP_TEXT)


async def on_error(update, ctx: ContextTypes.DEFAULT_TYPE):
    error = ctx.error
    if isinstance(error, BadRequest) and "query is too old" in str(error).casefold():
        log.info("ignored expired Telegram callback query")
        return
    log.error("unhandled error", exc_info=error)


async def on_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await send_welcome(q.message.chat, q.from_user.first_name or "يا غالي")


# ───────────────────────── الصلاة على النبي ─────────────────────────

async def on_salat(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    now = time.monotonic()
    if now - ctx.user_data.get("last_salat", 0) < 0.35:  # حماية من الضغط السريع جدًا
        await q.answer()
        return
    ctx.user_data["last_salat"] = now
    total = db.add_salawat()
    await q.answer("اللهم صلِّ وسلِّم وبارك على سيدنا محمد ﷺ")
    try:
        await q.edit_message_reply_markup(main_menu(total))
    except (BadRequest, RetryAfter):
        pass


# ───────────────────────── تدفق الدخول ─────────────────────────

async def on_solve(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = q.from_user.id
    await q.answer()
    db.touch_user(uid, q.from_user.full_name)
    if uid in ctx.application.bot_data["cancels"]:
        await q.message.reply_text("⏳ في حل شغّال لحسابك دلوقتي. استنى لما يخلص أو اضغط «إيقاف».")
        return
    if queue_full(ctx.application):  # قبل ما نطلب الباسورد أصلاً
        await q.message.reply_text(QUEUE_FULL_TEXT)
        return
    ctx.user_data.pop("report_consent", None)
    if OWNER_CHAT_ID is None:  # لا مطوّر مُعرَّف = لا تقارير، فلا داعي لسؤال الموافقة
        ctx.user_data["report_consent"] = False
        await continue_solve_flow(q.message, ctx)
        return
    ctx.user_data["state"] = "await_report_consent"
    await q.message.reply_html(
        "🔎 <b>تقارير الأعطال اختيارية</b>\n\n"
        "لو وافقت لهذه المهمة، عند حدوث مشكلة فقط سأرسل لمطوّر البوت "
        f"(Telegram ID: <code>{OWNER_CHAT_ID}</code>) تقريرًا تقنيًا ولقطة شاشة.\n"
        "سأحجب الشريط العلوي وحقول الإدخال، لكن قد يظهر محتوى المنصة في الصورة. "
        "لن أرسل كود الدخول أو كلمة المرور. الرفض لا يمنع تشغيل الحل.",
        reply_markup=report_consent_kb(),
    )


async def continue_solve_flow(message, ctx):
    uid = message.chat.id
    saved = db.get_username(uid)
    if saved:
        kb = InlineKeyboardMarkup(
            [
                [btn("✅ استخدم الكود ده", "success", callback_data="use_saved")],
                [btn("✏️ كود تاني", "primary", callback_data="new_user")],
            ]
        )
        await message.reply_html(f"عندي كود محفوظ لحسابك:\n<code>{html.escape(saved)}</code>\n\nتستخدمه؟", reply_markup=kb)
    else:
        await ask_username(message, ctx)


async def on_report_consent(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.message.chat.type != "private" or q.from_user.id != q.message.chat.id:
        return
    ctx.user_data.pop("state", None)
    ctx.user_data["report_consent"] = q.data == "report_consent_yes"
    await q.message.reply_text(
        "✅ هنبعت تقرير منقّح عند حدوث مشكلة فقط."
        if ctx.user_data["report_consent"]
        else "تمام، مش هنبعت أي تقرير أو لقطة شاشة."
    )
    await continue_solve_flow(q.message, ctx)


async def ask_username(message, ctx):
    ctx.user_data["state"] = "await_user"
    await message.reply_html(
        "ابعتلي <b>كود الدخول</b> (Login ID) بتاع حسابك على المنصة 👇", reply_markup=cancel_kb()
    )


async def ask_password(message, ctx, username):
    ctx.user_data["state"] = "await_pass"
    ctx.user_data["username"] = username
    await message.reply_html(
        f"تمام ✅ الكود: <code>{html.escape(username)}</code>\n\n"
        "دلوقتي ابعت <b>الباسورد</b> 🔑\n"
        "<i>هتتمسح رسالتك أول ما أستلمها.</i>",
        reply_markup=cancel_kb(),
    )


async def on_new_user(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await ask_username(q.message, ctx)


async def on_use_saved(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    saved = db.get_username(q.from_user.id)
    if not saved:
        await ask_username(q.message, ctx)
        return
    await ask_password(q.message, ctx, saved)


async def on_cancel_flow(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    ctx.user_data.pop("state", None)
    ctx.user_data.pop("username", None)
    ctx.user_data.pop("report_consent", None)
    await q.answer("اتلغى")
    try:
        await q.message.delete()
    except TelegramError:
        pass


async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    state = ctx.user_data.get("state")
    text = (update.message.text or "").strip()
    uid = update.effective_user.id

    if state == "await_user":
        if not (2 <= len(text) <= 64) or any(c.isspace() for c in text):
            await update.message.reply_text("الكود شكله مش مظبوط (من غير مسافات). ابعته تاني.")
            return
        db.save_username(uid, text)
        await ask_password(update.message, ctx, text)

    elif state == "await_pass":
        password = update.message.text or ""
        try:  # امسح رسالة الباسورد فورًا
            await update.message.delete()
        except TelegramError:
            pass
        username = ctx.user_data.pop("username", None) or db.get_username(uid)
        ctx.user_data.pop("state", None)
        if not username or not password.strip():
            await update.effective_chat.send_message("حصلت مشكلة، ابدأ من جديد بـ /start")
            return
        ctx.application.create_task(
            run_student_job(
                ctx.application,
                update.effective_chat.id,
                uid,
                username,
                password,
                ctx.user_data.pop("report_consent", False),
            ),
            update=update,
        )
        password = None

    elif state == "await_report_consent":
        await update.message.reply_text("اختار من الأزرار قبل المتابعة.", reply_markup=report_consent_kb())

    else:
        await update.message.reply_text("اكتب /start عشان تفتح القائمة 🌿")


# ───────────────────────── تشغيل الحل + عرض التقدّم ─────────────────────────

async def send_owner_diagnostic(app, report):
    if OWNER_CHAT_ID is None:
        return
    details = (
        f"🚨 تقرير عطل #{report['id']}\n"
        f"الحالة: {report['status']}\n"
        f"الوقت: {report['timestamp']}\n"
        f"الموقع: {report['page']}\n"
        f"النشاط: {report['activity']}\n"
        f"التشخيص: {report['error']}"
    )[:4000]
    try:
        await app.bot.send_message(chat_id=OWNER_CHAT_ID, text=details)
    except TelegramError as exc:
        log.warning("owner diagnostic message failed (%s)", type(exc).__name__)

    screenshot = report.get("screenshot")
    if screenshot:
        image = io.BytesIO(screenshot)
        image.name = f"diagnostic-{report['id']}.png"
        try:
            await app.bot.send_photo(
                chat_id=OWNER_CHAT_ID,
                photo=image,
                caption="لقطة منقّحة: الشريط العلوي وحقول الإدخال محجوبة.",
            )
        except TelegramError as exc:
            log.warning("owner diagnostic screenshot failed (%s)", type(exc).__name__)
    else:
        log.info("diagnostic report %s has no screenshot", report["id"])


async def run_student_job(app, chat_id, uid, username, password, report_consent=False):
    data = app.bot_data
    if uid in data["cancels"]:
        return
    if queue_full(app):  # تأكيد تاني احتياطي (ممكن الطابور يتملّى وهو بيكتب الباسورد)
        password = None
        await app.bot.send_message(chat_id, QUEUE_FULL_TEXT, reply_markup=after_kb())
        return

    cancel = threading.Event()
    timed_out = threading.Event()
    data["cancels"][uid] = cancel
    queued = len(data["cancels"]) > MAX_JOBS
    queue_reserved = queued
    if queue_reserved:
        data["waiting"] += 1

    started = time.time()
    loop = asyncio.get_running_loop()
    progress = None
    stop_ticker = None
    ticker_task = None
    timer = None
    sem = data["sem"]
    acquired = False
    result = {"status": "error", "error": "unknown"}

    def hook(kind, text):
        if kind == "diagnostic":
            def schedule_report():
                app.create_task(send_owner_diagnostic(app, text))

            loop.call_soon_threadsafe(schedule_report)
            return
        if kind == "debug":
            # تشخيص للمطوّر في لوج Railway (من غير كود الطالب أو الباسورد)
            safe = str(text)
            for secret in (password, username):
                if secret:
                    safe = safe.replace(secret, "***")
            log.info("solver uid=%s: %s", uid, safe[:600])
            return
        loop.call_soon_threadsafe(progress.handle, kind, text)

    def on_timeout():
        timed_out.set()
        cancel.set()

    try:
        msg = await app.bot.send_message(
            chat_id,
            "⏳ جاري التجهيز...",
            reply_markup=stop_kb(),
        )
        progress = Progress(app.bot, chat_id, msg.message_id)
        data.setdefault("progress", {})[uid] = progress
        stop_ticker = asyncio.Event()
        ticker_task = asyncio.create_task(ticker(progress, stop_ticker))

        if queued:
            progress.handle(
                "phase",
                f"في انتظار مكان في الطابور (ترتيبك {ar_num(data['waiting'])})",
            )
        acquired = await acquire_job_slot(sem, cancel)
        if queue_reserved:
            data["waiting"] -= 1
            queue_reserved = False

        if not acquired:
            result = {"status": "stopped"}
        else:
            try:
                if cancel.is_set():
                    result = {"status": "stopped"}
                else:
                    progress.handle("phase", "بيسجّل الدخول")
                    timer = loop.call_later(JOB_TIMEOUT_MIN * 60, on_timeout)
                    result = await asyncio.to_thread(
                        core.run_job,
                        username,
                        password,
                        data["entries"],
                        hook,
                        cancel,
                        True,
                        report_consent,
                    )
            finally:
                sem.release()
                acquired = False
    except Exception as exc:  # noqa: BLE001
        log.error("job crashed uid=%s (%s)", uid, type(exc).__name__)
        result = {"status": "error", "error": "حصل خطأ غير متوقع في البوت."}
    finally:
        password = None
        if timer:
            timer.cancel()
        if acquired:
            sem.release()
        if queue_reserved:
            data["waiting"] -= 1
        if stop_ticker:
            stop_ticker.set()
        if ticker_task:
            try:
                await ticker_task
            except Exception as exc:  # noqa: BLE001
                log.warning("progress ticker stopped unexpectedly (%s)", type(exc).__name__)
        if data["cancels"].get(uid) is cancel:
            data["cancels"].pop(uid, None)
        if progress is not None and data.get("progress", {}).get(uid) is progress:
            data["progress"].pop(uid, None)

    if progress is None:
        try:
            db.log_job(uid, started, result.get("status", "error"), 0, 0, result.get("error", ""))
        except Exception as exc:  # noqa: BLE001
            log.error("could not record failed job uid=%s (%s)", uid, type(exc).__name__)
        return

    status = result.get("status")
    if timed_out.is_set():
        status = "timeout"
    last = result.get("last") or "—"
    elapsed = time.monotonic() - progress.t0
    summary = job_summary(progress.questions, progress.activities, elapsed)

    if status == "finished":
        progress.header = "🎉 <b>اكتملت الأنشطة المتاحة</b>"
        final = completion_message(
            progress.questions, progress.activities, elapsed, last
        )
    elif status == "tests_pending":
        test_name = html.escape((result.get("test") or "")[:120])
        if result.get("reason") == "failed":
            progress.header = "📝 <b>الاختبار لم يُجتز</b>"
            final = (
                f"📝 <b>المنصة أكدت عدم اجتياز الاختبار:</b>\n"
                f"{('• ' + test_name) if test_name else ''}\n\n{summary}\n\n"
                "لم أسجّل الاختبار مكتملًا. راجع المحاولة في المنصة ثم شغّل الحل مجددًا."
            )
        else:
            progress.header = "📝 <b>وقفت عند اختبار — مفيش إجابات مطابقة</b>"
            final = (
                f"📝 <b>وصلت لاختبار مش موجود في بنك الإجابات:</b>\n"
                f"{('• ' + test_name) if test_name else ''}\n\n{summary}\n\n"
                "حلّه بنفسك، وبعدين دوس «حل الثقافة المالية» تاني والبوت يكمّل اللي بعده."
            )
    elif status == "login_failed":
        progress.header = "❌ <b>فشل تسجيل الدخول</b>"
        final = "❌ الكود أو الباسورد غلط، أو الحساب مش متاح دلوقتي.\nراجع بياناتك وحاول تاني."
    elif status == "stopped":
        progress.header = "⛔ <b>اتوقّف الحل</b>"
        final = f"⛔ وقفت الحل.\n\n{summary}"
    elif status == "stuck":
        progress.header = "🧩 <b>توقّف الحل بأمان</b>"
        err = html.escape((result.get("error") or "لم يتوفر سبب إضافي.")[:700])
        final = (
            "🧩 <b>لم أستطع تأكيد الخطوة التالية، لذلك لم أخمّن أو أسجّل إجابة غير مؤكدة.</b>\n"
            f"<blockquote>{err}</blockquote>\n\n{summary}\n"
            f"📍 آخر نشاط: <b>{html.escape(last)}</b>\n\n"
            "راجع هذا العنصر في المنصة، ثم شغّل الحل مجددًا للمتابعة."
        )
    elif status == "timeout":
        progress.header = "⌛ <b>خلصت المدة القصوى</b>"
        final = f"⌛ الحل خد وقت أطول من المسموح فوقفته.\n\n{summary}\nتقدر تشغّله تاني وهيكمّل من حيث وقف."
    else:
        progress.header = "⚠️ <b>الحل وقف بسبب مشكلة</b>"
        err = html.escape((result.get("error") or "")[:500])
        final = f"⚠️ الحل وقف بسبب مشكلة:\n<code>{err}</code>\n\n{summary}\nآخر نشاط: {html.escape(last)}"
        log.warning("job error uid=%s: %s", uid, result.get("error"))

    progress.show_stop = False
    progress.confirm_stop = False
    progress.dirty = True
    await progress.flush(force=True)
    await app.bot.send_message(chat_id, final, parse_mode=ParseMode.HTML, reply_markup=after_kb())
    db.log_job(uid, started, status, progress.questions, progress.activities, result.get("error", "") or "")


def _live_progress(ctx, uid):
    return ctx.application.bot_data.get("progress", {}).get(uid)


async def on_stop(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """أول ضغطة على «إيقاف» تطلب تأكيدًا حتى لا يضيع الحل بلمسة بالغلط."""
    q = update.callback_query
    uid = q.from_user.id
    event = ctx.application.bot_data["cancels"].get(uid)
    if not event:
        await q.answer("مفيش حل شغّال.")
        return
    progress = _live_progress(ctx, uid)
    if progress is None:  # لم تُنشأ بطاقة بعد: أوقف مباشرة
        event.set()
        await q.answer("هوقف بعد الخطوة الحالية…")
        return
    progress.confirm_stop = True
    await q.answer("متأكد إنك عايز توقف؟")
    await progress.flush(force=True)


async def on_stop_yes(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = q.from_user.id
    event = ctx.application.bot_data["cancels"].get(uid)
    progress = _live_progress(ctx, uid)
    if progress is not None:
        progress.confirm_stop = False
    if not event:
        await q.answer("مفيش حل شغّال.")
        return
    event.set()
    await q.answer("هوقف بعد الخطوة الحالية…")
    if progress is not None:
        progress.handle("phase", "بيوقف بعد الخطوة الحالية")
        await progress.flush(force=True)


async def on_stop_no(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    progress = _live_progress(ctx, q.from_user.id)
    await q.answer("تمام، كمّلنا ✅")
    if progress is not None:
        progress.confirm_stop = False
        await progress.flush(force=True)


async def on_progress_refresh(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    progress = _live_progress(ctx, q.from_user.id)
    if progress is None:
        await q.answer("مفيش حل شغّال.")
        return
    await q.answer("اتحدّث 🔄")
    progress.dirty = True
    await progress.flush(force=True)


async def on_progress_details(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    progress = _live_progress(ctx, q.from_user.id)
    if progress is None:
        await q.answer("مفيش حل شغّال.")
        return
    progress.show_details = not progress.show_details
    await q.answer("تفاصيل الوحدات " + ("ظهرت 📋" if progress.show_details else "اختفت 🔼"))
    progress.dirty = True
    await progress.flush(force=True)


async def on_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """نافذة منبثقة بملخص حالة الحل الجاري من القائمة الرئيسية."""
    q = update.callback_query
    progress = _live_progress(ctx, q.from_user.id)
    if progress is None:
        await q.answer("مفيش حل شغّال دلوقتي. دوس «حل منصة الثقافة المالية» وابدأ 🚀", show_alert=True)
        return
    await q.answer(progress.snapshot(), show_alert=True)


async def post_init(app: Application):
    db.init(os.getenv("DB_PATH", str(BASE / "bot.db")))
    app.bot_data["sem"] = asyncio.Semaphore(MAX_JOBS)
    app.bot_data["cancels"] = {}
    app.bot_data["progress"] = {}
    app.bot_data["waiting"] = 0
    app.bot_data["entries"] = core.parse_answer_file(ANSWERS_FILE)
    log.info("loaded %d answers", len(app.bot_data["entries"]))


def build_app():
    app = Application.builder().token(BOT_TOKEN).concurrent_updates(True).post_init(post_init).build()
    private = filters.ChatType.PRIVATE
    app.add_handler(CommandHandler("start", on_start, filters=private))
    app.add_handler(CommandHandler("menu", on_start, filters=private))
    app.add_handler(CommandHandler("cancel", on_cancel_cmd, filters=private))
    app.add_handler(CommandHandler("help", on_help_cmd, filters=private))
    app.add_error_handler(on_error)
    for pattern, handler in [
        ("^solve$", on_solve),
        ("^use_saved$", on_use_saved),
        ("^new_user$", on_new_user),
        ("^cancel_flow$", on_cancel_flow),
        ("^report_consent_(yes|no)$", on_report_consent),
        ("^stop$", on_stop),
        ("^stop_yes$", on_stop_yes),
        ("^stop_no$", on_stop_no),
        ("^p_refresh$", on_progress_refresh),
        ("^p_details$", on_progress_details),
        ("^status$", on_status),
        ("^help$", on_help),
        ("^privacy$", on_privacy),
        ("^salat$", on_salat),
        ("^menu$", on_menu),
    ]:
        app.add_handler(CallbackQueryHandler(handler, pattern=pattern))
    app.add_handler(MessageHandler(private & filters.TEXT & ~filters.COMMAND, on_text))
    return app


if __name__ == "__main__":
    if not BOT_TOKEN:
        raise SystemExit("حط BOT_TOKEN في ملف .env")
    if OWNER_CHAT_ID is None:
        log.warning("OWNER_CHAT_ID غير مضبوط — تقارير الأعطال معطّلة.")
    build_app().run_polling()
