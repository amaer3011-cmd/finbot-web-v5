"""
pw_speed.py — أدوات Playwright اختيارية (قياس + استقرار) بأقل تعديل على core

⚠️ تصحيح مهم: كاتب البوت (CHANGELOG v4.0/v4.1) قاس إن حجب الصور والخطوط
   (BLOCK_ASSETS / context.route) بيعطّل الـ HTTP cache وممكن يعلّق شاشة تحميل المنصة،
   فسابه مقفول افتراضياً. لذلك كل خيارات الحجب هنا **مقفولة افتراضياً** وما اتجرّبتش على المنصة الحقيقية.
   فعّلها واحدة واحدة وقارن النتيجة (الدخول بيخلص؟ الحل بيكمل؟) قبل ما تعتمد عليها.

الموجود:
  1) profile_sleeps()  — يقيس كام ثانية ضاعت في sleep/wait_for_timeout وفي أنهي سطر (آمن، قراءة بس)
  2) patch_launch_args() — يضيف flags استقرار للمتصفح (مهم في Docker/Railway: --disable-dev-shm-usage)
  3) apply_speedups(context, ...) — اختياري، كله مقفول افتراضياً:
        block_assets=True     حجب الصور/الفيديو/الخطوط   (الكاتب لقاها بتعلّق المنصة — جرّبها بحذر)
        block_trackers=True   حجب سكربتات التتبع (analytics) فقط
        block_css=True        حجب CSS (خطر: يكشف عناصر مخفية ويلخبط الـ selectors)
        kill_animations=True  صفّر الـ animations/transitions (خطر: transitionend مش بيتبعت لو المدة 0)

القياس بدون أي تعديل في core:  PROFILE_SLEEPS=1 في الـ .env  ← main.py بيطبع تقرير بعد كل حل.
"""
import asyncio
import collections
import os
import sys
import threading
import time

# ── خيارات التشغيل ───────────────────────────────────────────────────

LAUNCH_ARGS = ["--disable-dev-shm-usage"]   # الوحيد المهم في Docker (/dev/shm صغير). البوت (v4.1) بيحطه أصلاً.


def _merge_args(existing, extra):
    args = list(existing or [])
    have = {a.split("=")[0] for a in args}
    args += [a for a in extra if a.split("=")[0] not in have]
    return args


def patch_launch_args(extra=None):
    """
    يضيف LAUNCH_ARGS لأي `chromium.launch(...)` في البرنامج كله، من غير تعديل core.py.
    لازم فقط لو الـ core بتاعك مش بيمرّر --disable-dev-shm-usage بنفسه (البوت v4.1 بيمرّره، فمش محتاجه).
    آمن لو اتنادى أكتر من مرة.
    """
    extra = list(extra or LAUNCH_ARGS)
    n = 0
    for mod in ("playwright.sync_api", "playwright.async_api"):
        try:
            cls = __import__(mod, fromlist=["BrowserType"]).BrowserType
        except Exception:
            continue
        orig = cls.launch
        if getattr(orig, "_pw_speed_patched", False):
            continue
        if asyncio.iscoroutinefunction(orig):
            async def wrapped(self, *a, __o=orig, **kw):
                kw["args"] = _merge_args(kw.get("args"), extra)
                return await __o(self, *a, **kw)
        else:
            def wrapped(self, *a, __o=orig, **kw):
                kw["args"] = _merge_args(kw.get("args"), extra)
                return __o(self, *a, **kw)
        wrapped._pw_speed_patched = True
        cls.launch = wrapped
        n += 1
    return n


# ── الحجب ────────────────────────────────────────────────────────────

_ASSET_TYPES = {"image", "media", "font"}
_TRACKER_HOSTS = (
    "google-analytics.com", "googletagmanager.com", "doubleclick.net",
    "facebook.net", "facebook.com/tr", "hotjar.com", "clarity.ms",
)

KILL_ANIM_JS = """
(() => {
  const css = '*,*::before,*::after{animation-duration:0s!important;animation-delay:0s!important;' +
              'transition-duration:0s!important;transition-delay:0s!important;scroll-behavior:auto!important}';
  const add = () => { const s = document.createElement('style'); s.textContent = css;
                      (document.head || document.documentElement).appendChild(s); };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', add); else add();
})();
"""


def should_block(resource_type: str, url: str, block_assets=False, block_css=False,
                 block_trackers=False, extra=()) -> bool:
    if block_assets and resource_type in _ASSET_TYPES:
        return True
    if block_css and resource_type == "stylesheet":
        return True
    u = url.lower()
    if block_trackers and any(h in u for h in _TRACKER_HOSTS):
        return True
    return any(h in u for h in tuple(extra))


def _sync_handler(opts):
    def handler(route):
        r = route.request
        if should_block(r.resource_type, r.url, **opts):
            route.abort()
        else:
            route.continue_()
    return handler


def _async_handler(opts):
    async def handler(route):
        r = route.request
        if should_block(r.resource_type, r.url, **opts):
            await route.abort()
        else:
            await route.continue_()
    return handler


def _plan(block_assets, block_css, block_trackers, extra_block):
    opts = dict(block_assets=block_assets, block_css=block_css,
                block_trackers=block_trackers, extra=tuple(extra_block))
    needs_route = bool(block_assets or block_css or block_trackers or extra_block)
    return opts, needs_route


def apply_speedups(context, block_assets=False, block_css=False, block_trackers=False,
                   kill_animations=False, extra_block=(), timeout_ms=None, nav_timeout_ms=None):
    """Sync API. الافتراضي: لا يغيّر أي شيء. (اعتراض الطلبات بيعطّل الـ HTTP cache، فمش بنركّبه إلا لو طلبت حجب.)"""
    opts, needs_route = _plan(block_assets, block_css, block_trackers, extra_block)
    if needs_route:
        context.route("**/*", _sync_handler(opts))
    if kill_animations:
        context.add_init_script(KILL_ANIM_JS)
    if timeout_ms:
        context.set_default_timeout(timeout_ms)
    if nav_timeout_ms:
        context.set_default_navigation_timeout(nav_timeout_ms)
    return context


async def apply_speedups_async(context, block_assets=False, block_css=False, block_trackers=False,
                               kill_animations=False, extra_block=(), timeout_ms=None, nav_timeout_ms=None):
    opts, needs_route = _plan(block_assets, block_css, block_trackers, extra_block)
    if needs_route:
        await context.route("**/*", _async_handler(opts))
    if kill_animations:
        await context.add_init_script(KILL_ANIM_JS)
    if timeout_ms:
        context.set_default_timeout(timeout_ms)
    if nav_timeout_ms:
        context.set_default_navigation_timeout(nav_timeout_ms)
    return context


# ── قياس الانتظار (profiler) ─────────────────────────────────────────

_lock = threading.Lock()
_sleeps = collections.defaultdict(lambda: [0, 0.0])   # "file.py:123" -> [عدد, ثواني]
_t0 = time.monotonic()
_orig_sleep = time.sleep
_installed = False


def _caller_site() -> str:
    f = sys._getframe(1)
    while f is not None and f.f_code.co_filename == __file__:
        f = f.f_back
    return f"{os.path.basename(f.f_code.co_filename)}:{f.f_lineno}" if f else "?"


def _record(site: str, seconds: float):
    with _lock:
        rec = _sleeps[site]
        rec[0] += 1
        rec[1] += seconds


def _patched_sleep(secs):
    site = _caller_site()
    t = time.monotonic()
    _orig_sleep(secs)
    _record(site, time.monotonic() - t)


def profile_sleeps(*modules):
    """
    يبدأ يقيس كل time.sleep و page.wait_for_timeout.
    مرّر الموديولات اللي عملت فيها `from time import sleep` (زي core) عشان تتغطى كمان.
    """
    global _installed
    if _installed:
        return
    _installed = True
    time.sleep = _patched_sleep
    for m in modules:
        if getattr(m, "sleep", None) is _orig_sleep:
            m.sleep = _patched_sleep

    try:  # Playwright sync
        from playwright.sync_api import Page, Frame
        for cls in (Page, Frame):
            orig = cls.wait_for_timeout
            def make(orig):
                def wrapped(self, timeout):
                    site = _caller_site()
                    t = time.monotonic()
                    try:
                        return orig(self, timeout)
                    finally:
                        _record(site + " (wait_for_timeout)", time.monotonic() - t)
                return wrapped
            cls.wait_for_timeout = make(orig)
    except Exception:
        pass

    try:  # Playwright async
        from playwright.async_api import Page as APage, Frame as AFrame
        for cls in (APage, AFrame):
            orig = cls.wait_for_timeout
            def amake(orig):
                async def wrapped(self, timeout):
                    site = _caller_site()
                    t = time.monotonic()
                    try:
                        return await orig(self, timeout)
                    finally:
                        _record(site + " (wait_for_timeout)", time.monotonic() - t)
                return wrapped
            cls.wait_for_timeout = amake(orig)
    except Exception:
        pass


def reset():
    global _t0
    with _lock:
        _sleeps.clear()
        _t0 = time.monotonic()


def report(top: int = 8) -> str:
    wall = max(time.monotonic() - _t0, 1e-9)
    with _lock:
        items = sorted(_sleeps.items(), key=lambda kv: kv[1][1], reverse=True)
    total = sum(v[1] for _, v in items)
    lines = [f"⏱️  وقت الحل: {wall:.1f}ث | انتظار ثابت (sleep): {total:.1f}ث ({100 * total / wall:.0f}%)"]
    for site, (n, secs) in items[:top]:
        lines.append(f"   {site:<45} ×{n:<4} = {secs:6.1f}ث ({100 * secs / wall:.0f}%)")
    if not items:
        lines.append("   (مفيش sleeps متسجلة)")
    return "\n".join(lines)
