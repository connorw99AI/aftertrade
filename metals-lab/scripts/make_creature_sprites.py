"""Cut the 5x2 creature sheet into transparent sprites and recolour each strategy's long and short half.

Usage (needs numpy, pillow, scipy; not part of the lab's runtime dependencies):
    python scripts/make_creature_sprites.py creatures-sheet.png dashboard/public/creatures
Colours per half are set in LONG and SHORT below.
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

# Colour per half. None keeps the original art untouched. A number is a hue; a tuple is (hue, saturation x, brightness x)
# for lighter shades; "ice" is silver-white. Rules: every long differs from every other long, every short from every
# other short, no short matches its twin, no long is red, no short is blue or green.
LONG = {1: None, 2: None, 3: None, 4: 200, 5: None, 6: "ice", 7: None, 8: None, 9: 95, 10: None}
SHORT = {1: (340, 0.65, 1.12), 2: 305, 3: 28, 4: "ice", 5: (262, 0.5, 1.15), 6: 22, 7: 0, 8: (38, 1.25, 1.1), 9: 272, 10: (55, 1.25, 1.25)}

STONE = {6, 9}  # grey-bodied golems: only the glowing accents change, the stone keeps its detail

def recolour(rgb, al, target, stone=False):
    if target is None:
        return rgb
    h, s, v = rgb2hsv(rgb)
    w = al > 0.3
    sat = w & (s > 0.28) & (v > 0.18)
    if target == "ice":  # silver-white with a cold hint
        m = sat if stone else w
        return hsv2rgb(np.where(m, 205.0, h), np.where(m, np.clip(s * 0.18, 0, 0.14), s), np.where(m, np.clip(v * 1.18 + 0.06, 0, 1), v))
    hue, smul, vmul = target if isinstance(target, tuple) else (target, 1.0, 1.0)
    dom = np.degrees(np.angle(np.mean(np.exp(1j * np.radians(h[sat])))))
    # only the body colour changes: eyes, bellies and trim in other colours keep their original look
    sat = sat & (np.abs(((h - dom + 180) % 360) - 180) < 70)
    h2 = (h + (hue - dom)) % 360
    # pull saturated pixels towards the target so a multi-coloured body reads as one colour
    diff = ((h2 - hue + 180) % 360) - 180
    h2 = (hue + diff * 0.45) % 360
    s2 = np.where(sat, np.clip(s * smul, 0, 1), s)
    v2 = np.where(sat, np.clip(v * vmul, 0, 1), v)
    return hsv2rgb(np.where(sat, h2, h), s2, v2)

a = np.asarray(Image.open(sheet).convert("RGB")).astype(float)
bg = np.median(np.concatenate([a[:20].reshape(-1, 3), a[-20:].reshape(-1, 3)]), axis=0)
H, W = a.shape[:2]
cw, ch = W / 5, H / 2
for i in range(10):
    r, c = divmod(i, 5)
    box = (int(c * cw), int(r * ch), int((c + 1) * cw), int((r + 1) * ch))
    rgb, al = cut(a, box, bg)
    sid = i + 1
    save(recolour(rgb, al, LONG[sid], sid in STONE), al, f"{out}/S{sid:02d}L.png", 320)
    save(recolour(rgb, al, SHORT[sid], sid in STONE), al, f"{out}/S{sid:02d}S.png", 320)

print("ok")
