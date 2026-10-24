"""Cut the 5x2 creature sheet into transparent sprites and recolour each strategy's long and short half.

Usage (needs numpy, pillow, scipy; not part of the lab's runtime dependencies):
    python scripts/make_creature_sprites.py creatures-sheet.png dashboard/public/creatures
Every long half gets its own colour (LONG_HUE) and every short half its own (SHORT_HUE).
After changing colours, update COLOUR in dashboard/components/Creature.tsx and rebuild the dashboard.
"""
import sys
import numpy as np
from PIL import Image
from scipy import ndimage

sheet, out = sys.argv[1], sys.argv[2]

def alpha_of(a, bg):
    d = np.abs(a - bg).max(axis=2)
    return np.clip((d - 8) / 38.0, 0, 1)

def cut(a, box, bg, pad=6):
    x0, y0, x1, y1 = box
    sub = a[y0:y1, x0:x1]
    al = alpha_of(sub, bg)
    lab, n = ndimage.label(al > 0.35)
    if n == 0:
        raise SystemExit(f"nothing in {box}")
    sizes = ndimage.sum(np.ones_like(lab), lab, range(1, n + 1))
    keep = np.isin(lab, [i + 1 for i, s in enumerate(sizes) if s > sizes.max() * 0.02])
    keep = ndimage.binary_dilation(keep, iterations=10)
    al = al * keep
    ys, xs = np.where(al > 0.05)
    yy0, yy1, xx0, xx1 = max(0, ys.min() - pad), min(sub.shape[0], ys.max() + pad), max(0, xs.min() - pad), min(sub.shape[1], xs.max() + pad)
    rgb = sub[yy0:yy1, xx0:xx1]
    return rgb, al[yy0:yy1, xx0:xx1]

def save(rgb, al, path, h):
    im = Image.fromarray(np.dstack([np.clip(rgb, 0, 255), al * 255]).astype(np.uint8), "RGBA")
    w = round(im.width * h / im.height)
    im.resize((w, h), Image.LANCZOS).save(path, optimize=True)

def rgb2hsv(c):
    c = c / 255.0
    mx, mn = c.max(2), c.min(2)
    d = mx - mn
    h = np.zeros_like(mx)
    r, g, b = c[..., 0], c[..., 1], c[..., 2]
    m = d > 1e-6
    i = m & (mx == r); h[i] = ((g - b)[i] / d[i]) % 6
    i = m & (mx == g); h[i] = (b - r)[i] / d[i] + 2
    i = m & (mx == b); h[i] = (r - g)[i] / d[i] + 4
    return h * 60, np.where(mx > 0, d / np.maximum(mx, 1e-6), 0), mx

def hsv2rgb(h, s, v):
    c = v * s; x = c * (1 - np.abs((h / 60) % 2 - 1)); m = v - c
    z = np.zeros_like(h); k = (h // 60).astype(int) % 6
    r = np.select([k == 0, k == 1, k == 2, k == 3, k == 4, k == 5], [c, x, z, z, x, c])
    g = np.select([k == 0, k == 1, k == 2, k == 3, k == 4, k == 5], [x, c, c, x, z, z])
    b = np.select([k == 0, k == 1, k == 2, k == 3, k == 4, k == 5], [z, z, x, c, c, x])
    return np.dstack([r + m, g + m, b + m]) * 255

# Ten evenly spread hues. Every long has its own, every short has its own, and no short matches its twin.
LONG_HUE = {1: 28, 2: 140, 3: 228, 4: 200, 5: 318, 6: 0, 7: 52, 8: 272, 9: "ice", 10: 172}
SHORT_HUE = {1: 200, 2: 318, 3: 28, 4: "ice", 5: 172, 6: 228, 7: 0, 8: 140, 9: 272, 10: 52}

STONE = {6, 9}  # grey-bodied creatures: tint the stone itself, not just the accents

def recolour(rgb, al, target, stone=False):
    h, s, v = rgb2hsv(rgb)
    if target == "ice":  # silver-white: almost no colour, brighter, a cold blue hint
        w = al > 0.3
        return hsv2rgb(np.full_like(h, 205.0), np.where(w, np.clip(s * 0.18, 0, 0.14), s), np.where(w, np.clip(v * 1.18 + 0.06, 0, 1), v))
    w = al > 0.3
    sat = w & (s > 0.28) & (v > 0.18)
    if not stone and sat.sum() > w.sum() * 0.25:  # colourful creature: rotate its dominant hue to the target
        dom = np.degrees(np.angle(np.mean(np.exp(1j * np.radians(h[sat])))))
        h2 = (h + (target - dom)) % 360
        # pull every saturated pixel close to the target so multi-coloured bodies read as one colour
        diff = ((h2 - target + 180) % 360) - 180
        h2 = (target + diff * 0.45) % 360
        return hsv2rgb(np.where(sat, h2, h), s, v)
    # mostly grey creature (stone golems): tint the stone and recolour the glowing accents
    h2 = np.full_like(h, float(target))
    s2 = np.where(sat, np.maximum(s, 0.6), np.clip(s + 0.5, 0.42, 0.62))
    v = np.where(sat, v, np.clip(v * 1.12, 0, 1))
    return hsv2rgb(h2, s2, v)

a = np.asarray(Image.open(sheet).convert("RGB")).astype(float)
bg = np.median(np.concatenate([a[:20].reshape(-1, 3), a[-20:].reshape(-1, 3)]), axis=0)
H, W = a.shape[:2]
cw, ch = W / 5, H / 2
for i in range(10):
    r, c = divmod(i, 5)
    box = (int(c * cw), int(r * ch), int((c + 1) * cw), int((r + 1) * ch))
    rgb, al = cut(a, box, bg)
    sid = i + 1
    save(recolour(rgb, al, LONG_HUE[sid], sid in STONE), al, f"{out}/S{sid:02d}L.png", 320)
    save(recolour(rgb, al, SHORT_HUE[sid], sid in STONE), al, f"{out}/S{sid:02d}S.png", 320)

print("ok")
