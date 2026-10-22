"use client";
/* Ten hand-drawn 12x12 pixel creatures, one per strategy. Rendered as crisp SVG rects.
   Legend: o outline, a body, b shade, c accent, e eye white, p pupil, . empty */

type Sprite = { name: string; rows: string[]; a: string; b: string; c: string };

export const CREATURES: Record<string, Sprite> = {
  S01: { name: "Dart the fox", a: "#E58B3A", b: "#B5602A", c: "#F6E7D2", rows: [
    "o.........o.", "oo.......oo.", "oao.....oao.", "oaaoooooaao.", "oaaaaaaaaao.", "oaepaaaepao.",
    "oaaaaaaaaao.", ".oacccccao..", "..oaaaaao...", "..oabbbao...", ".oao...oao..", ".oo.....oo.."] },
  S02: { name: "Lune the owl", a: "#7D86C9", b: "#5A619C", c: "#E3B341", rows: [
    "..o......o..", "..oo....oo..", "..oaoooooao.", ".oaaaaaaaaao", ".oeeaaaaeeao", ".oeppaaeppao",
    ".oeeacaaeeao", ".oaaaccaaaao", ".oabbabbabao", ".oabbbbbbbao", "..oaaaaaaao.", "...oc...co.."] },
  S03: { name: "Fib the frog", a: "#6FBF5A", b: "#4B8F3C", c: "#F2D06B", rows: [
    "............", "..ooo..ooo..", ".oepo..oepo.", ".oaaooooaao.", "oaaaaaaaaaao", "oaaaaaaaaaao",
    "oacccccccaao", ".oaaaaaaaao.", ".oabbbbbbao.", "oaao....oaao", "oo........oo", "............"] },
  S04: { name: "Bell the rooster", a: "#F2E6CF", b: "#C9B79A", c: "#D0654A", rows: [
    "....ccc.....", "...ccccc....", "...oaaao....", "..oaaepoo...", "..oaaaaaccc.", "..oaaaaaoc..",
    ".ooaaaaao...", "oabaaaaaao..", "oabbaaaaaao.", ".oabbbbbaao.", "..oooooooo..", "...c....c..."] },
  S05: { name: "Clasp the crab", a: "#D0654A", b: "#9E4632", c: "#F6D1A8", rows: [
    ".oo......oo.", "oaao....oaao", "oaao....oaao", ".oao.ee.oao.", "..oaopposo..", ".oaaaaaaaao.",
    "oaaaaaaaaaao", "oaccaaaacca.", ".oaaaaaaaao.", "..obbbbbbo..", ".oo.o..o.oo.", "o..o....o..o"] },
  S06: { name: "Coil the snake", a: "#9A6ED1", b: "#6E4A9E", c: "#E3B341", rows: [
    "....oooo....", "...oaaaao...", "..oaepaepo..", "..oaaaaaao..", "...oacca o..", "....oaao....",
    "..ooaaaaoo..", ".oaabbbbaao.", "oaabooooobao", ".oaaaaaaaao.", "..obbbbbbo..", "...oooooo..."] },
  S07: { name: "Shell the turtle", a: "#4FB09A", b: "#2F7A68", c: "#C9A15A", rows: [
    "............", "....oooo....", "...occcco...", "..occbbcco..", ".occbccbcco.", "oaocccccccoa",
    "oepocccccco.", "oaaooooooooa", ".oa.oa..oa..", ".oo.oo..oo..", "............", "............"] },
  S08: { name: "Howl the wolf", a: "#9AA4B5", b: "#6B7486", c: "#EDE8DA", rows: [
    "o........o..", "oao.....oao.", "oaaooooaaao.", "oaaaaaaaaao.", "oaepaaaepao.", "oaaaaaaaaao.",
    ".oaacocaao..", "..oaccccao..", "..oaaaaaao..", ".oabbbbbbao.", ".oao.oo.oao.", ".oo..oo..oo."] },
  S09: { name: "Thud the bison", a: "#8A5A3B", b: "#5E3C27", c: "#E8DDC8", rows: [
    "c..........c", "co.oooooo.oc", ".ooaaaaaaoo.", "oaabbbbbbaao", "oaepbbbbepao", "oaabbbbbbaao",
    ".oaaccccaao.", "..oaaaaaao..", ".oabbbbbbao.", ".oabbbbbbao.", ".oao.oo.oao.", ".oo..oo..oo."] },
  S10: { name: "Twin the axolotl", a: "#E3B341", b: "#C9D1DE", c: "#E58FA8", rows: [
    "c..........c", ".c.oooooo.c.", "..oaaaabbbo.", ".oaaaaabbbbo", ".oaepaabepbo", ".oaaaaabbbbo",
    "..oaacbcbbo.", "...oaaabbo..", "..oaaaabbbo.", "..oaaoobbo..", "...oo..oo...", "............"] },
};

export function Creature({ bot, size = 96, mood = "idle", label }: {
  bot: string; size?: number; mood?: "idle" | "asleep" | "hurt" | "busy"; label?: string;
}) {
  const s = CREATURES[bot];
  if (!s) return null;
  const color: Record<string, string> = { o: "#14131F", a: s.a, b: s.b, c: s.c, e: "#FFFFFF", p: "#14131F", s: "#14131F" };
  if (mood === "asleep") {
    color.e = s.b;
    color.p = "#14131F";
  }
  const rects = [];
  for (let y = 0; y < 12; y++) {
    const row = (s.rows[y] || "").padEnd(12, ".").slice(0, 12);
    for (let x = 0; x < 12; x++) {
      const ch = row[x];
      if (ch === "." || ch === " ") continue;
      let fill = color[ch] ?? color.a;
      if (mood === "asleep" && ch === "e") fill = s.b;
      rects.push(<rect key={`${x}-${y}`} x={x} y={y} width={1.02} height={1.02} fill={fill} />);
    }
  }
  return (
    <svg className={`creature mood-${mood}`} viewBox="0 0 12 12" width={size} height={size} shapeRendering="crispEdges"
         role="img" aria-label={label ?? s.name}>
      {mood === "hurt" && <rect x={0} y={0} width={12} height={12} fill="#D0654A" opacity={0.18} />}
      <g className="creature-body">{rects}</g>
      {mood === "asleep" && <text x={9.2} y={2.2} fontSize={2.2} fill="#EDE8DA" fontFamily="Silkscreen">z</text>}
    </svg>
  );
}
