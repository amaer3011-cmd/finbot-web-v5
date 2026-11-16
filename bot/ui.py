"""عناصر الواجهة: الأرقام العربية، أشرطة التقدم، الرسائل الجاهزة، ولوحات الأزرار."""
import html

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

_AR_DIGITS = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")
SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
CLOCKS = "🕛🕐🕑🕒🕓🕔🕕🕖🕗🕘🕙🕚"
_EIGHTHS = " ▏▎▍▌▋▊▉"


# ───────────────────────── أرقام ونِسَب وأشرطة ─────────────────────────

def ar_num(n):
    return f"{n:,}".replace(",", "٬").translate(_AR_DIGITS)


def ar_num_two_digits(n):
    return ar_num(n).rjust(2, "٠")


def ar_percent(done, total):
    if total <= 0:
        return "٠٪"
    percentage = max(0, min(100, int(done * 100 / total)))
    return ar_num(percentage) + "٪"


def progress_bar(done, total, width=10):
    """شريط بسيط ▰▱ (للأشرطة القصيرة مثل سؤال الاختبار)."""
    if width <= 0:
        return ""
    if total <= 0:
        return "▱" * width
    filled = max(0, min(width, round(width * done / total)))
    return "▰" * filled + "▱" * (width - filled)


def fine_bar(done, total, width=18):
    """شريط ناعم بدقة ثُمن خانة: ██████▌░░░░ — يتحرك مع كل خطوة صغيرة."""
    if width <= 0:
        return ""
    if total <= 0:
        return "░" * width
    ratio = max(0.0, min(1.0, done / total))
    eighths = int(round(ratio * width * 8))
    full, part = divmod(eighths, 8)
    bar = "█" * full
    if full < width:
        bar += _EIGHTHS[part] if part else "░"
    return bar.ljust(width, "░")[:width].replace(" ", "░")


def format_duration(seconds):
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return (
            f"{ar_num(hours)} س {ar_num_two_digits(minutes)} د "
            f"{ar_num_two_digits(seconds)} ث"
        )
    if minutes:
        return f"{ar_num(minutes)} د {ar_num_two_digits(seconds)} ث"
    return f"{ar_num(seconds)} ث"


def short(text, limit=28):
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


# ───────────────────────── رسائل جاهزة ─────────────────────────

def job_summary(questions, activities, elapsed):
    lines = [
        f"📝 الأسئلة المجابة: <b>{ar_num(questions)}</b>",
        f"📚 الأنشطة المكتملة: <b>{ar_num(activities)}</b>",
        f"⏱️ إجمالي الوقت: <b>{format_duration(elapsed)}</b>",
    ]
    if questions and elapsed >= 30:
        lines.append(f"🏎️ متوسط السرعة: <b>{ar_num(round(questions * 60 / elapsed))}</b> سؤال/دقيقة")
    return "\n".join(lines)


def completion_message(questions, activities, elapsed, last):
    return (
        "╭━━━━━━━━━━━━━━━━━━╮\n"
        "🎉 <b>اكتملت المهمة بنجاح</b>\n"
        "╰━━━━━━━━━━━━━━━━━━╯\n\n"
        f"<code>{fine_bar(1, 1, 18)}</code> <b>{ar_percent(1, 1)}</b>\n"
        "<i>لم يتبق نشاط مفتوح متاح للمتابعة وقت التشغيل.</i>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"{job_summary(questions, activities, elapsed)}\n"
        f"📍 آخر نشاط: <b>{html.escape(last or '—')}</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "<i>قد تظل أنشطة مقفولة بانتظار فتحها من المنصة.</i>"
    )


# ───────────────────────── الأزرار ─────────────────────────

def btn(text, style=None, **kw):
    """زر ملوّن (primary=أزرق، success=أخضر، danger=أحمر). لو نسخة المكتبة قديمة يرجع لزر عادي."""
    if style:
        try:
            return InlineKeyboardButton(text, style=style, **kw)
        except TypeError:
            pass
    return InlineKeyboardButton(text, **kw)


def main_menu(total, dev_username):
    return InlineKeyboardMarkup(
        [
            [btn("🚀 حل منصة الثقافة المالية", "success", callback_data="solve")],
            [btn("📊 حالة الحل الحالي", "primary", callback_data="status")],
            [
                btn("📖 طريقة الاستخدام", "primary", callback_data="help"),
                btn("🔒 الأمان والخصوصية", "primary", callback_data="privacy"),
            ],
            [btn(f"🤍 اللهم صلِّ على سيدنا محمد ﷺ  •  {ar_num(total)}", "primary", callback_data="salat")],
            [btn(f"Dev: @{dev_username}", url=f"https://t.me/{dev_username}")],
        ]
    )


def cancel_kb():
    return InlineKeyboardMarkup([[btn("❌ إلغاء", "danger", callback_data="cancel_flow")]])


def report_consent_kb():
    return InlineKeyboardMarkup(
        [
            [btn("✅ موافق لهذه المهمة", "success", callback_data="report_consent_yes")],
            [btn("لا، بدون تقرير", "primary", callback_data="report_consent_no")],
            [btn("❌ إلغاء", "danger", callback_data="cancel_flow")],
        ]
    )


def stop_kb():
    return InlineKeyboardMarkup([[btn("⛔ إيقاف الحل", "danger", callback_data="stop")]])


def progress_kb(show_details=False, confirm_stop=False):
    """أزرار رسالة التقدم الحية: تحديث فوري، إظهار/إخفاء الوحدات، إيقاف بتأكيد."""
    if confirm_stop:
        return InlineKeyboardMarkup(
            [
                [
                    btn("✅ أيوه، أوقف", "danger", callback_data="stop_yes"),
                    btn("↩️ كمّل الحل", "success", callback_data="stop_no"),
                ]
            ]
        )
    return InlineKeyboardMarkup(
        [
            [
                btn("🔄 تحديث الآن", "primary", callback_data="p_refresh"),
                btn("🔼 إخفاء الوحدات" if show_details else "📋 تفاصيل الوحدات", "primary", callback_data="p_details"),
            ],
            [btn("⛔ إيقاف الحل", "danger", callback_data="stop")],
        ]
    )


def after_kb():
    return InlineKeyboardMarkup(
        [
            [btn("🔁 تشغيل تاني", "success", callback_data="solve")],
            [btn("🏠 القائمة الرئيسية", "primary", callback_data="menu")],
        ]
    )
