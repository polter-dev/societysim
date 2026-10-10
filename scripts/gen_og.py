"""Write link-preview (Open Graph) pages and images into a staged site.

    python scripts/gen_og.py <site_dir> <batch_dir> [--base https://society-sim.us]

Creates, under <site_dir>/og/:
  card.png                       the site-wide image
  <game_id>.html / .png          a small redirect page per game, with its own meta and image
  <game_id>-<seq>.html / .png    the same per highlight moment (from <batch>/highlights.json)

Share these og/ URLs: chat apps read the meta, and a visitor is sent on to the viewer.
Plain `/?game=...&step=...` links get the site-wide card from viewer/index.html.

Cards never carry hidden information. They use only the game id, the day, and the model
names seated at the table. Roles, the winner and highlight descriptions (which name roles)
are left out, so a card is safe for a moment mid-game.

Needs Pillow (a matplotlib dependency); text is set in matplotlib's bundled DejaVu font.
"""

import argparse
import html
import json
import re
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

SIZE = (1200, 630)
BG, FG, MUTED, ACCENT = "#14161c", "#f3f4f6", "#9ca3af", "#7c9cf5"
DEFAULT_BASE = "https://society-sim.us"
SITE_TITLE = "SocietySim"
SITE_TEXT = (
    "LLM agents from different labs play Werewolf. Watch who lies, who gets caught, and who wins."
)
SAFE_ID = re.compile(r"^[A-Za-z0-9_.-]+$")


def _font(size: int, bold: bool = False):
    try:
        import matplotlib

        name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
        return ImageFont.truetype(str(Path(matplotlib.get_data_path()) / "fonts/ttf" / name), size)
    except Exception:
        return ImageFont.load_default(size)


def _wrap(draw, text: str, font, width: int, lines: int) -> list[str]:
    out, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if cur and draw.textlength(trial, font=font) > width:
            out.append(cur)
            cur = word
        else:
            cur = trial
    out.append(cur)
    if len(out) > lines:
        out = out[:lines]
        out[-1] = out[-1].rstrip(". ") + "..."
    return out


def card_image(path: Path, title: str, text: str, kicker: str = SITE_TITLE) -> None:
    img = Image.new("RGB", SIZE, BG)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, 14, SIZE[1]], fill=ACCENT)
    d.text((80, 70), kicker, font=_font(34, True), fill=ACCENT)
    y = 170
    for line in _wrap(d, title, _font(68, True), 1040, 2):
        d.text((80, y), line, font=_font(68, True), fill=FG)
        y += 88
    y += 20
    for line in _wrap(d, text, _font(36), 1040, 4):
        d.text((80, y), line, font=_font(36), fill=MUTED)
        y += 52
    d.text((80, 560), "society-sim.us", font=_font(30), fill=MUTED)
    img.save(path, optimize=True)


def page(base: str, name: str, title: str, text: str, target: str) -> str:
    e = html.escape
    url, image = f"{base}/og/{name}.html", f"{base}/og/{name}.png"
    dest = e(target, quote=True)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{e(title)}</title>
<meta name="description" content="{e(text, quote=True)}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="{SITE_TITLE}">
<meta property="og:title" content="{e(title, quote=True)}">
<meta property="og:description" content="{e(text, quote=True)}">
<meta property="og:url" content="{e(url, quote=True)}">
<meta property="og:image" content="{e(image, quote=True)}">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{e(title, quote=True)}">
<meta name="twitter:description" content="{e(text, quote=True)}">
<meta name="twitter:image" content="{e(image, quote=True)}">
<meta http-equiv="refresh" content="0; url={dest}">
<link rel="canonical" href="{dest}">
<script>location.replace({json.dumps(target).replace("<", "\\u003c")});</script>
</head>
<body><a href="{dest}">Open the replay</a></body>
</html>
"""


def _safe(value) -> str | None:
    return value if isinstance(value, str) and SAFE_ID.match(value) else None


def generate(site: Path, batch: Path, base: str = DEFAULT_BASE) -> list[str]:
    """Write the cards; return the og/ file names written."""
    base = base.rstrip("/")
    og = site / "og"
    og.mkdir(parents=True, exist_ok=True)
    card_image(og / "card.png", SITE_TITLE, SITE_TEXT)

    models: dict[str, list[str]] = {}
    for entry in json.loads((batch / "report" / "index.json").read_text(encoding="utf-8")):
        gid = _safe(entry.get("game_id"))
        if gid is None:
            print(
                f"gen_og: skipping game with unusable id {entry.get('game_id')!r}", file=sys.stderr
            )
            continue
        seats = entry.get("seats") or {}
        # model names only: seats also carry roles, which must stay out of a card
        models[gid] = sorted(
            {s["model"] for s in seats.values() if isinstance(s, dict) and s.get("model")}
        )

    def seated(gid: str) -> str:
        return (
            f"{len(models[gid])} models at the table: {', '.join(models[gid])}."
            if models[gid]
            else ""
        )

    made = ["card.png"]
    for gid in models:
        title, text = f"Werewolf replay: {gid}", f"{seated(gid)} Watch the full game.".strip()
        (og / f"{gid}.html").write_text(
            page(base, gid, title, text, f"../?game={gid}"), encoding="utf-8"
        )
        card_image(og / f"{gid}.png", title, text, "SocietySim replay")
        made += [f"{gid}.html", f"{gid}.png"]

    hl_path = batch / "highlights.json"
    seen = set()
    for h in json.loads(hl_path.read_text(encoding="utf-8")) if hl_path.is_file() else []:
        gid, seq, day = _safe(h.get("game_id")), h.get("seq"), h.get("day")
        if gid not in models or not (isinstance(seq, list) and seq and isinstance(seq[0], int)):
            continue
        name = f"{gid}-{seq[0]}"
        if name in seen:
            continue
        seen.add(name)
        # The highlight's own description names roles, so a card says only where and when.
        when = f"Day {day}" if isinstance(day, int) else "A key moment"
        title, text = (
            f"{when} of a Werewolf game: {gid}",
            f"{seated(gid)} Watch this moment.".strip(),
        )
        target = f"../?game={gid}&seq={seq[0]}&mode=narrator"
        (og / f"{name}.html").write_text(page(base, name, title, text, target), encoding="utf-8")
        card_image(og / f"{name}.png", title, text, "SocietySim moment")
        made += [f"{name}.html", f"{name}.png"]
    return made


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("site", type=Path)
    ap.add_argument("batch", type=Path)
    ap.add_argument("--base", default=DEFAULT_BASE)
    a = ap.parse_args()
    made = generate(a.site, a.batch, a.base)
    print(f"gen_og: wrote {len(made)} files to {a.site / 'og'}")


if __name__ == "__main__":
    main()
