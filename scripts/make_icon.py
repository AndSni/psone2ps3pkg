"""Draw the app icon (256x256 PNG): a disc on a dark tile with the four face shapes.

    python scripts/make_icon.py
writes src/psone2ps3pkg/gui/icon.png and packaging/psone2ps3pkg.png.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

import pygame  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
S = 1024  # draw big, scale down for smooth edges


def main() -> None:
    pygame.init()
    surf = pygame.Surface((S, S), pygame.SRCALPHA)
    tile = pygame.Rect(40, 40, S - 80, S - 80)
    grad = pygame.Surface(tile.size, pygame.SRCALPHA)
    for y in range(tile.height):
        k = y / tile.height
        c = (int(28 * (1 - k) + 4 * k), int(36 * (1 - k) + 6 * k), int(96 * (1 - k) + 22 * k))
        pygame.draw.line(grad, c, (0, y), (tile.width, y))
    mask = pygame.Surface(tile.size, pygame.SRCALPHA)
    pygame.draw.rect(mask, (255, 255, 255, 255), mask.get_rect(), border_radius=180)
    grad.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
    surf.blit(grad, tile.topleft)
    pygame.draw.rect(surf, (150, 160, 210), tile, 10, border_radius=180)

    cx, cy, r = S // 2, S // 2 - 56, 300
    pygame.draw.circle(surf, (20, 20, 40, 160), (cx + 14, cy + 18), r)
    pygame.draw.circle(surf, (204, 208, 222), (cx, cy), r)
    hues = [(255, 120, 120), (255, 214, 110), (110, 240, 160), (110, 190, 255), (190, 130, 255)]
    rect = pygame.Rect(0, 0, int(r * 1.72), int(r * 1.72))
    rect.center = (cx, cy)
    for i, c in enumerate(hues):
        pygame.draw.arc(surf, c, rect.inflate(-i * 28, -i * 28), 0.5, 1.6, 14)
        pygame.draw.arc(surf, c, rect.inflate(-i * 28, -i * 28), 3.64, 4.74, 14)
    pygame.draw.circle(surf, (232, 234, 242), (cx, cy), int(r * 0.42))
    pygame.draw.circle(surf, (140, 146, 168), (cx, cy), int(r * 0.42), 8)
    pygame.draw.circle(surf, (8, 10, 30), (cx, cy), int(r * 0.13))
    pygame.draw.circle(surf, (110, 116, 140), (cx, cy), r, 8)

    # face shapes along the bottom
    from psone2ps3pkg.gui.theme import shape  # same face buttons as the app

    y = S - 150
    xs = [S // 2 - 276, S // 2 - 92, S // 2 + 92, S // 2 + 276]
    for x, kind in zip(xs, ("triangle", "circle", "cross", "square")):
        pygame.draw.circle(surf, (170, 178, 220), (x, y), 84)  # light rim on the dark tile
        shape(surf, kind, (x, y), 80)

    out = pygame.transform.smoothscale(surf, (256, 256))
    a = ROOT / "src" / "psone2ps3pkg" / "gui" / "icon.png"
    pygame.image.save(out, str(a))
    shutil.copy(a, ROOT / "packaging" / "psone2ps3pkg.png")
    print("wrote", a)


if __name__ == "__main__":
    main()
