import os, sys, asyncio
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pw_speed as ps

class Req:
    def __init__(s, t, u): s.resource_type, s.url = t, u
class Route:
    def __init__(s, t, u): s.request, s.act = Req(t, u), None
    def abort(s): s.act = "abort"
    def continue_(s): s.act = "continue"
class Ctx:
    def __init__(s): s.calls = []
    def route(s, pat, h): s.calls.append("route"); s.h = h
    def add_init_script(s, js): s.calls.append("init")
    def set_default_timeout(s, t): s.calls.append("timeout")
    def set_default_navigation_timeout(s, t): s.calls.append("nav")

def test_defaults_change_nothing():
    c = Ctx(); ps.apply_speedups(c); assert c.calls == []          # لا route (يحافظ على HTTP cache) ولا init script

def test_opt_in_blocking():
    c = Ctx(); ps.apply_speedups(c, block_assets=True, block_trackers=True)
    assert c.calls == ["route"]
    a, b, d, e = Route("image", "https://x/a.png"), Route("xhr", "https://x/api"), Route("script", "https://www.googletagmanager.com/g.js"), Route("stylesheet", "https://x/a.css")
    for r in (a, b, d, e): c.h(r)
    assert (a.act, b.act, d.act, e.act) == ("abort", "continue", "abort", "continue")

def test_should_block_defaults_false():
    assert not ps.should_block("image", "https://x/a.png")
    assert ps.should_block("image", "https://x/a.png", block_assets=True)
    assert not ps.should_block("stylesheet", "https://x/a.css", block_assets=True)
    assert ps.should_block("stylesheet", "https://x/a.css", block_css=True)

def test_kill_animations_opt_in():
    c = Ctx(); ps.apply_speedups(c, kill_animations=True, timeout_ms=9000); assert c.calls == ["init", "timeout"]

def test_async_variant():
    class AC(Ctx):
        async def route(s, p, h): s.calls.append("route"); s.h = h
    class AR(Route):
        async def abort(s): s.act = "abort"
        async def continue_(s): s.act = "continue"
    async def go():
        c = AC(); await ps.apply_speedups_async(c, block_assets=True)
        r = AR("font", "https://x/f.woff"); await c.h(r); assert r.act == "abort"
    asyncio.run(go())

if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"): f(); print("ok", n)
