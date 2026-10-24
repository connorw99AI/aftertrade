/* Procedural 32x32 pixel aliens: one species per strategy; the long half wears bull horns, the short half its hood.
   Pure data (no React) so it can be previewed outside the app. Deterministic per bot id. */

export type Px = { x: number; y: number; c: string; layer: "body" | "eye" | "acc" };
export type Art = { px: Px[]; sky: [string, string]; ground: string; stars: [number, number][]; species: string };

const N = 32;

function hash(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); }
  return h >>> 0;
}
function rng(seed: number) {
  let a = seed;
  return () => { a |= 0; a = (a + 0x6d2b79f5) | 0; let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
}
function hsl(h: number, s: number, l: number): string {
  s /= 100; l /= 100;
  const k = (n: number) => (n + h / 30) % 12, a = s * Math.min(l, 1 - l);
  const f = (n: number) => l - a * Math.max(-1, Math.min(k(n) - 3, Math.min(9 - k(n), 1)));
  const to = (v: number) => Math.round(v * 255).toString(16).padStart(2, "0");
  return `#${to(f(0))}${to(f(8))}${to(f(4))}`;
}

type Shape = { name: string; inside: (x: number, y: number) => boolean; eyeY: number; legs: "two" | "four" | "tentacles" | "none" | "stem" };
const cx = 15.5;
const SHAPES: Shape[] = [
  { name: "blob", inside: (x, y) => ((x - cx) / 11) ** 2 + ((y - 19) / 9) ** 2 <= 1, eyeY: 15, legs: "two" },
  { name: "tall", inside: (x, y) => ((x - cx) / 7.5) ** 2 + ((y - 17) / 11.5) ** 2 <= 1, eyeY: 11, legs: "two" },
  { name: "squat", inside: (x, y) => ((x - cx) / 13) ** 2 + ((y - 22) / 6.5) ** 2 <= 1, eyeY: 19, legs: "four" },
  { name: "pear", inside: (x, y) => y >= 8 && y <= 27 && Math.abs(x - cx) <= 4 + (y - 8) * 0.5 - Math.max(0, y - 23) * 1.4, eyeY: 13, legs: "two" },
  { name: "mushroom", inside: (x, y) => (y <= 17 && ((x - cx) / 13) ** 2 + ((y - 15) / 8) ** 2 <= 1) || (y > 15 && y <= 27 && Math.abs(x - cx) <= 5), eyeY: 20, legs: "stem" },
  { name: "jelly", inside: (x, y) => y <= 20 && ((x - cx) / 11) ** 2 + ((y - 17) / 10) ** 2 <= 1, eyeY: 13, legs: "tentacles" },
  { name: "crystal", inside: (x, y) => Math.abs(x - cx) / 10 + Math.abs(y - 17) / 12 <= 1, eyeY: 15, legs: "none" },
  { name: "insect", inside: (x, y) => ((x - cx) / 6) ** 2 + ((y - 10) / 5) ** 2 <= 1 || ((x - cx) / 9) ** 2 + ((y - 21) / 7) ** 2 <= 1, eyeY: 9, legs: "four" },
  { name: "orb", inside: (x, y) => ((x - cx) / 9.5) ** 2 + ((y - 16) / 9.5) ** 2 <= 1, eyeY: 14, legs: "none" },
  { name: "slug", inside: (x, y) => y >= 17 && ((x - cx) / 14) ** 2 + ((y - 23) / 5.5) ** 2 <= 1 || (y >= 13 && y < 18 && Math.abs(x - cx) <= 8), eyeY: 16, legs: "none" },
];

/* One species per strategy: both halves are the same creature. The long half (bull) wears horns on a green scene,
   the short half (bear) has its hood up, a different body colour and a dark red scene. */
const SPECIES_BY_STRATEGY: Record<string, string> = {
  S01: "Glimmerkin", S02: "Spirelop", S03: "Mudwaddle", S04: "Peardrop", S05: "Capcrawler",
  S06: "Jellywisp", S07: "Shardback", S08: "Chitterwing", S09: "Orbling", S10: "Slugmallow",
};
export const SPECIES: Record<string, string> = Object.fromEntries(
  Object.entries(SPECIES_BY_STRATEGY).flatMap(([sid, n]) => [[`${sid}L`, `Bull ${n}`], [`${sid}S`, `Hooded ${n}`]]));

export function alienArt(bot: string): Art {
  if (bot === "ASSESSOR") return assessorArt();
  const sid = bot.slice(0, 3);
  const idx = Math.max(0, parseInt(bot.slice(1, 3), 10) - 1);
  const short = bot.endsWith("S");
  const r = rng(hash(sid)); // same draws for both halves: same pattern, eyes and mouth
  const shape = SHAPES[idx % SHAPES.length];
  const jitter = Math.floor(r() * 18);
  const hue = (idx * 36 + jitter + (short ? 150 : 0)) % 360;
  const base = hsl(hue, 58, 56), shade = hsl(hue, 52, 38), light = hsl(hue, 70, 74), outline = "#14131F";
  const pattern = hsl((hue + 40) % 360, 55, 45);
  const grid: (string | null)[][] = Array.from({ length: N }, () => Array(N).fill(null));
  const inside = (x: number, y: number) => x >= 0 && y >= 0 && x < N && y < N && shape.inside(x, y);
  // body with light from top-left
  for (let y = 0; y < N; y++) for (let x = 0; x < N; x++) {
    if (!inside(x, y)) continue;
    const edgeBR = !inside(x + 1, y) || !inside(x, y + 1) || !inside(x + 1, y + 1);
    const edgeTL = !inside(x - 1, y) || !inside(x, y - 1);
    grid[y][x] = edgeBR ? shade : edgeTL ? light : base;
  }
  // pattern: spots or stripes (mirrored)
  const spots = r() < 0.5;
  for (let y = 0; y < N; y++) for (let x = 0; x < 16; x++) {
    if (!grid[y][x] || grid[y][x] !== base) continue;
    const hit = spots ? (x * 7 + y * 13 + idx) % 23 === 0 : (y + Math.floor(x / 3)) % 6 === 0 && y > shape.eyeY + 3;
    if (hit) { grid[y][x] = pattern; grid[y][31 - x] = pattern; }
  }
  // legs / tentacles / stem
  const bottom = (x: number) => { for (let y = N - 1; y >= 0; y--) if (grid[y][x]) return y; return -1; };
  const leg = (x: number, len: number) => { const b = bottom(x); if (b < 0) return; for (let i = 1; i <= len && b + i < N - 1; i++) grid[b + i][x] = shade; };
  if (shape.legs === "two") { [11, 12, 19, 20].forEach((x) => leg(x, 3)); }
  if (shape.legs === "four") { [7, 8, 13, 18, 23, 24].forEach((x) => leg(x, 3)); }
  if (shape.legs === "tentacles") {
    for (const x0 of [8, 12, 16, 20, 24]) { const b = bottom(Math.min(31, x0)); for (let i = 1; i <= 9 && b + i < N - 1; i++) grid[b + i][x0 + (i % 4 < 2 ? 0 : 1) - 1 + (x0 > 16 ? 1 : 0)] = i % 3 === 0 ? pattern : shade; }
  }
  const ey = shape.eyeY;
  // hood (short half): cloth over the head down to the eyes, framing the face, one pixel proud of the body, with a peak
  if (short) {
    const cloth = hsl((hue + 200) % 360, 28, 26), clothLight = hsl((hue + 200) % 360, 30, 38), rim = hsl((hue + 200) % 360, 35, 48);
    const hoodTo = ey + 3;
    const body = grid.map((row) => row.map((c) => c !== null));
    const isBody = (x: number, y: number) => body[y]?.[x] === true;
    for (let y = 0; y <= Math.min(N - 1, hoodTo); y++) for (let x = 0; x < N; x++) {
      const near = [[1, 0], [-1, 0], [0, -1], [1, -1], [-1, -1]].some(([dx, dy]) => isBody(x + dx, y + dy));
      if (isBody(x, y)) {
        let edge = 99;
        for (let d = 1; d <= 3; d++) if (!isBody(x - d, y) || !isBody(x + d, y)) { edge = d; break; }
        if (y <= ey - 2) grid[y][x] = y === ey - 2 ? rim : (x < 16 ? clothLight : cloth);
        else if (edge <= 2) grid[y][x] = edge === 2 ? rim : cloth;
      } else if (near && y >= 1) grid[y][x] = cloth;
    }
    // peak on top, leaning back
    let t = 0;
    for (let y = 0; y < N; y++) if (grid[y][15] || grid[y][16]) { t = y; break; }
    for (const [dx, dy] of [[0, -1], [1, -1], [1, -2], [2, -2], [2, -3]]) if (t + dy >= 0) grid[t + dy][15 + dx] = cloth;
  } else {
    // arms
    let w = 0;
    for (let x = 0; x < 16; x++) if (grid[ey + 3]?.[x]) { w = x; break; }
    if (w > 2) for (let i = 1; i <= 3; i++) { grid[ey + 3 + (i === 3 ? 1 : 0)][w - i] = shade; grid[ey + 3 + (i === 3 ? 1 : 0)][31 - (w - i)] = shade; }
  }
  // outline
  const out: (string | null)[][] = grid.map((row) => row.slice());
  for (let y = 0; y < N; y++) for (let x = 0; x < N; x++) {
    if (grid[y][x]) continue;
    const nb = [[1, 0], [-1, 0], [0, 1], [0, -1]].some(([dx, dy]) => grid[y + dy]?.[x + dx]);
    if (nb) out[y][x] = outline;
  }
  const px: Px[] = [];
  for (let y = 0; y < N; y++) for (let x = 0; x < N; x++) if (out[y][x]) px.push({ x, y, c: out[y][x] as string, layer: "body" });
  // eyes
  const nEyes = 1 + Math.floor(r() * 3);
  const eyeXs = nEyes === 1 ? [15] : nEyes === 2 ? [11, 19] : [9, 15, 21];
  const big = nEyes === 1;
  for (const ex of eyeXs) {
    const w = big ? 4 : 3;
    for (let dy = 0; dy < 3; dy++) for (let dx = 0; dx < w; dx++) px.push({ x: ex - Math.floor(w / 2) + dx, y: ey - 1 + dy, c: "#FFFFFF", layer: "eye" });
    px.push({ x: ex, y: ey, c: outline, layer: "eye" });
    if (big) px.push({ x: ex + 1, y: ey, c: outline, layer: "eye" });
  }
  // mouth
  const my = ey + 4;
  const mouth = Math.floor(r() * 3);
  for (let dx = -2; dx <= 2; dx++) {
    if (mouth === 0) px.push({ x: 15 + dx + (dx > 0 ? 1 : 0), y: my + (Math.abs(dx) === 2 ? -1 : 0), c: outline, layer: "body" });
    if (mouth === 1 && Math.abs(dx) <= 1) px.push({ x: 15 + dx + (dx > 0 ? 1 : 0), y: my, c: outline, layer: "body" });
  }
  if (mouth === 2) { px.push({ x: 14, y: my, c: outline, layer: "body" }, { x: 17, y: my, c: outline, layer: "body" }, { x: 15, y: my + 1, c: "#FFFFFF", layer: "body" }, { x: 16, y: my + 1, c: "#FFFFFF", layer: "body" }); }
  // bull horns (long half), plus a chest badge on both
  const top = (x: number) => { for (let y = 0; y < N; y++) if (out[y][x]) return y; return 10; };
  if (!short) {
    const horn = "#F2E6C9", hornShade = "#C9B48A";
    for (const side of [-1, 1]) {
      const bx = side < 0 ? 10 : 21;
      const ty = top(bx);
      const pts: [number, number, string][] = [[0, 0, hornShade], [0, -1, horn], [side, -1, horn], [side, -2, horn], [side * 2, -2, horn],
        [side * 2, -1, hornShade], [side * 3, -2, horn], [side * 3, -3, horn], [side * 4, -3, hornShade], [side * 4, -4, horn], [side * 4, -5, "#FFFFFF"]];
      for (const [dx, dy, c] of pts) px.push({ x: bx + dx, y: ty + dy, c, layer: "acc" });
      for (const [dx, dy] of [[side * 5, -3], [side * 5, -4], [side * 5, -5], [side * 4, -6], [side * 3, -4], [side * 2, -3], [side, -3], [0, -2], [-side, -1], [-side, 0]])
        px.push({ x: bx + dx, y: ty + dy, c: outline, layer: "acc" });
    }
    for (const [dx, dy] of [[0, 0], [-1, 1], [0, 1], [1, 1]]) px.push({ x: 15 + dx, y: Math.min(29, my + 4) + dy, c: "#4FCB8A", layer: "acc" });
  } else {
    for (const [dx, dy] of [[-1, 0], [0, 0], [1, 0], [0, 1]]) px.push({ x: 15 + dx, y: Math.min(29, my + 4) + dy, c: "#E0604A", layer: "acc" });
  }
  const stars: [number, number][] = Array.from({ length: 5 }, () => [Math.floor(r() * 32), Math.floor(r() * 9)]);
  return { px, sky: short ? ["#1F0B0E", "#3A1418"] : ["#0A1E14", "#123623"], ground: short ? "#4A1F22" : "#1F4A30", stars,
           species: SPECIES[bot] ?? bot };
}


/** The assessor: a golden orb-sage with a monocle and a ledger. */
function assessorArt(): Art {
  const base = "#E3B341", shade = "#A97A1E", light = "#F7D97A", outline = "#14131F";
  const inside = (x: number, y: number) => ((x - 15.5) / 10) ** 2 + ((y - 15) / 10) ** 2 <= 1;
  const px: Px[] = [];
  for (let y = 0; y < N; y++) for (let x = 0; x < N; x++) {
    if (inside(x, y)) {
      const br = !inside(x + 1, y) || !inside(x, y + 1), tl = !inside(x - 1, y) || !inside(x, y - 1);
      px.push({ x, y, c: br ? shade : tl ? light : base, layer: "body" });
    } else if (inside(x + 1, y) || inside(x - 1, y) || inside(x, y + 1) || inside(x, y - 1)) px.push({ x, y, c: outline, layer: "body" });
  }
  for (const ex of [11, 20]) {
    for (let dy = 0; dy < 3; dy++) for (let dx = 0; dx < 3; dx++) px.push({ x: ex - 1 + dx, y: 12 + dy, c: "#FFFFFF", layer: "eye" });
    px.push({ x: ex, y: 13, c: outline, layer: "eye" });
  }
  for (const [x, y] of [[18, 11], [19, 10], [20, 10], [21, 10], [22, 11], [23, 12], [23, 13], [23, 14], [22, 15], [21, 16], [20, 16], [19, 16], [18, 15], [17, 14], [17, 13], [17, 12], [23, 15], [24, 16], [24, 17], [24, 18]])
    px.push({ x, y, c: x === 24 && y > 15 ? "#C9D1DE" : "#C9D1DE", layer: "acc" });
  for (const dx of [-2, -1, 0, 1, 2]) px.push({ x: 15 + dx + (dx > 0 ? 1 : 0), y: 19 + (Math.abs(dx) === 2 ? -1 : 0), c: outline, layer: "body" });
  for (let y = 22; y < 29; y++) for (let x = 6; x < 13; x++) px.push({ x, y, c: y === 22 || x === 6 || x === 12 || y === 28 ? "#5A3A24" : (y % 2 ? "#F2EEE4" : "#D8D0BE"), layer: "acc" });
  for (let y = 25; y < 28; y++) for (let x = 20; x < 26; x++) px.push({ x, y, c: shade, layer: "body" });
  return { px, sky: ["#1E2A3A", "#2D3F55"], ground: "#3A3550", stars: [[3, 2], [27, 4], [8, 6], [22, 1], [29, 8]], species: "The Assessor" };
}
