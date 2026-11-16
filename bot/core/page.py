"""وحدة page — جزء من حزمة core (مقسومة من core.py)."""
import time

from .answers import (
    normalize_text,
)
from .config import (
    FEEDBACK_MARKERS,
    INCORRECT_FEEDBACK_PHRASES,
    WAIT_AFTER_ANSWER,
    _job,
)
from .runtime import (
    check_cancel,
)


def main_content(page):
    mains = page.locator("main")
    count = mains.count()
    if not count:
        return page.locator("body")
    return mains.nth(count - 1)

def current_context(page):
    heading = main_content(page).locator("h1, h2").first
    if heading.count():
        return normalize_text(heading.inner_text())
    return normalize_text(page.url)

def _remember_unmatched(prompt):
    job = _job.get()
    if job is not None:
        text = " ".join(prompt.split())[:120]
        if text not in job["unmatched"]:
            job["unmatched"].append(text)

def _wait_until(page, predicate, timeout, poll_interval=100):
    deadline = time.monotonic() + max(1, timeout) / 1000
    while True:
        check_cancel()
        if predicate():
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        page.wait_for_timeout(
            min(poll_interval, max(1, int(remaining * 1000)))
        )

def _wait_for_url(page, url_pattern, timeout, poll_interval=100):
    return _wait_until(
        page,
        lambda: bool(url_pattern.search(page.url)),
        timeout,
        poll_interval,
    )

def wait_for_answer_feedback(page, previous_text, timeout=WAIT_AFTER_ANSWER):
    """Return as soon as the platform shows new grading feedback, with a safe timeout."""
    previous_count = len(FEEDBACK_MARKERS.findall(previous_text or ""))
    return _wait_until(
        page,
        lambda: len(
            FEEDBACK_MARKERS.findall(_activity_view_snapshot(page)["text"])
        ) > previous_count,
        timeout,
        poll_interval=50,
    )

def has_new_incorrect_feedback(previous_text, current_text):
    previous = (previous_text or "").casefold()
    current = (current_text or "").casefold()
    return any(
        current.count(phrase.casefold()) > previous.count(phrase.casefold())
        for phrase in INCORRECT_FEEDBACK_PHRASES
    )

def _activity_view_snapshot(page):
    return page.evaluate(
        "() => { const roots = document.querySelectorAll('main'); "
        "const root = roots.length ? roots[roots.length - 1] : document.body; "
        "return { url: location.href, text: (root && root.innerText || '').trim() }; }"
    )
