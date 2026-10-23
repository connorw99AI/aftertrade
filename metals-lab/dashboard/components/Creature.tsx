"use client";
/* Ten hand-drawn 16x16 pixel creatures, one per strategy, rendered as crisp SVG rects.
   Legend: o outline, a body, b shade, h highlight, c accent, d accent 2, w light, e eye white, p pupil, k cheek, . empty */

type Sprite = { name: string; colors: Record<string, string>; rows: string[] };

export const CREATURES: Record<string, Sprite> = {
  S01: { name: "Dart the fox", colors: {"a": "#E8893A", "b": "#B45A22", "h": "#F7B267", "c": "#2B2238", "d": "#E8893A", "w": "#FFF3E2", "k": "#F29C9C"}, rows: [
    "..o..........o..", ".oho........oho.", ".ohao......oaho.", ".oaaaoooooooaaao",
    "oaaaaaaaaaaaaaao", "oahaaaaaaaaaahao", "oaaepaaaaaaepaao", "oaaeppaaaaaeppao",
    "oakaawwwwwwaakao", ".oaawwwoowwwaao.", "..oawwwwwwwwao..", "...oowwwwwwoo...",
    "..oaaawwwwaaao..", ".oaaaawwwwaaaaoo", ".obaaaaaaaaaabho", "..oboo....oobooo",
  ] },
  S02: { name: "Lune the owl", colors: {"a": "#7D86C9", "b": "#525A99", "h": "#AEB5E8", "c": "#E8B33F", "d": "#C98A2B", "w": "#E9E4F7", "k": "#F29CB8"}, rows: [
    "..oo........oo..", "..oho......oho..", "..ohaoooooooaho.", ".oaaaaaaaaaaaaao",
    ".oaweewaawweewao", ".oweppewaweppewo", ".oweppewaweppewo", ".oaweewaccaweewo",
    ".oaaaaaacdaaaaao", ".oaawwwwwwwwaaao", ".oawwbwwbwwbwaao", ".oaawwbwwbwwaaao",
    ".obaaaaaaaaaaabo", "..obbaaaaaaabbo.", "...oocoo..oocoo.", "....cc......cc..",
  ] },
  S03: { name: "Fib the frog", colors: {"a": "#6CC255", "b": "#3F8F35", "h": "#A6E58A", "c": "#F2D06B", "d": "#2E6B2A", "w": "#E6F7C9", "k": "#F29C9C"}, rows: [
    "................", "..oooo....oooo..", ".oeeppo..oeeppo.", ".oeeppo..oeeppo.",
    ".ohaaaooooaaaho.", "ohaaaaaaaaaaaaho", "oaaaaaaaaaaaaaao", "oakaaaaaaaaaakao",
    "oaoooooooooooaao", ".oawwwwwwwwwwao.", ".oawwwwwwwwwwao.", "..obaaaaaaaabo..",
    ".oaabooooooboaao", "oaaabo....obaaao", "obbbo......obbbo", "oooo........oooo",
  ] },
  S04: { name: "Bell the rooster", colors: {"a": "#F4EBDA", "b": "#C9B79A", "h": "#FFFFFF", "c": "#D9503F", "d": "#E8B33F", "w": "#FFFFFF", "k": "#F29C9C"}, rows: [
    "......cc.c......", ".....ccccc......", ".....occco......", "....oaaaaao.....",
    "....oaepaaoddd..", "....oaaaaaoddd..", "....oaacaao.....", "...ooaacaaoo....",
    "..oaaaaaaaaao.cc", ".ohaaaaaaaaaaocc", "ohhaaaaaaaaaaoc.", "ohaaabbbbbaaao..",
    ".oaabbbbbbbaao..", "..oaaaaaaaaao...", "...ooodoodoo....", ".....ddo.ddo....",
  ] },
  S05: { name: "Clasp the crab", colors: {"a": "#DE5B44", "b": "#9E3826", "h": "#F59A80", "c": "#F6D1A8", "d": "#9E3826", "w": "#FFFFFF", "k": "#F6D1A8"}, rows: [
    "................", ".oo..........oo.", "ohao........oaho", "oaao.oe..eo.oaao",
    ".oao.op..po.oao.", "..oao.o..o.oao..", "..oohaaaaaaaoo..", ".ohaaaaaaaaaaao.",
    "ohaakaaaaaakaaao", "oaaaaaaooaaaaaao", ".obbbbbbbbbbbbo.", ".o.oooooooooo.o.",
    "oao.oao..oao.oao", "oo..oo....oo..oo", "................", "................",
  ] },
  S06: { name: "Coil the snake", colors: {"a": "#9A6ED1", "b": "#6A459E", "h": "#C6A5F0", "c": "#E8B33F", "d": "#D9503F", "w": "#E9DDF8", "k": "#F29CB8"}, rows: [
    ".....oooooo.....", "....ohaaaaao....", "...ohaepaepao...", "...oaaaaaaaao...",
    "...oaakaaakao...", "....oaaddaao....", ".....ooddoo.....", ".......do.......",
    "....oooaaooo....", "..oohaaaaaaaoo..", ".ohaacacacacaao.", "oaaaaaaaaaaaaaao",
    "obaacacacacacabo", ".obbaaaaaaaabbo.", "..oooobbbboooo..", "......oooo......",
  ] },
  S07: { name: "Shell the turtle", colors: {"a": "#55B79F", "b": "#2F7A68", "h": "#8FDCC8", "c": "#C9A15A", "d": "#8C6A34", "w": "#EFE6C8", "k": "#F29C9C"}, rows: [
    "................", "................", ".....oooooo.....", "....occdccco....",
    "...occdccdcco...", "..occcddccccoooo", ".occdcccdccohhao", ".odccccdcccoaepo",
    "ooooooooooooaaao", "owwwwwwwwwwwoao.", ".oaao.....oaao..", ".oaao.....oaao..",
    ".oooo.....oooo..", "................", "................", "................",
  ] },
  S08: { name: "Howl the wolf", colors: {"a": "#9AA6BA", "b": "#646E82", "h": "#CBD3E0", "c": "#2B2238", "d": "#E8B33F", "w": "#F2EEE4", "k": "#F29C9C"}, rows: [
    ".o............o.", "oho..........oho", "ohao........oaho", "oaaaoooooooooaao",
    "oaaaaaaaaaaaaaao", "oahaaaaaaaaaahao", "oaadpaaaaaaadpao", "oaaddaaaaaaddaao",
    ".oaaaawwwwaaaao.", ".oaaawwwwwwaaao.", "..oawwwoowwwao..", "...owwwwwwwwo...",
    "..oaowwwwwwoao..", ".oaaaoooooooaao.", ".obbaaaaaaaabbo.", "..ooo.oo.oo.ooo.",
  ] },
  S09: { name: "Thud the bison", colors: {"a": "#8A5A3B", "b": "#5A3A24", "h": "#B07A52", "c": "#EDE3CF", "d": "#3B2618", "w": "#C9A27A", "k": "#F29C9C"}, rows: [
    "c..............c", "co............oc", ".co.oooooooo.oc.", "..oohhhhhhhhoo..",
    ".ohhhaaaaaahhho.", "ohaaaaaaaaaaaaho", "oaaepaaaaaaepaao", "oaaaaaaaaaaaaaao",
    ".oaawwwwwwwwaao.", ".oawwoowwoowwao.", "..owwwwwwwwwwo..", "..oaawwwwwwaao..",
    ".oaaaaaaaaaaaao.", ".obbaaaaaaaabbo.", ".oddoboo.oobddo.", ".oo..oo...oo..o.",
  ] },
  S10: { name: "Twin the axolotl", colors: {"a": "#F0B93F", "b": "#C78F22", "h": "#FFE08A", "c": "#E87FA0", "d": "#D7DEE8", "w": "#FFF6DC", "k": "#E87FA0"}, rows: [
    "c.c..........c.c", ".cc..........cc.", "cc.oooooooooo.cc", "..ohaaaaaddddo..",
    ".ohaaaaaadddddo.", ".oaaepaaaddepdo.", ".oakaaaaadddkdo.", ".oaaaaaoodddddo.",
    "..oaaaaaaddddo..", "...ooaaaaddoo...", "..ohaaaaaddddo..", ".ohaaaaaadddddo.",
    ".oaaaaaaadddddo.", "..obbaaaaddddo..", "..oao.oo..oo.oo.", "................",
  ] },
};

export type Mood = "idle" | "asleep" | "hurt" | "busy" | "happy";

export function Creature({ bot, size = 96, mood = "idle", label }: { bot: string; size?: number; mood?: Mood; label?: string }) {
  const s = CREATURES[bot];
  if (!s) return null;
  const col: Record<string, string> = { o: "#14131F", e: "#FFFFFF", p: "#14131F", ...s.colors };
  const body: React.ReactNode[] = [];
  const eyes: React.ReactNode[] = [];
  s.rows.forEach((row, y) => {
    for (let x = 0; x < 16; x++) {
      const ch = row[x];
      if (!ch || ch === ".") continue;
      const isEye = ch === "e" || ch === "p";
      let fill = col[ch] ?? col.a;
      if (mood === "asleep" && isEye) fill = col.b;
      const r = <rect key={`${x}-${y}`} x={x} y={y} width={1.02} height={1.02} fill={fill} />;
      (isEye ? eyes : body).push(r);
    }
  });
  return (
    <svg className={`creature mood-${mood}`} viewBox="0 0 16 16" width={size} height={size} shapeRendering="crispEdges"
         role="img" aria-label={label ?? s.name}>
      {mood === "busy" && <ellipse cx={8} cy={15.2} rx={6.5} ry={0.8} fill="#E3B341" opacity={0.35} />}
      <g className="creature-body">
        {body}
        <g className="creature-eyes">{eyes}</g>
      </g>
      {mood === "hurt" && <rect x={0} y={0} width={16} height={16} fill="#D0654A" opacity={0.16} />}
      {mood === "asleep" && <text x={12.4} y={3} fontSize={3} fill="#EDE8DA" fontFamily="Silkscreen" className="zzz">z</text>}
      {mood === "happy" && <text x={12.2} y={3.4} fontSize={3.2} fill="#E3B341" fontFamily="Silkscreen">+</text>}
    </svg>
  );
}
