#!/usr/bin/env python3
"""desk — one desktop-control front for agents (macOS, Windows, Linux).

Tries Vercel agent-desktop first (semantic, headless, explicit delivery/retry),
falls back to Cua Driver (background AX, then pixel click from the element frame).

  desk find   --app APP --name NAME [--role ROLE]
  desk click  --app APP --name NAME [--role ROLE]
  desk type   --app APP --name NAME --text TEXT [--role ROLE]
  desk shot   --app APP --out FILE.png
  desk apps
  desk web open|read|snapshot|click|fill|shot …   agent-browser → playwright-cli
                                                  (read also tries agent-reach)
  desk ad|cua|ab|pw|reach ...   raw agent-desktop / cua-driver / agent-browser /
                                playwright-cli / agent-reach

Playwright MCP is registered in Claude as server "playwright" (system Chrome).

Every command prints one JSON line: {"ok", "route", ...}.
"""
import argparse, base64, json, os, shutil, subprocess, sys, unicodedata


def tool(name):
    # Prefer ~/.local/bin (where the installers drop them), then anything on PATH.
    # On Windows the same tools ship as .exe or, for npm ones, .cmd shims.
    local = os.path.join(os.path.expanduser("~"), ".local", "bin", name)
    for c in (local, local + ".exe", local + ".cmd"):
        if os.path.isfile(c):
            return c
    return shutil.which(name) or local


def handoff(path, args):
    # Windows has no real exec(): run the tool and pass its exit code through.
    if os.name == "nt":
        sys.exit(subprocess.call([path, *args]))
    os.execv(path, [path, *args])


AD = tool("agent-desktop")
CUA = tool("cua-driver")


def run(cmd, timeout=60):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        out = p.stdout.strip() or p.stderr.strip()
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": {"code": "TIMEOUT"}}
    try:
        return json.loads(out)
    except ValueError:
        return {"ok": False, "raw": out[:500]}


def norm(s):
    # App names like "System­einstellungen" carry soft hyphens; compare without them.
    return unicodedata.normalize("NFC", (s or "").replace("­", "")).casefold()


def emit(**kw):
    print(json.dumps(kw, ensure_ascii=False))
    sys.exit(0 if kw.get("ok") else 1)


# ---------- agent-desktop ----------

def ad_app_name(app):
    apps = run([AD, "list-apps"]).get("data", {}).get("apps", [])
    for a in apps:
        if norm(a.get("name")) == norm(app):
            return a["name"]
    return app


def ad_find(a):
    cmd = [AD, "find", "--app", ad_app_name(a.app), "--name", a.name, "--first"]
    if a.role:
        cmd += ["--role", a.role]
    r = run(cmd)
    if not r.get("ok"):
        return None, r
    d = r.get("data")
    items = d if isinstance(d, list) else (d.get("matches") or d.get("elements") or [d]) if isinstance(d, dict) else []
    for it in items:
        ref = isinstance(it, dict) and (it.get("ref") or it.get("qualified_ref"))
        if ref:
            return ref, r
    return None, r


def ad_delivered(r):
    disp = (r.get("data") or {}).get("disposition") or (r.get("error") or {}).get("disposition") or {}
    return r.get("ok") and disp.get("delivery", "delivered") != "not_delivered"


# ---------- cua ----------

def cua_targets(app):
    ws = run([CUA, "list_windows", "{}"])
    ws = ws.get("structuredContent", ws).get("windows", [])
    hits = [w for w in ws if norm(w.get("app_name")) == norm(app)]
    hits.sort(key=lambda w: (not w.get("title"), -(w.get("bounds", {}).get("width", 0) or 0)))
    return hits


def cua_state(pid, wid):
    s = run([CUA, "get_window_state", json.dumps({"pid": pid, "window_id": wid})], timeout=90)
    return s.get("structuredContent", s)


def cua_match(state, name, role):
    want = norm(name)
    best = None
    for e in state.get("elements", []):
        label = norm(e.get("label") or e.get("title") or e.get("value"))
        if not label or (role and role.lower() not in (e.get("role") or "").lower()):
            continue
        if label == want:
            return e
        if best is None and want in label:
            best = e
    return best


def cua_locate(a):
    for w in cua_targets(a.app):
        st = cua_state(w["pid"], w["window_id"])
        e = cua_match(st, a.name, a.role)
        if e:
            return w, st, e
    return None, None, None


def pixel_of(st, e):
    f, b, k = e.get("frame"), st.get("window_bounds"), st.get("screenshot_scale") or 1
    if not f or not b:
        return None
    return (round((f["x"] + f["w"] / 2 - b["x"]) * k), round((f["y"] + f["h"] / 2 - b["y"]) * k))


def cua_click(w, st, e):
    base = {"pid": w["pid"], "window_id": w["window_id"]}
    r = run([CUA, "click", json.dumps({**base, "element_token": e["element_token"]})])
    eff = r.get("structuredContent", r).get("effect", r.get("effect"))
    if eff not in ("refused", "suspected_noop") and r.get("ok", True) is not False:
        return "cua-ax", eff
    px = pixel_of(st, e)
    if not px:
        return "cua-ax", eff
    r = run([CUA, "click", json.dumps({**base, "x": px[0], "y": px[1]})])
    return "cua-pixel", r.get("structuredContent", r).get("effect", r.get("effect"))


# ---------- commands ----------

def cmd_find(a):
    ref, r = ad_find(a)
    if ref:
        emit(ok=True, route="agent-desktop", ref=ref)
    w, st, e = cua_locate(a)
    if e:
        emit(ok=True, route="cua", pid=w["pid"], window_id=w["window_id"],
             element_token=e["element_token"], role=e.get("role"), frame=e.get("frame"))
    emit(ok=False, route="none", agent_desktop=r.get("error"))


def cmd_click(a):
    ref, r = ad_find(a)
    if ref:
        c = run([AD, "click", ref])
        if ad_delivered(c):
            emit(ok=True, route="agent-desktop", ref=ref, disposition=c.get("data", {}).get("disposition"))
        disp = (c.get("error") or {}).get("disposition", {})
        if disp.get("delivery") not in (None, "not_delivered"):
            # May have landed: never click a second time through another backend.
            emit(ok=False, route="agent-desktop", uncertain=True, result=c)
    w, st, e = cua_locate(a)
    if not e:
        emit(ok=False, route="none", agent_desktop=(r or {}).get("error"))
    route, eff = cua_click(w, st, e)
    emit(ok=eff not in ("refused",), route=route, effect=eff, label=e.get("label"))


def cmd_type(a):
    ref, r = ad_find(a)
    if ref:
        c = run([AD, "type", ref, a.text])
        if ad_delivered(c):
            emit(ok=True, route="agent-desktop", ref=ref)
        if (c.get("error") or {}).get("disposition", {}).get("delivery") not in (None, "not_delivered"):
            emit(ok=False, route="agent-desktop", uncertain=True, result=c)
    w, st, e = cua_locate(a)
    if not e:
        emit(ok=False, route="none")
    r = run([CUA, "type_text", json.dumps({"pid": w["pid"], "window_id": w["window_id"],
                                           "element_token": e["element_token"], "text": a.text})])
    eff = r.get("structuredContent", r).get("effect", r.get("effect"))
    emit(ok=eff != "refused", route="cua", effect=eff)


def cmd_shot(a):
    ws = cua_targets(a.app)
    if not ws:
        emit(ok=False, route="none", error="no window")
    st = cua_state(ws[0]["pid"], ws[0]["window_id"])
    png = st.get("screenshot_png_b64")
    if not png:
        emit(ok=False, route="cua", error="no screenshot")
    with open(a.out, "wb") as fh:
        fh.write(base64.b64decode(png))
    emit(ok=True, route="cua", out=a.out, window=ws[0].get("title"))


def cmd_apps(_):
    apps = run([AD, "list-apps"]).get("data", {}).get("apps", [])
    emit(ok=True, route="agent-desktop", apps=[x["name"] for x in apps if x.get("presentation") != "background"])


# ---------- web (browser) ----------

AB = tool("agent-browser")
PW = tool("playwright-cli")
REACH = tool("agent-reach")


def sh(cmd, timeout=120):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or p.stderr).strip()
    except subprocess.TimeoutExpired:
        return 124, "timeout"


STICKY = os.path.join(os.path.expanduser("~"), ".desk-web-route")


def chain(steps):
    # Each step: (route, argv). First one that exits 0 wins. The browser that
    # opened the page goes first for later steps, so refs stay in one session.
    try:
        last = open(STICKY).read().strip()
        steps = sorted(steps, key=lambda s: s[0] != last)
    except OSError:
        pass
    tried = []
    for route, argv in steps:
        code, out = sh(argv)
        if code == 0:
            if route in ("agent-browser", "playwright-cli"):
                with open(STICKY, "w") as fh:
                    fh.write(route)
            emit(ok=True, route=route, out=out[:20000])
        tried.append({"route": route, "code": code, "out": out[:300]})
    emit(ok=False, route="none", tried=tried)


def cmd_web(argv):
    if not argv:
        emit(ok=False, error="desk web open|read|click|fill|shot|snapshot …")
    op, rest = argv[0], argv[1:]
    if op == "open":
        chain([("agent-browser", [AB, "open", *rest]), ("playwright-cli", [PW, "open", *rest])])
    if op == "read":
        chain([("agent-browser", [AB, "read", *rest]), ("agent-reach", [REACH, "read", *rest]),
               ("playwright-cli", [PW, "goto", *rest])])
    if op == "snapshot":
        chain([("agent-browser", [AB, "snapshot", "-i", *rest]), ("playwright-cli", [PW, "snapshot", *rest])])
    if op in ("click", "fill", "type", "check", "uncheck", "select", "hover"):
        chain([("agent-browser", [AB, op, *rest]), ("playwright-cli", [PW, op, *rest])])
    if op == "shot":
        pw = [PW, "screenshot", "--filename", rest[0], *rest[1:]] if rest else [PW, "screenshot"]
        chain([("agent-browser", [AB, "screenshot", *rest]), ("playwright-cli", pw)])
    # Anything else goes to agent-browser unchanged.
    handoff(AB, [op, *rest])


def main():
    raw = {"ad": AD, "cua": CUA, "ab": AB, "pw": PW, "reach": REACH}
    if len(sys.argv) > 1 and sys.argv[1] in raw:
        handoff(raw[sys.argv[1]], sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1] == "web":
        cmd_web(sys.argv[2:])
    p = argparse.ArgumentParser(prog="desk", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    for n in ("find", "click", "type"):
        s = sub.add_parser(n)
        s.add_argument("--app", required=True)
        s.add_argument("--name", required=True)
        s.add_argument("--role")
        if n == "type":
            s.add_argument("--text", required=True)
    s = sub.add_parser("shot")
    s.add_argument("--app", required=True)
    s.add_argument("--out", required=True)
    sub.add_parser("apps")
    a = p.parse_args()
    {"find": cmd_find, "click": cmd_click, "type": cmd_type, "shot": cmd_shot, "apps": cmd_apps}[a.cmd](a)


if __name__ == "__main__":
    main()
