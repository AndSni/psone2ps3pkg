"""Drawing primitives for the PSX-style look.

The layout uses a fixed 640x480 grid (the PS1's own resolution). Nothing is
drawn at that size and then enlarged: every helper here takes layout
coordinates and draws at the real window size (scale factor ``K``), so shapes
and text stay sharp at any scale, including fractional ones.
"""

from __future__ import annotations

import math
from pathlib import Path

import pygame
import pygame.gfxdraw

W, H = 640, 480  # layout units
K = 1.0  # real pixels per layout unit; set by set_scale()

TEXT = (236, 238, 248)
DIM = (150, 156, 190)
FAINT = (95, 100, 135)
ACCENT = (120, 176, 255)
OK = (90, 226, 150)
BAD = (255, 108, 108)
WARN = (255, 206, 110)
PANEL = (14, 18, 46, 215)
EDGE_LIGHT = (176, 182, 214)
EDGE_DARK = (34, 36, 66)
BG_BOT = (2, 3, 14)

# The four face-button colours.
TRIANGLE = (52, 222, 196)
CIRCLE = (238, 106, 98)
CROSS = (156, 170, 230)
SQUARE = (214, 148, 196)
_GLYPH_COLOURS = {"triangle": TRIANGLE, "circle": CIRCLE, "cross": CROSS, "square": SQUARE}

_fonts: dict[int, pygame.font.Font] = {}
_cache: dict[tuple, pygame.Surface] = {}
_glyphs: dict[tuple[str, int], pygame.Surface] = {}


# --------------------------------------------------------------------------- #
# scale
# --------------------------------------------------------------------------- #
def set_scale(k: float) -> None:
    global K
    K = max(0.5, float(k))
    reset_fonts()


def pick_scale(desktop: tuple[int, int] | None) -> float:
    """Largest quarter-step scale that keeps the window within ~85 % of the screen."""
    if not desktop or desktop[0] <= 0:
        return 1.0
    k = min(desktop[0] * 0.85 / W, desktop[1] * 0.85 / H)
    return max(1.0, math.floor(k * 4) / 4)


def window_size() -> tuple[int, int]:
    return round(W * K), round(H * K)


def sc(v: float) -> int:
    return round(v * K)


def pt(p) -> tuple[int, int]:
    return round(p[0] * K), round(p[1] * K)


def rc(r) -> pygame.Rect:
    r = pygame.Rect(r)
    x0, y0 = round(r.left * K), round(r.top * K)
    return pygame.Rect(x0, y0, round(r.right * K) - x0, round(r.bottom * K) - y0)


def to_layout(pos) -> tuple[int, int]:
    return int(pos[0] / K), int(pos[1] / K)


def width(w: float) -> int:
    return max(1, round(w * K))


def reset_fonts() -> None:
    _fonts.clear()
    _cache.clear()
    _glyphs.clear()


# --------------------------------------------------------------------------- #
# basic shapes (layout coordinates in, real pixels out)
# --------------------------------------------------------------------------- #
def line(surf, colour, a, b, w: float = 1) -> None:
    pygame.draw.line(surf, colour, pt(a), pt(b), width(w))


def lines(surf, colour, closed: bool, pts, w: float = 1) -> None:
    pygame.draw.lines(surf, colour, closed, [pt(p) for p in pts], width(w))


def rect(surf, colour, r, w: float = 0, radius: float = 0) -> None:
    pygame.draw.rect(surf, colour, rc(r), 0 if w == 0 else width(w), border_radius=sc(radius))


def polygon(surf, colour, pts) -> None:
    p = [pt(q) for q in pts]
    pygame.gfxdraw.aapolygon(surf, p, colour)
    pygame.gfxdraw.filled_polygon(surf, p, colour)


def circle(surf, colour, centre, r: float, w: float = 0) -> None:
    """Anti-aliased circle (filled, or a ring of width w)."""
    x, y = pt(centre)
    R = sc(r)
    if w == 0:
        pygame.gfxdraw.aacircle(surf, x, y, R, colour)
        pygame.gfxdraw.filled_circle(surf, x, y, R, colour)
        return
    t = width(w)
    for i in range(t):
        pygame.gfxdraw.aacircle(surf, x, y, R - i, colour)


def fill_alpha(surf, r, rgba) -> None:
    rr = rc(r)
    if rr.width <= 0 or rr.height <= 0:
        return
    layer = pygame.Surface(rr.size, pygame.SRCALPHA)
    layer.fill(rgba)
    surf.blit(layer, rr.topleft)


# --------------------------------------------------------------------------- #
# text
# --------------------------------------------------------------------------- #
FONT_FILE = Path(__file__).resolve().parent / "fonts" / "NotoSans-SemiBold.ttf"
# Layout sizes were set for pygame's default font, which pygame draws at
# 0.6875 of the asked size; keep the same visual size with Noto Sans.
FONT_FACTOR = 0.6875


def font(px: int) -> pygame.font.Font:
    if px not in _fonts:
        try:
            f = pygame.font.Font(str(FONT_FILE), px)
        except (OSError, pygame.error):  # pragma: no cover - missing data file
            f = pygame.font.Font(None, round(px / FONT_FACTOR))
        _fonts[px] = f
    return _fonts[px]


def render(s: str, size: int, colour) -> pygame.Surface:
    """Text rendered directly at the real pixel size (anti-aliased)."""
    key = (s, size, tuple(colour), K)
    img = _cache.get(key)
    if img is None:
        if len(_cache) > 3000:
            _cache.clear()
        px = max(6, round(size * K * FONT_FACTOR))
        img = font(px).render(s, True, colour)
        _cache[key] = img
    return img


def measure(s: str, size: int) -> tuple[int, int]:
    """Text size in layout units."""
    img = render(s, size, TEXT)
    return math.ceil(img.get_width() / K), math.ceil(img.get_height() / K)


def ellipsize(text_: str, size: int, max_w: int, left: bool = False) -> str:
    if measure(text_, size)[0] <= max_w:
        return text_
    dots = "…"
    lo, hi = 0, len(text_)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        cand = (dots + text_[-mid:]) if left else (text_[:mid] + dots)
        if measure(cand, size)[0] <= max_w:
            lo = mid
        else:
            hi = mid - 1
    return (dots + text_[-lo:]) if left else (text_[:lo] + dots)


def text(
    surf: pygame.Surface,
    s: str,
    size: int,
    pos: tuple[int, int],
    colour=TEXT,
    align: str = "left",
    max_w: int | None = None,
    shadow: bool = True,
    left_ellipsis: bool = False,
) -> pygame.Rect:
    """Draw text; returns its rectangle in layout units."""
    if max_w:
        s = ellipsize(s, size, max_w, left=left_ellipsis)
    img = render(s, size, colour)
    r = img.get_rect()
    p = pt(pos)
    if align == "center":
        r.midtop = p
    elif align == "right":
        r.topright = p
    else:
        r.topleft = p
    if shadow:
        sh = render(s, size, (0, 0, 0)).copy()
        sh.set_alpha(150)
        off = max(1, round(1.5 * K))
        surf.blit(sh, r.move(off, off))
    surf.blit(img, r)
    return pygame.Rect(int(r.left / K), int(r.top / K), math.ceil(r.width / K), math.ceil(r.height / K))


def wrap(s: str, size: int, max_w: int) -> list[str]:
    out: list[str] = []
    for para in s.split("\n"):
        cur = ""
        for word in para.split(" "):
            cand = f"{cur} {word}".strip()
            if measure(cand, size)[0] <= max_w or not cur:
                cur = cand
            else:
                out.append(cur)
                cur = word
        out.append(cur)
    return out


# --------------------------------------------------------------------------- #
# background
# --------------------------------------------------------------------------- #
class Background:
    """Memory-card-screen style: a clean round lavender glow on dark navy that
    slowly "breathes" on a sine. No dots, spheres or scanlines."""

    CENTRE = (0.5, 0.46)  # fraction of the window
    EDGE = (6, 6, 30)
    MID = (52, 52, 140)
    CORE = (116, 116, 210)

    def __init__(self) -> None:
        w, h = window_size()
        self.base = self._radial(w, h, (self.CORE, self.MID, self.EDGE), 0.78)
        # extra glow, blended on top with a pulsing alpha
        self.glow = self._radial(w, h, ((44, 44, 100), (12, 12, 40), (0, 0, 0)), 0.42)

    def _radial(self, w, h, stops, reach) -> pygame.Surface:
        """Radial gradient through 3 colour stops, computed small and smooth-scaled."""
        step = max(4, math.ceil(w / 200))
        sw, sh = max(2, w // step), max(2, h // step)
        small = pygame.Surface((sw, sh))
        cx, cy = sw * self.CENTRE[0], sh * self.CENTRE[1]
        rmax = math.hypot(max(cx, sw - cx), max(cy, sh - cy)) * reach
        (c0, c1, c2) = stops
        for y in range(sh):
            for x in range(sw):
                d = min(1.0, math.hypot(x - cx, (y - cy) * 1.15) / rmax)
                if d < 0.45:  # core -> mid by 45 %, mid -> edge after
                    u = (d / 0.45) ** 1.3
                    c = [c0[i] + (c1[i] - c0[i]) * u for i in range(3)]
                else:
                    u = ((d - 0.45) / 0.55) ** 0.8
                    c = [c1[i] + (c2[i] - c1[i]) * u for i in range(3)]
                small.set_at((x, y), [int(v) for v in c])
        return pygame.transform.smoothscale(small, (w, h))

    def draw(self, surf: pygame.Surface, t: float) -> None:
        surf.blit(self.base, (0, 0))
        self.glow.set_alpha(int(110 + 110 * math.sin(t * 0.9)))
        surf.blit(self.glow, (0, 0), special_flags=pygame.BLEND_RGB_ADD)


# --------------------------------------------------------------------------- #
# panels and buttons
# --------------------------------------------------------------------------- #
def panel(surf: pygame.Surface, r: pygame.Rect, alpha_fill=PANEL) -> None:
    fill_alpha(surf, r, alpha_fill)
    rr = rc(r)
    x0, y0, x1, y1 = rr.left, rr.top, rr.right - 1, rr.bottom - 1
    w = width(1)
    pygame.draw.line(surf, EDGE_LIGHT, (x0, y0), (x1, y0), w)
    pygame.draw.line(surf, EDGE_LIGHT, (x0, y0), (x0, y1), w)
    pygame.draw.line(surf, EDGE_DARK, (x0, y1), (x1, y1), w)
    pygame.draw.line(surf, EDGE_DARK, (x1, y0), (x1, y1), w)


def glow(surf: pygame.Surface, r: pygame.Rect, t: float, colour=ACCENT) -> None:
    pulse = 0.55 + 0.45 * math.sin(t * 5)
    for i, a in enumerate((70, 40, 18)):
        g = rc(pygame.Rect(r).inflate(4 + i * 4, 4 + i * 4))
        layer = pygame.Surface(g.size, pygame.SRCALPHA)
        pygame.draw.rect(layer, (*colour, int(a * pulse)), layer.get_rect(), width(2), border_radius=sc(4))
        surf.blit(layer, g.topleft)


def button(
    surf: pygame.Surface,
    r: pygame.Rect,
    label: str,
    focused: bool,
    t: float,
    enabled: bool = True,
    glyph: str | None = None,
) -> None:
    """A brushed-metal key, like the menus of the era."""
    top, bot = ((236, 237, 244), (148, 151, 168)) if enabled else ((120, 122, 136), (80, 82, 96))
    rr = rc(r)
    for y in range(rr.height):
        k = y / max(1, rr.height - 1)
        c = [int(top[i] * (1 - k) + bot[i] * k) for i in range(3)]
        pygame.draw.line(surf, c, (rr.left, rr.top + y), (rr.right - 1, rr.top + y))
    pygame.draw.line(surf, (255, 255, 255), rr.topleft, (rr.right - 1, rr.top), width(1))
    pygame.draw.rect(surf, (30, 32, 50), rr, width(1))
    if focused and enabled:
        glow(surf, r, t)
        pygame.draw.rect(surf, (255, 255, 255), rr, width(2))
    colour = (16, 18, 34) if enabled else (54, 56, 70)
    img = render(label, 19, colour)
    gw = sc(28) if glyph else 0
    ir = img.get_rect(center=(rr.centerx + gw // 2, rr.centery + sc(1)))
    if glyph:
        shape(surf, glyph, ((ir.left - sc(16)) / K, rr.centery / K), 11)
    surf.blit(img, ir)


def glyph_image(kind: str, r_px: int) -> pygame.Surface:
    """A face button: black disc of radius r_px with a thick coloured symbol.

    Drawn at 4x and smooth-scaled down for clean edges; cached per size.
    """
    key = (kind, r_px)
    if key in _glyphs:
        return _glyphs[key]
    k = 4
    R = r_px * k
    size = 2 * R + 2
    big = pygame.Surface((size, size), pygame.SRCALPHA)
    c = (size // 2, size // 2)
    black = (0, 0, 0, 255)
    col = _GLYPH_COLOURS[kind]
    t = max(k * 2, int(R * 0.15))  # stroke width
    pygame.draw.circle(big, black, c, R)
    cx, cy = c
    if kind == "circle":
        pygame.draw.circle(big, col, c, int(R * 0.6))
        pygame.draw.circle(big, black, c, int(R * 0.6) - t)
    elif kind == "square":
        h = int(R * 0.5)
        pygame.draw.rect(big, col, pygame.Rect(cx - h, cy - h, 2 * h, 2 * h))
        pygame.draw.rect(big, black, pygame.Rect(cx - h + t, cy - h + t, 2 * (h - t), 2 * (h - t)))
    elif kind == "triangle":
        outer = [(cx, cy - R * 0.68), (cx + R * 0.66, cy + R * 0.46), (cx - R * 0.66, cy + R * 0.46)]
        # shrink toward the incentre by the stroke width for the hollow
        (ax, ay), (bx, by), (qx, qy) = outer
        la = math.dist(outer[1], outer[2])
        lb = math.dist(outer[0], outer[2])
        lc = math.dist(outer[0], outer[1])
        per = la + lb + lc
        ix = (la * ax + lb * bx + lc * qx) / per
        iy = (la * ay + lb * by + lc * qy) / per
        area = abs((bx - ax) * (qy - ay) - (qx - ax) * (by - ay)) / 2
        rho = 2 * area / per
        f = (rho - t) / rho
        inner = [(ix + (x - ix) * f, iy + (y - iy) * f) for x, y in outer]
        pygame.draw.polygon(big, col, outer)
        pygame.draw.polygon(big, black, inner)
    elif kind == "cross":
        arm = R * 0.48
        for sx in (1, -1):
            ex, ey = arm * 0.95, arm * 0.95 * sx
            nx, ny = -ey, ex
            norm = math.hypot(nx, ny)
            nx, ny = nx / norm * t / 2, ny / norm * t / 2
            pts = [(cx - ex + nx, cy - ey + ny), (cx + ex + nx, cy + ey + ny),
                   (cx + ex - nx, cy + ey - ny), (cx - ex - nx, cy - ey - ny)]
            pygame.draw.polygon(big, col, pts)
    img = pygame.transform.smoothscale(big, (size // k, size // k))
    _glyphs[key] = img
    return img


def shape(surf: pygame.Surface, kind: str, centre, r: float) -> None:
    """Draw a face button (cross, circle, triangle, square); layout units."""
    img = glyph_image(kind, max(3, sc(r)))
    surf.blit(img, img.get_rect(center=pt(centre)))


def hints(surf: pygame.Surface, items: list[tuple[str, str]]) -> list[tuple[str, pygame.Rect]]:
    """Bottom bar: (shape, label) pairs, right-aligned. Returns click areas (layout units)."""
    bar = pygame.Rect(0, H - 26, W, 26)
    surf.fill((4, 5, 18), rc(bar))  # opaque: an overlay's hints replace the screen's
    line(surf, (60, 64, 100), bar.topleft, bar.topright)
    x = W - 18
    hits: list[tuple[str, pygame.Rect]] = []
    for kind, label in reversed(items):
        lw = measure(label, 16)[0]
        right = x
        x -= lw
        text(surf, label, 16, (x, bar.top + 6), DIM, shadow=False)
        x -= 17
        circle(surf, (78, 84, 124), (x, bar.centery), 11)  # rim: dark disc on dark bar
        shape(surf, kind, (x, bar.centery), 10)
        hits.append((kind, pygame.Rect(x - 12, bar.top, right - x + 16, bar.height)))
        x -= 26
    return hits


def checkbox(surf: pygame.Surface, r: pygame.Rect, checked: bool) -> None:
    rect(surf, (10, 12, 30), r)
    rect(surf, EDGE_LIGHT, r, 1)
    if checked:
        q = pygame.Rect(r).inflate(-6, -6)
        lines(surf, OK, False, [(q.left, q.centery), (q.centerx - 1, q.bottom), (q.right, q.top)], 2.5)


def progress(surf: pygame.Surface, r: pygame.Rect, frac: float, t: float, busy: bool) -> None:
    rect(surf, (6, 8, 24), r)
    rect(surf, EDGE_LIGHT, r, 1)
    inner = rc(pygame.Rect(r).inflate(-4, -4))
    fw = int(inner.width * max(0.0, min(1.0, frac)))
    for x in range(fw):
        k = x / max(1, inner.width)
        c = (int(60 + 60 * k), int(110 + 70 * k), 255)
        pygame.draw.line(surf, c, (inner.left + x, inner.top), (inner.left + x, inner.bottom - 1))
    if busy:
        seg = sc(60)
        span = inner.width + seg
        x = int((t * 160 * K) % span) - seg
        clip = surf.get_clip()
        surf.set_clip(inner)
        layer = pygame.Surface((seg, inner.height), pygame.SRCALPHA)
        for i in range(seg):
            a = int(120 * math.sin(math.pi * i / seg))
            pygame.draw.line(layer, (255, 255, 255, a), (i, 0), (i, inner.height))
        surf.blit(layer, (inner.left + x, inner.top))
        surf.set_clip(clip)


def disc(surf: pygame.Surface, centre, r: float, t: float) -> None:
    """A spinning compact disc with a rainbow sheen (layout units)."""
    x, y = centre
    circle(surf, (40, 44, 64), (x + 3, y + 4), r)  # shadow
    circle(surf, (196, 200, 214), (x, y), r)
    hues = [(255, 120, 120), (255, 220, 120), (120, 255, 170), (120, 200, 255), (200, 140, 255)]
    base = rc(pygame.Rect(0, 0, 0, 0))
    R = sc(r)
    cx, cy = pt((x, y))
    for i, c in enumerate(hues):
        a0 = t * 2.2 + i * 0.35
        rad = int(R * 0.85) - i * sc(3)
        base = pygame.Rect(0, 0, 2 * rad, 2 * rad)
        base.center = (cx, cy)
        pygame.draw.arc(surf, c, base, a0, a0 + 0.9, width(3))
        pygame.draw.arc(surf, c, base, a0 + math.pi, a0 + math.pi + 0.9, width(3))
    circle(surf, (226, 228, 238), (x, y), r * 0.38)
    circle(surf, (150, 154, 170), (x, y), r * 0.38, 2)
    circle(surf, BG_BOT, (x, y), r * 0.13)
    circle(surf, (120, 124, 140), (x, y), r, 2)
