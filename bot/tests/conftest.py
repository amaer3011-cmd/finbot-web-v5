"""أدوات مشتركة للاختبارات.

بعد تقسيم core إلى وحدات، كل دالة تقرأ أسماء الوحدات الأخرى من مساحة أسماء وحدتها.
`patch_core` يستبدل الاسم في الحزمة وفي كل وحدة تعرّفه أو تستوردْه، فيعمل
الاستبدال أينما استُدعيت الدالة.
"""
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import core  # noqa: E402


def patch_core(monkeypatch, name, value):
    targets = [core] + [
        m for m in vars(core).values()
        if isinstance(m, types.ModuleType) and m.__name__.startswith("core.")
    ]
    for module in sys.modules.values():
        if getattr(module, "__name__", "").startswith("core."):
            targets.append(module)
    seen = set()
    for module in targets:
        if id(module) in seen or not hasattr(module, name):
            continue
        seen.add(id(module))
        monkeypatch.setattr(module, name, value)
