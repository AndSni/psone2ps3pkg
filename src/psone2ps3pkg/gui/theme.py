"""Drawing primitives for the PSX-style look.

Everything is drawn on a fixed 640x480 canvas (the PS1's own resolution);
pygame.SCALED enlarges the window by a whole factor on big screens.
"""

from __future__ import annotations

import math

import pygame

W, H = 640, 480

BG_TOP = (20, 26, 70)
BG_BOT = (2, 3, 14)
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

# The four face-button colours.
TRIANGLE = (52, 222, 196)
CIRCLE = (238, 106, 98)
CROSS = (156, 170, 230)
SQUARE = (214, 148, 196)
_GLYPH_COLOURS = {"triangle": TRIANGLE, "circle": CIRCLE, "cross": CROSS, "square": SQUARE}

_fonts: dict[tuple[int, bool], pygame.font.Font] = {}


_cache: dict[tuple, pygame.Surface] = {}


def reset_fonts() -> None:
    _fonts.clear()
    _cache.clear()
    _glyphs.clear()


def render(s: str, size: int, colour) -> pygame.Surface:
    """Render at twice the size and scale down: even letter spacing at small sizes."""
    key = (s, size, tuple(colour))
    img = _cache.get(key)
    if img is None:
        if len(_cache) > 2000:
            _cache.clear()
        big = font(size * 2).render(s, True, colour)
        img = pygame.transform.smoothscale_by(big, 0.5)
        _cache[key] = img
    return img


def measure(s: str, size: int) -> tuple[int, int]:
    w, h = font(size * 2).size(s)
    return w // 2, h // 2


def font(size: int, bold: bool = True) -> pygame.font.Font:
    key = (size, bold)
    if key not in _fonts:
        f = pygame.font.Font(None, size)  # pygame's bundled sans
        f.set_bold(False)
        _fonts[key] = f
    return _fonts[key]


# --------------------------------------------------------------------------- #
# background
# --------------------------------------------------------------------------- #
class Background:
    def __init__(self) -> None:
        self.base = pygame.Surface((W, H))
        for y in range(H):
            t = y / (H - 1)
            c = [int(BG_TOP[i] * (1 - t) + BG_BOT[i] * t) for i in range(3)]
            pygame.draw.line(self.base, c, (0, y), (W, y))
        self.scan = pygame.Surface((W, H), pygame.SRCALPHA)
        for y in range(0, H, 3):
            pygame.draw.line(self.scan, (0, 0, 0, 46), (0, y), (W, y))
        self.ribbons = pygame.Surface((W, H), pygame.SRCALPHA)

    def draw(self, surf: pygame.Surface, t: float) -> None:
        surf.blit(self.base, (0, 0))
        self.ribbons.fill((0, 0, 0, 0))
        specs = (
            (0.23, 300, 40, 0.0, (70, 110, 255, 34)),
            (0.17, 330, 55, 1.7, (150, 90, 255, 26)),
            (0.31, 270, 30, 3.1, (90, 200, 255, 22)),
        )
        for speed, base_y, amp, phase, colour in specs:
            top, bot = [], []
            for i in range(0, W + 33, 32):
                x = i
                y = base_y + amp * math.sin(i / 120 + t * speed * 2 + phase)
                thick = 26 + 14 * math.sin(i / 90 - t * speed + phase)
                top.append((x, y - thick))
                bot.append((x, y + thick))
            pygame.draw.polygon(self.ribbons, colour, top + bot[::-1])
        surf.blit(self.ribbons, (0, 0))
        surf.blit(self.scan, (0, 0))


# --------------------------------------------------------------------------- #
# text
# --------------------------------------------------------------------------- #
def ellipsize(text: str, size: int, max_w: int, left: bool = False) -> str:
    if measure(text, size)[0] <= max_w:
        return text
    dots = "…"
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        cand = (dots + text[-mid:]) if left else (text[:mid] + dots)
        if measure(cand, size)[0] <= max_w:
            lo = mid
        else:
            hi = mid - 1
    return (dots + text[-lo:]) if left else (text[:lo] + dots)


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
    if max_w:
        s = ellipsize(s, size, max_w, left=left_ellipsis)
    img = render(s, size, colour)
    r = img.get_rect()
    if align == "center":
        r.midtop = pos
    elif align == "right":
        r.topright = pos
    else:
        r.topleft = pos
    if shadow:
        sh = render(s, size, (0, 0, 0)).copy()
        sh.set_alpha(150)
        surf.blit(sh, r.move(2, 2))
    surf.blit(img, r)
    return r


def wrap(s: str, size: int, max_w: int) -> list[str]:
    lines: list[str] = []
    for para in s.split("\n"):
        cur = ""
        for word in para.split(" "):
            cand = f"{cur} {word}".strip()
            if measure(cand, size)[0] <= max_w or not cur:
                cur = cand
            else:
                lines.append(cur)
                cur = word
        lines.append(cur)
    return lines


# --------------------------------------------------------------------------- #
# panels and buttons
# --------------------------------------------------------------------------- #
def panel(surf: pygame.Surface, rect: pygame.Rect, alpha_fill=PANEL) -> None:
    layer = pygame.Surface(rect.size, pygame.SRCALPHA)
    layer.fill(alpha_fill)
    surf.blit(layer, rect.topleft)
    x0, y0, x1, y1 = rect.left, rect.top, rect.right - 1, rect.bottom - 1
    pygame.draw.line(surf, EDGE_LIGHT, (x0, y0), (x1, y0))
    pygame.draw.line(surf, EDGE_LIGHT, (x0, y0), (x0, y1))
    pygame.draw.line(surf, EDGE_DARK, (x0, y1), (x1, y1))
    pygame.draw.line(surf, EDGE_DARK, (x1, y0), (x1, y1))


def glow(surf: pygame.Surface, rect: pygame.Rect, t: float, colour=ACCENT) -> None:
    pulse = 0.55 + 0.45 * math.sin(t * 5)
    for i, a in enumerate((70, 40, 18)):
        g = rect.inflate(4 + i * 4, 4 + i * 4)
        layer = pygame.Surface(g.size, pygame.SRCALPHA)
        pygame.draw.rect(layer, (*colour, int(a * pulse)), layer.get_rect(), 2, border_radius=4)
        surf.blit(layer, g.topleft)


def button(
    surf: pygame.Surface,
    rect: pygame.Rect,
    label: str,
    focused: bool,
    t: float,
    enabled: bool = True,
    glyph: str | None = None,
) -> None:
    """A brushed-metal key, like the menus of the era."""
    top, bot = ((236, 237, 244), (148, 151, 168)) if enabled else ((120, 122, 136), (80, 82, 96))
    h = rect.height
    for y in range(h):
        k = y / max(1, h - 1)
        c = [int(top[i] * (1 - k) + bot[i] * k) for i in range(3)]
        pygame.draw.line(surf, c, (rect.left, rect.top + y), (rect.right - 1, rect.top + y))
    pygame.draw.line(surf, (255, 255, 255), rect.topleft, (rect.right - 1, rect.top))
    pygame.draw.rect(surf, (30, 32, 50), rect, 1)
    if focused and enabled:
        glow(surf, rect, t)
        pygame.draw.rect(surf, (255, 255, 255), rect, 2)
    colour = (16, 18, 34) if enabled else (54, 56, 70)
    img = render(label, 19, colour)
    gw = 0
    if glyph:
        gw = 28
    r = img.get_rect(center=(rect.centerx + gw // 2, rect.centery + 1))
    if glyph:
        shape(surf, glyph, (r.left - 16, rect.centery), 11)
    surf.blit(img, r)


_glyphs: dict[tuple[str, int], pygame.Surface] = {}


def glyph(kind: str, r: int) -> pygame.Surface:
    """A face button: black disc of radius r with a thick coloured symbol.

    Drawn at 4x and smooth-scaled down for clean edges; cached per size.
    """
    key = (kind, r)
    if key in _glyphs:
        return _glyphs[key]
    k = 4
    R = r * k
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


def shape(surf: pygame.Surface, kind: str, centre: tuple[int, int], r: int) -> None:
    """Draw a face button (cross, circle, triangle, square) centred on `centre`."""
    img = glyph(kind, r)
    surf.blit(img, img.get_rect(center=centre))


def hints(surf: pygame.Surface, items: list[tuple[str, str]]) -> None:
    """Bottom bar: (shape, label) pairs, right-aligned."""
    bar = pygame.Rect(0, H - 26, W, 26)
    surf.fill((4, 5, 18), bar)  # opaque: an overlay's hints replace the screen's
    pygame.draw.line(surf, (60, 64, 100), bar.topleft, bar.topright)
    x = W - 18
    for kind, label in reversed(items):
        img = render(label, 16, DIM)
        x -= img.get_width()
        surf.blit(img, (x, bar.top + 7))
        x -= 17
        pygame.draw.circle(surf, (78, 84, 124), (x, bar.centery), 11)  # rim: dark disc on dark bar
        shape(surf, kind, (x, bar.centery), 10)
        x -= 26


def checkbox(surf: pygame.Surface, rect: pygame.Rect, checked: bool) -> None:
    pygame.draw.rect(surf, (10, 12, 30), rect)
    pygame.draw.rect(surf, EDGE_LIGHT, rect, 1)
    if checked:
        r = rect.inflate(-6, -6)
        pygame.draw.lines(surf, OK, False, [(r.left, r.centery), (r.centerx - 1, r.bottom), (r.right, r.top)], 3)


def progress(surf: pygame.Surface, rect: pygame.Rect, frac: float, t: float, busy: bool) -> None:
    pygame.draw.rect(surf, (6, 8, 24), rect)
    pygame.draw.rect(surf, EDGE_LIGHT, rect, 1)
    inner = rect.inflate(-4, -4)
    fill = inner.copy()
    fill.width = int(inner.width * max(0.0, min(1.0, frac)))
    if fill.width:
        for x in range(fill.width):
            k = x / max(1, inner.width)
            c = (int(60 + 60 * k), int(110 + 70 * k), 255)
            pygame.draw.line(surf, c, (fill.left + x, fill.top), (fill.left + x, fill.bottom - 1))
    if busy:
        seg = 60
        span = inner.width + seg
        x = int((t * 160) % span) - seg
        clip = surf.get_clip()
        surf.set_clip(inner)
        layer = pygame.Surface((seg, inner.height), pygame.SRCALPHA)
        for i in range(seg):
            a = int(120 * math.sin(math.pi * i / seg))
            pygame.draw.line(layer, (255, 255, 255, a), (i, 0), (i, inner.height))
        surf.blit(layer, (inner.left + x, inner.top))
        surf.set_clip(clip)


def disc(surf: pygame.Surface, centre: tuple[int, int], r: int, t: float) -> None:
    """A spinning compact disc with a rainbow sheen."""
    x, y = centre
    pygame.draw.circle(surf, (40, 44, 64), (x + 3, y + 4), r)  # shadow
    pygame.draw.circle(surf, (196, 200, 214), (x, y), r)
    hues = [(255, 120, 120), (255, 220, 120), (120, 255, 170), (120, 200, 255), (200, 140, 255)]
    rect = pygame.Rect(0, 0, int(r * 1.7), int(r * 1.7))
    rect.center = (x, y)
    for i, c in enumerate(hues):
        a0 = t * 2.2 + i * 0.35
        pygame.draw.arc(surf, c, rect.inflate(-i * 6, -i * 6), a0, a0 + 0.9, 3)
        pygame.draw.arc(surf, c, rect.inflate(-i * 6, -i * 6), a0 + math.pi, a0 + math.pi + 0.9, 3)
    pygame.draw.circle(surf, (226, 228, 238), (x, y), int(r * 0.38))
    pygame.draw.circle(surf, (150, 154, 170), (x, y), int(r * 0.38), 2)
    pygame.draw.circle(surf, BG_BOT, (x, y), int(r * 0.13))
    pygame.draw.circle(surf, (120, 124, 140), (x, y), r, 2)
