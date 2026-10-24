"use client";
import { useMemo } from "react";
import { assessorArt } from "./alienArt";

export type Mood = "idle" | "asleep" | "hurt" | "busy" | "happy";

export const BOTS = ["S01", "S02", "S03", "S04", "S05", "S06", "S07", "S08", "S09", "S10"].flatMap((s) => [`${s}L`, `${s}S`]);

/** One species per strategy: the long half (bull) and short half (bear) are the same creature in different colours. */
const SPECIES: Record<string, string> = {
  S01: "Oculon", S02: "Fanmantis", S03: "Bruiser", S04: "Prismid", S05: "Manyeye",
  S06: "Magmaw", S07: "Coilhorn", S08: "Snorkler", S09: "Ironhorn", S10: "Hollowheart",
};
/** Body colour of each sprite, for chart lines and legends. Longs are blues and greens, shorts reds and oranges. */
const COLOUR: Record<string, string> = {
  S01L: "#9BEA2E", S01S: "#EA7461", S02L: "#40EA6E", S02S: "#EA3732", S03L: "#3371EA", S03S: "#EA8332",
  S04L: "#4EBEEA", S04S: "#EA3140", S05L: "#76DCEA", S05S: "#EAA06F", S06L: "#327EEA", S06S: "#EA7E35",
  S07L: "#62EAB2", S07S: "#EA302E", S08L: "#6449EA", S08S: "#EAA12E", S09L: "#4FEA51", S09S: "#EA5059",
  S10L: "#35EADC", S10S: "#EA5F2E",
};

/** Display name and colour per bot, used everywhere a bot is shown. */
export const CREATURES: Record<string, { name: string; colors: { a: string } }> = {
  ...Object.fromEntries(BOTS.map((b) => [b, { name: `${b.endsWith("L") ? "Bull" : "Bear"} ${SPECIES[b.slice(0, 3)]}`, colors: { a: COLOUR[b] } }])),
  ASSESSOR: { name: "The Assessor", colors: { a: "#E3B341" } },
};

/** Bull (long) or bear (short) badge for a bot. */
export function SideIcon({ bot, size = 44 }: { bot: string; size?: number }) {
  const long = bot.endsWith("L");
  return <img className="side-icon" src={`/creatures/${long ? "bull" : "bear"}.png`} width={size} height={size}
              alt={long ? "Long (bull)" : "Short (bear)"} title={long ? "Long half (bull)" : "Short half (bear)"} />;
}

export function Creature(props: { bot: string; size?: number; mood?: Mood; label?: string; scene?: boolean }) {
  const { bot, size = 96, mood = "idle", label, scene = false } = props;
  if (bot === "ASSESSOR") return <PixelCreature {...props} />;
  if (!COLOUR[bot]) return null;
  const name = CREATURES[bot].name;
  return (
    <span className={`creature sprite mood-${mood} ${scene ? `scene scene-${bot.slice(-1)}` : ""}`} style={{ width: size, height: size }}
          role="img" aria-label={label ?? `${name}${mood === "asleep" ? ", asleep" : ""}`}>
      {mood === "busy" && <span className="glow" />}
      <img className="creature-body" src={`/creatures/${bot}.png`} alt="" draggable={false} />
      {mood === "asleep" && size >= 60 && <span className="zzz">z</span>}
      {mood === "happy" && size >= 60 && <span className="sparkle"><i /><i /><i /><i /></span>}
    </span>
  );
}

function PixelCreature({ bot, size = 96, mood = "idle", label, scene = false }: {
  bot: string; size?: number; mood?: Mood; label?: string; scene?: boolean;
}) {
  const art = useMemo(() => assessorArt(), []);
    const gid = `sky-${bot}`;
  const asleep = mood === "asleep";
  const eyes = art.px.filter((p) => p.layer === "eye");
  const eyeRows = new Set(eyes.map((p) => p.y));
  const midEye = Math.round([...eyeRows].reduce((s, y) => s + y, 0) / Math.max(1, eyeRows.size));
  return (
    <svg className={`creature mood-${mood}`} viewBox="0 0 32 32" width={size} height={size} shapeRendering="crispEdges"
         role="img" aria-label={label ?? `${CREATURES[bot].name}${asleep ? ", asleep" : ""}`}>
      {scene && (
        <g className="scene">
          <defs><linearGradient id={gid} x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor={art.sky[0]} /><stop offset="1" stopColor={art.sky[1]} /></linearGradient></defs>
          <rect width={32} height={32} fill={`url(#${gid})`} />
          {art.stars.map(([x, y], i) => <rect key={i} x={x} y={y} width={1} height={1} fill="#fff" className="star" style={{ animationDelay: `${i * 0.7}s` }} />)}
          <rect x={0} y={29} width={32} height={3} fill={art.ground} />
        </g>
      )}
      {mood === "busy" && <ellipse cx={16} cy={29.5} rx={10} ry={1.2} fill="#E3B341" opacity={0.45} className="glow" />}
      <g className="creature-body">
        {art.px.filter((p) => p.layer !== "eye").map((p, i) => <rect key={i} x={p.x} y={p.y} width={1.02} height={1.02} fill={p.c} />)}
        <g className="creature-eyes">
          {asleep
            ? [...new Set(eyes.map((p) => p.x))].map((x) => <rect key={x} x={x} y={midEye} width={1.02} height={1.02} fill="#14131F" />)
            : eyes.map((p, i) => <rect key={i} x={p.x} y={p.y} width={1.02} height={1.02} fill={p.c} />)}
        </g>
      </g>
      {mood === "hurt" && <rect x={0} y={0} width={32} height={32} fill="#D0654A" opacity={0.18} />}
      {asleep && <text x={24} y={6} fontSize={5} fill="#EDE8DA" fontFamily="Silkscreen" className="zzz">z</text>}
      {mood === "happy" && (
        <g className="sparkle">
          <rect x={4} y={6} width={1} height={1} fill="#F2D06B" /><rect x={27} y={9} width={1} height={1} fill="#F2D06B" />
          <rect x={6} y={14} width={1} height={1} fill="#F2D06B" /><rect x={25} y={3} width={1} height={1} fill="#F2D06B" />
        </g>
      )}
    </svg>
  );
}
