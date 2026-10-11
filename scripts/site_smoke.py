"""Live site smoke check: load every public page headless and say what is broken.

    uv run --extra smoke python -m playwright install chromium   # once
    uv run --extra smoke python scripts/site_smoke.py [BASE_URL] [--json] [--channel chrome]

Read-only: only GETs and clicks on the page (no form submits, no secrets). A guard in the browser
context aborts every POST/PUT/PATCH/DELETE to the play API (/api/...) and fails the run if one was
attempted, so no selector change can start a paid visitor game. Never load ?page=play without
mode=advanced: the basic mode auto-starts a game.
Pages and selectors are in ROUTES below; routes and query params come from viewer/src/main.tsx.
Exits 1 if any check fails. --strict-play is kept for the Site smoke workflow and does nothing now.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from urllib.parse import urljoin, urlsplit

DEFAULT_BASE = "https://society-sim.us/"
ROLE_WORDS = re.compile(r"\b(werewolf|wolf|wolves|seer|doctor|villager)\b", re.I)
# Optional assets the viewer probes and handles; their 404s are not site errors.
IGNORED_404 = re.compile(r"/favicon\.ico$|\.audio/index\.json$")
# The Turnstile widget on the play page logs its own noise from challenges.cloudflare.com.
THIRD_PARTY = re.compile(r"challenges\.cloudflare\.com|cloudflareinsights\.com")
# The play server's API, on any host. Reads stay allowed; anything that writes is aborted.
PLAY_API_PATH = re.compile(r"/api(/|$)")
PLAY_API_GLOB = "**/api/**"
READ_ONLY_METHODS = {"GET", "HEAD", "OPTIONS"}

# key element per page: (selector, what it is). The replay and its deep links share one.
SEL_H1 = "main h1"
SEL_SEATS = "[data-seat]"  # the table: one <li> per player (Table.tsx); count and read these
# The <li> is a zero-size anchor since the medallion table (#404): its button, medallion and
# plate overflow it, so Playwright never sees the <li> as visible. Wait on the button instead.
# The testid only exists once deployed; the structural fallback works on the live site today.
SEL_SEAT_KEY = "[data-testid='seat-button'], [data-seat] > button"
# The seat inspector is the player profile dialog (PlayerProfile.tsx); its role line.
SEL_PROFILE_ROLE = "dialog[aria-label^='Inspector:'] .profile-role"
SEL_STEP = ".step-label"  # "beat N / M"
SEL_SPOT = "section.spotwolf"
# Landing screens (viewer/src/landing). Each key is "data-testid, current DOM": the testid is
# stable across redesigns but only exists once deployed; the class selector works on the live
# site today.
SEL_LANDING = "[data-testid='landing'], main.landing-page"
SEL_MODES = "[data-testid='mode-selection'], main.mode-selection"
SEL_MODE_CARDS = "[data-testid='mode-cards'] a, nav.mode-cards a"
SEL_ADVANCED_CONTROLS = "form [role='combobox'][aria-label], form select[aria-label]"
SEL_ADVANCED = "[data-testid='advanced-setup'], main.advanced-screen"


@dataclass
class Result:
    page: str
    check: str
    ok: bool
    detail: str = ""


@dataclass
class Route:
    name: str
    query: str  # may use {game}, {step}, {model}
    heading: str  # substring of the <h1> (case-insensitive); "" skips
    key: str  # selector that must be visible
    needs: tuple[str, ...] = ()  # context values the query uses


ROUTES = [
    # The nav's Play link is the mode picker ("modes"). Never add a bare ?page=play route: its
    # default (basic) auto-starts a real paid game. ?page=play&mode=advanced does not.
    # the bare URL is the illustrated landing page; the game index moved to ?page=games
    Route("landing", "", "society sim", SEL_LANDING),
    Route("modes", "?page=modes", "choose your fate", SEL_MODES),
    Route("advanced setup", "?page=play&mode=advanced", "advanced setup", SEL_ADVANCED),
    Route("index", "?page=games", "games", "main table a[href*='game=']"),
    Route("replay", "?game={game}", "", SEL_SEAT_KEY, ("game",)),
    # mode=villager is the spectator view (modeFromSearch in viewer/src); explicit so a changed
    # default can't swap in the narrator view
    Route(
        "step deep link",
        "?game={game}&step={step}&mode=villager",
        "",
        SEL_SEAT_KEY,
        ("game", "step"),
    ),
    Route("leaderboard", "?page=leaderboard", "leaderboard", "table caption"),
    Route("research", "?page=research", "research", ".deception-matrix"),
    Route("model profile", "?page=model&model={model}", "{model}", "main h2", ("model",)),
    Route("new game", "?page=new", "new game", "form.setup-form"),
    Route("spot the wolf", "?game={game}", "", "details.replay-spotwolf", ("game",)),
]


# ---- pure helpers (unit-tested) --------------------------------------------------------------


def with_slash(base: str) -> str:
    return base if base.endswith("/") else base + "/"


def page_url(base: str, query: str) -> str:
    return urljoin(with_slash(base), query)


def ignorable_console_error(text: str, url: str | None) -> bool:
    """Optional-file 404 lines and Turnstile noise; real page errors are never ignored."""
    if THIRD_PARTY.search(url or "") or text.startswith("%c%d"):
        return True
    return text.startswith("Failed to load resource") and bool(IGNORED_404.search(url or ""))


def is_play_write(method: str, url: str) -> bool:
    """True for a request that would change play-server state: a non-read method on /api/..."""
    return method.upper() not in READ_ONLY_METHODS and bool(
        PLAY_API_PATH.search(urlsplit(url).path)
    )


def pick_context(index: list, leaderboard: dict, log_text: str) -> dict[str, str]:
    """First game id, first leaderboard model, and a mid-game seq for the deep link."""
    ctx: dict[str, str] = {}
    if index:
        ctx["game"] = index[0]["game_id"]
    if leaderboard.get("models"):
        ctx["model"] = leaderboard["models"][0]["model"]
    seqs = [json.loads(line)["seq"] for line in log_text.splitlines() if line.strip()]
    if seqs:
        ctx["step"] = str(seqs[len(seqs) // 3])
    return ctx


def role_leaks(cards: list[dict]) -> list[str]:
    """Seats that are not revealed but show a role name. cards: {seat, revealed, text}."""
    return [
        f"{c['seat']}: {c['text']!r}"
        for c in cards
        if c["revealed"] != "true" and ROLE_WORDS.search(c["text"])
    ]


def wolf_chat_texts(log_text: str) -> list[str]:
    """public_text of every wolf_chat event in a game log: text a spectator must never see."""
    out = []
    for line in log_text.splitlines():
        if not line.strip():
            continue
        ev = json.loads(line)
        if ev.get("type") == "wolf_chat" and (ev.get("public_text") or "").strip():
            out.append(ev["public_text"].strip())
    return out


def wolf_chat_leaks(page_text: str, texts: list[str]) -> list[str]:
    """Wolf-chat lines (first 40 chars) found in the page text, whitespace-insensitive."""
    squash = lambda t: " ".join(t.split())  # noqa: E731
    body = squash(page_text)
    return [squash(t)[:40] for t in texts if squash(t) in body]


def step_beat(label: str) -> int | None:
    m = re.search(r"beat\s+(\d+)\s*/\s*(\d+)", label)
    return int(m.group(1)) if m else None


def table(results: list[Result]) -> str:
    w = max([len(r.page) for r in results] + [4])
    c = max([len(r.check) for r in results] + [5])
    lines = [f"{'page'.ljust(w)}  {'check'.ljust(c)}  result  detail"]
    for r in results:
        lines.append(
            f"{r.page.ljust(w)}  {r.check.ljust(c)}  {'PASS' if r.ok else 'FAIL'}    {r.detail}"
        )
    failed = sum(not r.ok for r in results)
    lines.append(f"\n{len(results) - failed} passed, {failed} failed")
    return "\n".join(lines)


# ---- browser ---------------------------------------------------------------------------------


def run(base: str, *, channel: str | None = None, timeout_ms: int = 20_000) -> list[Result]:
    from playwright.sync_api import Error as PwError
    from playwright.sync_api import sync_playwright

    base = with_slash(base)
    out: list[Result] = []

    def add(page: str, check: str, ok: bool, detail: str = "") -> bool:
        out.append(Result(page, check, bool(ok), detail))
        return bool(ok)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, channel=channel)
        ctx = browser.new_context()
        ctx.set_default_timeout(timeout_ms)

        # hard guard: nothing in this run may write to the play API, whatever a selector clicks
        blocked: list[str] = []

        def guard(route) -> None:
            req = route.request
            if is_play_write(req.method, req.url):
                blocked.append(f"{req.method} {req.url}")
                route.abort()
            else:
                route.continue_()

        ctx.route(PLAY_API_GLOB, guard)

        # data files: plain GETs, then the context for routes comes from them
        fetched: dict[str, str] = {}
        for name in ("index.json", "leaderboard.json", "metrics.json"):
            try:
                r = ctx.request.get(page_url(base, name))
                add("data", name, r.status == 200, f"HTTP {r.status}")
                if r.status == 200:
                    fetched[name] = r.text()
            except PwError as e:
                add("data", name, False, str(e).splitlines()[0])
        log_text = ""
        try:
            index = json.loads(fetched.get("index.json", "[]"))
            board = json.loads(fetched.get("leaderboard.json", "{}"))
            if index:
                r = ctx.request.get(page_url(base, index[0]["file"]))
                add("data", index[0]["file"], r.status == 200, f"HTTP {r.status}")
                log_text = r.text() if r.status == 200 else ""
            values = pick_context(index, board, log_text)
        except (ValueError, KeyError, TypeError) as e:
            values = {}
            add("data", "parse", False, f"{type(e).__name__}: {e}")

        for route in ROUTES:
            missing = [n for n in route.needs if n not in values]
            if missing:
                add(route.name, "context", False, f"no {', '.join(missing)} from the data files")
                continue
            url = page_url(base, route.query.format(**values))
            page = ctx.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e, errors=errors: errors.append(f"uncaught: {e}"))
            page.on(
                "console",
                lambda m, errors=errors: (
                    errors.append(m.text)
                    if m.type == "error"
                    and not ignorable_console_error(m.text, m.location.get("url"))
                    else None
                ),
            )
            try:
                resp = page.goto(url, wait_until="domcontentloaded")
                add(
                    route.name,
                    "HTTP 200",
                    resp is not None and resp.status == 200,
                    f"{resp and resp.status} {url}",
                )
                shown = page.locator(route.key).first
                shown.wait_for(state="visible")
                add(route.name, "key element", True, route.key)
            except PwError as e:
                add(route.name, "key element", False, f"{route.key}: {str(e).splitlines()[0]}")
                add(route.name, "console", not errors, "; ".join(errors[:3]) or "no errors")
                page.close()
                continue

            if route.heading:
                want = route.heading.format(**values).lower()
                got = page.locator(SEL_H1).first.inner_text()
                add(route.name, "heading", want in got.lower(), got)

            extra(page, route, values, add, log_text)
            page.wait_for_timeout(500)  # let late fetches and effects throw
            add(route.name, "console", not errors, "; ".join(errors[:3]) or "no errors")
            page.close()
        browser.close()
    add(
        "guard",
        "no play API writes",
        not blocked,
        "; ".join(blocked[:3]) or "0 POST/PUT/PATCH/DELETE to /api attempted",
    )
    if blocked:
        print(
            f"SMOKE GUARD TRIPPED: a page tried to write to the play API ({len(blocked)} "
            f"request(s), all aborted): {'; '.join(blocked[:3])}",
            file=sys.stderr,
        )
    return out


def extra(page, route: Route, values: dict[str, str], add, log_text: str = "") -> None:
    n = route.name
    if n == "landing":
        # the Play button leads to the mode picker; read-only, so only the link target is checked
        add(n, "play link", page.locator("a[href*='page=modes']").count() >= 1, "links to modes")
    elif n == "modes":
        modes = page.locator(SEL_MODE_CARDS).evaluate_all(
            "els => els.map(a => new URL(a.href).searchParams.get('mode'))"
        )
        add(n, "mode cards", sorted(modes) == ["advanced", "basic", "watch"], ", ".join(modes))
    elif n == "advanced setup":
        # themed comboboxes (ThemedSelect, #391); native selects before that deploy
        selects = page.locator(SEL_ADVANCED_CONTROLS).count()
        start = page.locator("button[aria-label='Start simulation']").count()
        # never clicked: starting a game is not read-only
        add(n, "setup form", selects >= 4 and start == 1, f"{selects} dropdowns, start button")
    elif n == "index":
        add(
            n,
            "game list",
            page.locator("main table a[href*='game=']").count() >= 1,
            "links to replays",
        )
    elif n in ("replay", "step deep link"):
        seats = page.locator(SEL_SEATS).count()
        add(n, "seats", seats >= 5, f"{seats} seats")
        label = page.locator(SEL_STEP).first.inner_text()
        beat = step_beat(label)
        if n == "step deep link":
            add(
                n,
                "step honoured",
                beat is not None and beat > 1 and "step=" in page.url,
                f"{label!r} {page.url}",
            )
        else:
            add(n, "step controls", beat is not None, label)
        if n == "replay":
            hidden_info(page, n, add)
        else:
            mid_game_hidden_info(page, n, add, log_text)
    elif n == "leaderboard":
        rows = page.locator("table tbody tr").count()
        add(n, "rows", rows >= 1, f"{rows} models")
    elif n == "research":
        ok = page.locator(
            ".deception-matrix table"
        ).count() > 0 or "No lies heard" in page.inner_text(".deception-matrix")
        add(n, "matrix", ok, "table or empty-state text")
    elif n == "model profile":
        add(n, "stats", page.locator("main dl, main table").count() > 0, "stats or tables")
    elif n == "new game":
        add(
            n,
            "setup json",
            page.locator("pre.setup-json").inner_text().strip().startswith("{"),
            "preview",
        )
    elif n == "spot the wolf":
        page.locator("details.replay-spotwolf > summary").click()
        page.locator(SEL_SPOT).wait_for(state="visible")
        marks = page.locator(f"{SEL_SPOT} button[aria-pressed]").count()
        seats = page.locator(SEL_SEATS).count()
        add(n, "marks", marks == seats and marks > 0, f"{marks} buttons for {seats} seats")
        add(
            n,
            "no roles",
            not ROLE_WORDS.search(page.locator(".spotwolf-seats").inner_text()),
            "seat list text",
        )


def seat_cards(page) -> list[dict]:
    return page.evaluate(
        """() => [...document.querySelectorAll('[data-seat]')].map(li => ({
            seat: li.dataset.seat, revealed: li.dataset.revealed,
            text: (li.querySelector('[data-role-card]') || li).innerText}))"""
    )


def mid_game_hidden_info(page, name: str, add, log_text: str) -> None:
    """Spectator view on a ?step= deep link before game end.

    Each check fails rather than passes when there is nothing to look at: no seats rendered, no
    unrevealed seat, or no wolf_chat in the log to look for.
    """
    cards = seat_cards(page)
    hidden = [c for c in cards if c["revealed"] != "true"]
    leaks = role_leaks(cards)
    add(
        name,
        "mid-game roles",
        len(cards) >= 5 and bool(hidden) and not leaks,
        "; ".join(leaks) or f"{len(hidden)} of {len(cards)} seats unrevealed, no role names",
    )
    texts = wolf_chat_texts(log_text)
    shown = page.locator("[data-channel='wolves']").count()
    found = wolf_chat_leaks(page.inner_text("body"), texts)
    add(
        name,
        "mid-game wolf chat",
        len(cards) >= 5 and bool(texts) and not shown and not found,
        "; ".join(found)
        or (f"{shown} wolf-channel entries" if shown else "")
        or (
            f"none of {len(texts)} wolf_chat lines on the page" if texts else "no wolf_chat in log"
        ),
    )


def hidden_info(page, name: str, add) -> None:
    """Villager (spectator) view before game end: no role names on seats that are not revealed."""
    cards = seat_cards(page)
    leaks = role_leaks(cards)
    add(
        name,
        "hidden info",
        not leaks and bool(cards),
        "; ".join(leaks) or f"{len(cards)} seats, no role names",
    )
    page.locator("[data-seat] button").first.click()
    badge = page.locator(SEL_PROFILE_ROLE).first
    badge.wait_for(state="visible")
    add(name, "inspector role", not ROLE_WORDS.search(badge.inner_text()), badge.inner_text())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("base_url", nargs="?", default=DEFAULT_BASE)
    ap.add_argument("--json", action="store_true", help="print results as JSON instead of a table")
    ap.add_argument(
        "--channel",
        help="use an installed browser, e.g. chrome or msedge, instead of Playwright's chromium",
    )
    ap.add_argument("--timeout", type=float, default=20, help="seconds to wait for each element")
    ap.add_argument(
        "--strict-play",
        action="store_true",
        help="ignored; kept so the Site smoke workflow still runs",
    )
    args = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    results = run(
        args.base_url,
        channel=args.channel,
        timeout_ms=int(args.timeout * 1000),
    )
    if args.json:
        print(json.dumps([asdict(r) for r in results], indent=2))
    else:
        print(table(results))
    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
