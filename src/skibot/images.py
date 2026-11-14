"""Licensed photo lookup (Wikimedia Commons) and Pillow rendering of 1080x1350 editorial graphics."""
from __future__ import annotations

import datetime as dt
import html
import io
import logging
import os
import random
import re
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from .config import CACHE_DIR

log = logging.getLogger("skibot")
W, H = 1080, 1350
INK, ICE, ACCENT, CREAM, WHITE = (11, 27, 43), (220, 235, 247), (255, 90, 54), (244, 239, 230), (255, 255, 255)
MARGIN = 72


def _ua() -> str:
    repo = os.getenv("GITHUB_REPOSITORY", "local")
    return f"SkiNewsBot/1.0 (https://github.com/{repo}; editorial bot)"


# ================================================================= photos
COMMONS = "https://commons.wikimedia.org/w/api.php"
BAD_TITLE = re.compile(r"\b(map|logo|flag|coat of arms|diagram|poster|icon|sign|screenshot|svg|panorama)\b", re.I)


def license_ok(name: str, allow_sa: bool) -> bool:
    t = [x for x in re.split(r"[\s\-]+", (name or "").lower()) if x]
    if not t or "nc" in t or "nd" in t:
        return False
    if t[0] == "cc0" or "public" in t and "domain" in t or t[0] in ("pd", "pdm"):
        return True
    if t[0] == "cc" and len(t) > 1 and t[1] == "by":
        return allow_sa or "sa" not in t
    return False


def _strip(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", s or ""))).strip()


def search_commons(query: str, allow_sa: bool, avoid: set[str]) -> list[dict]:
    params = {
        "action": "query", "format": "json", "generator": "search", "gsrnamespace": 6, "gsrlimit": 25,
        "gsrsearch": f"{query} filetype:bitmap", "prop": "imageinfo", "iiprop": "url|size|mime|extmetadata",
        "iiurlwidth": 1600, "iiextmetadatafilter": "LicenseShortName|Artist|NonFree|Restrictions",
    }
    r = requests.get(COMMONS, params=params, headers={"User-Agent": _ua()}, timeout=25)
    r.raise_for_status()
    out = []
    for page in (r.json().get("query", {}).get("pages", {}) or {}).values():
        title = page.get("title", "")
        info = (page.get("imageinfo") or [{}])[0]
        meta = info.get("extmetadata", {})
        val = lambda k: (meta.get(k) or {}).get("value", "")  # noqa: E731
        if title in avoid or BAD_TITLE.search(title) or info.get("mime") not in ("image/jpeg", "image/png"):
            continue
        if info.get("width", 0) < 1400 or info.get("height", 0) < 900:
            continue
        if val("NonFree") or val("Restrictions"):
            continue
        lic = val("LicenseShortName")
        if not license_ok(lic, allow_sa):
            continue
        artist = _strip(val("Artist")) or "Unknown author"
        out.append({"title": title, "url": info.get("thumburl") or info.get("url"), "license": lic,
                    "credit": artist[:60], "page_url": info.get("descriptionurl", "")})
    return out


def find_photo(queries: list[str], allow_sa: bool, avoid: set[str], seed: str) -> tuple[Image.Image, dict] | None:
    rng = random.Random(seed)
    for q in queries:
        try:
            cands = search_commons(q, allow_sa, avoid)
        except Exception as exc:  # noqa: BLE001
            log.warning("Commons search failed for %r: %s", q, exc)
            continue
        rng.shuffle(cands)
        for c in cands[:4]:
            try:
                r = requests.get(c["url"], headers={"User-Agent": _ua()}, timeout=40)
                r.raise_for_status()
                img = ImageOps.exif_transpose(Image.open(io.BytesIO(r.content))).convert("RGB")
                if img.width >= 1000:
                    return img, {k: c[k] for k in ("title", "license", "credit", "page_url")}
            except Exception as exc:  # noqa: BLE001
                log.warning("Photo download failed (%s): %s", c["title"], exc)
    return None


# ================================================================= fonts
FONT_URLS = {
    "serif": "https://raw.githubusercontent.com/google/fonts/main/ofl/playfairdisplay/PlayfairDisplay%5Bwght%5D.ttf",
    "sans": "https://raw.githubusercontent.com/google/fonts/main/ofl/inter/Inter%5Bopsz%2Cwght%5D.ttf",
}
FALLBACKS = {
    "serif": ["/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf", "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf"],
    "sans": ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"],
}
_font_cache: dict = {}
_font_failed: set = set()


def _font_file(kind: str) -> Path | None:
    p = CACHE_DIR / "fonts" / f"{kind}.ttf"
    if not p.exists():
        if kind in _font_failed:
            return None
        try:
            r = requests.get(FONT_URLS[kind], timeout=30, headers={"User-Agent": _ua()})
            r.raise_for_status()
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(r.content)
        except Exception as exc:  # noqa: BLE001
            log.info("Font download unavailable (%s); using system fallback.", type(exc).__name__)
            _font_failed.add(kind)
            return None
    return p


def font(kind: str, size: int, weight: str = "Bold") -> ImageFont.FreeTypeFont:
    key = (kind, size, weight)
    if key in _font_cache:
        return _font_cache[key]
    f = None
    path = _font_file(kind)
    if path:
        try:
            f = ImageFont.truetype(str(path), size)
            try:
                f.set_variation_by_name(weight)
            except Exception:  # noqa: BLE001 - static font or unknown name
                pass
        except Exception:  # noqa: BLE001
            f = None
    if f is None:
        for cand in FALLBACKS[kind]:
            if Path(cand).exists():
                f = ImageFont.truetype(cand, size)
                break
    if f is None:
        f = ImageFont.load_default(size)
    _font_cache[key] = f
    return f


# ================================================================= drawing helpers
def wrap(d: ImageDraw.ImageDraw, text: str, f, max_w: int) -> list[str]:
    lines, cur = [], ""
    for w in text.split():
        t = f"{cur} {w}".strip()
        if d.textlength(t, font=f) <= max_w or not cur:
            cur = t
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def fit_text(d, text, kind, weight, max_w, max_h, max_size, min_size, gap=1.1):
    size = max_size
    while size >= min_size:
        f = font(kind, size, weight)
        lines, lh = wrap(d, text, f, max_w), int(size * gap)
        if len(lines) * lh <= max_h:
            return f, lines, lh
        size -= 4
    f, lh = font(kind, min_size, weight), int(min_size * gap)
    lines = wrap(d, text, f, max_w)
    n = max(1, max_h // lh)
    if len(lines) > n:
        lines = lines[:n]
        lines[-1] = lines[-1].rstrip(" ,.;:") + "…"
    return f, lines, lh


def tracked(d, xy, text, f, fill, tracking=4, anchor="l") -> float:
    x, y = xy
    total = sum(d.textlength(c, font=f) + tracking for c in text) - tracking
    if anchor == "r":
        x -= total
    for c in text:
        d.text((x, y), c, font=f, fill=fill)
        x += d.textlength(c, font=f) + tracking
    return total


def gradient(img: Image.Image, y0: int, y1: int, color, a0: int, a1: int) -> None:
    h = max(1, y1 - y0)
    mask = Image.linear_gradient("L").resize((img.width, h)).point([int(a0 + (a1 - a0) * i / 255) for i in range(256)])
    img.paste(Image.new("RGB", (img.width, h), color), (0, y0), mask)


def cover(photo: Image.Image, size: tuple[int, int], centering=(0.5, 0.5)) -> Image.Image:
    return ImageOps.fit(photo, size, Image.LANCZOS, centering=centering)


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def header(img, post, light=True, y=58) -> None:
    d = ImageDraw.Draw(img)
    col = WHITE if light else INK
    first, _, rest = post["brand"].upper().partition(" ")
    w = tracked(d, (MARGIN, y), first, font("sans", 30, "Bold"), col, 7)
    if rest:
        tracked(d, (MARGIN + w + 14, y), rest, font("sans", 30, "Regular"), ACCENT if light else ACCENT, 7)
    tracked(d, (W - MARGIN, y + 4), post["date_label"].upper(), font("sans", 24, "SemiBold"), col, 3, anchor="r")


def kicker(img, text, xy, light_text=True) -> int:
    d = ImageDraw.Draw(img)
    f = font("sans", 25, "Bold")
    tw = sum(d.textlength(c, font=f) + 4 for c in text.upper()) - 4
    x, y = xy
    d.rounded_rectangle((x, y, x + tw + 44, y + 52), radius=26, fill=ACCENT)
    tracked(d, (x + 22, y + 11), text.upper(), f, WHITE, 4)
    return y + 52


def credit_bar(img, text: str, light=True) -> None:
    if not text:
        return
    d = ImageDraw.Draw(img)
    f = font("sans", 19, "Regular")
    text = text if d.textlength(text, font=f) < W - 2 * MARGIN else text[:95] + "…"
    d.text((MARGIN, H - 44), text, font=f, fill=(255, 255, 255) if light else (90, 100, 110))


# ================================================================= procedural mountains
PALETTES = [
    {"top": (14, 28, 56), "bot": (232, 128, 96), "far": (150, 120, 160), "near": (8, 16, 32)},
    {"top": (10, 40, 70), "bot": (160, 210, 228), "far": (150, 190, 212), "near": (6, 24, 40)},
    {"top": (30, 22, 60), "bot": (250, 170, 110), "far": (176, 122, 150), "near": (14, 12, 30)},
]


def _ridge(rng: random.Random, base: float, amp: float, rough=0.52) -> list[float]:
    pts = [base + rng.uniform(-amp, amp) * 0.4, base + rng.uniform(-amp, amp) * 0.4]
    disp = amp
    while len(pts) < 513:
        new = []
        for a, b in zip(pts, pts[1:]):
            new += [a, (a + b) / 2 + rng.uniform(-disp, disp)]
        new.append(pts[-1])
        pts, disp = new, disp * rough
    return pts


def mountain_scene(seed: str) -> Image.Image:
    rng = random.Random(seed)
    pal = PALETTES[rng.randrange(len(PALETTES))]
    s = 2
    img = Image.new("RGB", (W * s, H * s))
    sky = Image.linear_gradient("L").resize((W * s, int(H * s * 0.8)))
    img.paste(Image.composite(Image.new("RGB", sky.size, pal["bot"]), Image.new("RGB", sky.size, pal["top"]), sky), (0, 0))
    d = ImageDraw.Draw(img)
    d.rectangle((0, int(H * s * 0.8), W * s, H * s), fill=pal["bot"])
    for _ in range(70):  # faint stars in the dark upper sky
        x, y = rng.randrange(W * s), rng.randrange(int(H * s * 0.35))
        r = rng.choice((2, 2, 3))
        d.ellipse((x, y, x + r, y + r), fill=lerp(pal["top"], WHITE, rng.uniform(0.3, 0.8)))
    layers = 5
    for i in range(layers):
        t = i / (layers - 1)
        base = (0.66 + 0.2 * t) * H * s
        pts = _ridge(rng, base, (230 - 90 * t) * s)
        poly = [(k * W * s / 512, y) for k, y in enumerate(pts)] + [(W * s, H * s), (0, H * s)]
        d.polygon(poly, fill=lerp(pal["far"], pal["near"], t ** 0.8))
        haze = Image.linear_gradient("L").resize((W * s, int(260 * s))).point(lambda v: int(70 * (v / 255)) if t < 0.9 else 0)
        img.paste(Image.new("RGB", haze.size, lerp(pal["bot"], pal["far"], 0.3)), (0, int(base - 40 * s)), haze)
        d = ImageDraw.Draw(img)
    return img.resize((W, H), Image.LANCZOS)


# ================================================================= layouts
def layout_photo_news(post, photo) -> Image.Image:
    img = cover(photo, (W, H), (0.5, 0.45))
    img = Image.blend(img, Image.new("RGB", (W, H), INK), 0.18)
    gradient(img, 0, 260, INK, 140, 0)
    gradient(img, 330, H, INK, 0, 235)
    header(img, post)
    d = ImageDraw.Draw(img)
    f, lines, lh = fit_text(d, post["headline"], "serif", "Bold", W - 2 * MARGIN, 600, 86, 56)
    y_end = 1196
    y = y_end - lh * len(lines)
    kicker(img, post["kicker"], (MARGIN, y - 86))
    for ln in lines:
        d.text((MARGIN, y), ln, font=f, fill=WHITE)
        y += lh
    d.line((MARGIN, 1222, W - MARGIN, 1222), fill=(255, 255, 255), width=2)
    tracked(d, (MARGIN, 1244), f"SOURCE · {post['source_label'].upper()}"[:60], font("sans", 22, "SemiBold"), ICE, 3)
    credit_bar(img, post.get("credit_line", ""))
    return img


def layout_split(post, photo) -> Image.Image:
    img = Image.new("RGB", (W, H), CREAM)
    top = 700
    img.paste(cover(photo, (W, top), (0.5, 0.45)), (0, 0))
    gradient(img, 0, 200, INK, 150, 0)
    d = ImageDraw.Draw(img)
    d.rectangle((0, top, W, top + 10), fill=ACCENT)
    header(img, post)
    d = ImageDraw.Draw(img)
    tracked(d, (MARGIN, top + 52), post["kicker"].upper(), font("sans", 25, "Bold"), ACCENT, 5)
    f, lines, lh = fit_text(d, post["headline"], "serif", "Bold", W - 2 * MARGIN, 330, 66, 44)
    y = top + 100
    for ln in lines:
        d.text((MARGIN, y), ln, font=f, fill=INK)
        y += lh
    if post.get("blurb") and y < 1150:
        fb, bl, blh = fit_text(d, post["blurb"], "sans", "Regular", W - 2 * MARGIN, 1195 - y - 10, 30, 24, 1.3)
        y += 14
        for ln in bl:
            d.text((MARGIN, y), ln, font=fb, fill=(60, 72, 84))
            y += blh
    d.line((MARGIN, 1236, W - MARGIN, 1236), fill=INK, width=2)
    tracked(d, (MARGIN, 1258), post["footer_left"].upper()[:48], font("sans", 22, "SemiBold"), INK, 3)
    if post["handle"]:
        tracked(d, (W - MARGIN, 1258), post["handle"].upper()[:30], font("sans", 22, "SemiBold"), ACCENT, 3, anchor="r")
    credit_bar(img, post.get("credit_line", ""), light=False)
    return img


def layout_typographic(post, photo=None) -> Image.Image:
    img = mountain_scene(post["key"])
    gradient(img, 0, 900, INK, 170, 0)
    header(img, post)
    d = ImageDraw.Draw(img)
    kicker(img, post["kicker"], (MARGIN, 190))
    f, lines, lh = fit_text(d, post["headline"], "serif", "Bold", W - 2 * MARGIN, 470, 84, 52)
    y = 290
    for ln in lines:
        d.text((MARGIN, y), ln, font=f, fill=WHITE)
        y += lh
    if post.get("blurb"):
        fb, bl, blh = fit_text(d, post["blurb"], "sans", "Regular", W - 2 * MARGIN, 170, 32, 24, 1.3)
        y += 22
        for ln in bl:
            d.text((MARGIN, y), ln, font=fb, fill=ICE)
            y += blh
    gradient(img, H - 190, H, (4, 8, 16), 0, 200)
    d = ImageDraw.Draw(img)
    tracked(d, (MARGIN, H - 96), post["footer_left"].upper()[:48], font("sans", 22, "SemiBold"), ICE, 3)
    if post["handle"]:
        tracked(d, (W - MARGIN, H - 96), post["handle"].upper()[:30], font("sans", 22, "SemiBold"), (255, 150, 120), 3, anchor="r")
    return img


def render(post: dict, photo: Image.Image | None, settings: dict) -> Image.Image:
    """Pick a layout: photo news -> full-bleed or split (alternating by key); no photo -> typographic."""
    handle = (settings.get("handle") or "").strip()
    if "your_" in handle.lower() or handle.lower() in ("@", "@handle"):
        handle = ""  # never print a placeholder on a published graphic
    post = {**post, "brand": settings["brand_name"], "handle": handle,
            "date_label": dt.date.today().strftime("%d %b %Y")}
    if photo is None:
        return layout_typographic(post)
    use_split = post["kind"] != "news" or int(re.sub(r"\D", "", post["key"])[:6] or 0) % 2 == 1
    return layout_split(post, photo) if use_split else layout_photo_news(post, photo)


def save_jpeg(img: Image.Image, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(path, "JPEG", quality=90, optimize=True, progressive=True)
