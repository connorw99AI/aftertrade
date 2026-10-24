/* 32x32 pixel art for the Assessor. The trading bots use the creature sprites in public/creatures/. */

export type Px = { x: number; y: number; c: string; layer: "body" | "eye" | "acc" };
export type Art = { px: Px[]; sky: [string, string]; ground: string; stars: [number, number][]; species: string };

const N = 32;

/** The assessor: a golden orb-sage with a monocle and a ledger. */
export function assessorArt(): Art {
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
