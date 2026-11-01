<div align="center">

# desk

**One command to drive any Mac app *and* any website — from an AI agent.**

`desk click --app Safari --name "Reload"` &nbsp;·&nbsp; `desk web open https://example.com`

![python](https://img.shields.io/badge/python-3.9%2B-blue) ![deps](https://img.shields.io/badge/dependencies-zero-brightgreen) ![platform](https://img.shields.io/badge/platform-macOS-lightgrey) ![license](https://img.shields.io/badge/license-MIT-black)

</div>

---

Agents are great at text and terrible at GUIs — mostly because every GUI driver
has its own quirks. `desk` is a tiny **router** (one file, stdlib only) that puts
the best drivers behind a single, boring, predictable interface and falls back
automatically when one of them fails.

It is not a fourth automation framework. It is the glue that makes the existing
ones usable by an agent without it having to learn four CLIs.

## How it routes

```
desk click|find|type  ──►  agent-desktop   semantic, headless, explicit delivery info
                              │ not found / not delivered
                              ▼
                           cua-driver       accessibility token → pixel click from element frame

desk web …            ──►  agent-browser ──► playwright-cli   (read also tries agent-reach)
```

- **Mac apps:** [agent-desktop](https://github.com/vercel-labs/agent-desktop) first, [Cua Driver](https://github.com/trycua/cua) as fallback (AX token, then a pixel click computed from the element frame).
- **Web:** `agent-browser` first, `playwright-cli` as fallback. The browser that opened a page stays in front for follow-up steps, so element refs stay valid within a session.
- **Reading platforms:** `desk reach …` hands off to Agent-Reach (read-only: GitHub, YouTube captions, Reddit, X, …).

## Why it's nice for agents

- **One JSON line per command** — `{"ok": true, "route": "agent-desktop", …}`. Easy to parse, easy to log, always tells you *which* driver did the work.
- **Never double-clicks by accident.** If agent-desktop reports that an action *might* have landed (`uncertain: true`), `desk` stops instead of re-sending it through another backend. Read state first, then decide.
- **Unicode-proof app names.** macOS app names with soft hyphens (`System­einstellungen`) just work — names are NFC-normalized and case-folded before matching.
- **Window picking that makes sense.** Prefers titled, widest windows; falls back from exact label to substring match.
- **Zero dependencies.** One Python file, standard library only.

## Install

```bash
git clone https://github.com/schenkei-code/desk.git
install -m 755 desk/desk.py ~/.local/bin/desk
```

`desk` expects the drivers it routes to at `~/.local/bin/` — `agent-desktop`, `cua-driver`, `agent-browser`, `playwright-cli`, `agent-reach`. You only need the ones you use. On macOS grant your terminal **Accessibility** and **Screen Recording** permission.

## Usage

```bash
desk apps                                              # running GUI apps
desk find  --app Notes --name "New Note" --role button
desk click --app Notes --name "New Note"
desk type  --app Notes --name "Body" --text "hello from an agent"
desk shot  --app Notes --out notes.png

desk web open https://news.ycombinator.com
desk web snapshot                                      # interactive elements with refs
desk web click @e12
desk web read https://example.com                      # agent-browser → agent-reach → playwright
desk reach read https://github.com/vercel-labs/agent-desktop

desk ad | cua | ab | pw | reach …                      # raw passthrough to each driver
```

Every command exits `0` on success and prints a single JSON object, e.g.:

```json
{"ok": true, "route": "cua-pixel", "effect": "delivered", "label": "Reload"}
```

## Limits (on purpose)

- A locked screen means no windows — both GUI drivers see nothing.
- `desk` never types passwords, logs in, or buys things. That's for a human.
- macOS only for the GUI half; the web half works wherever the browser CLIs do.

## License

MIT
