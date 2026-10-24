"use client";
import { useMemo } from "react";
import { alienArt, SPECIES } from "./alienArt";

export type Mood = "idle" | "asleep" | "hurt" | "busy" | "happy";

export const BOTS = ["S01", "S02", "S03", "S04", "S05", "S06", "S07", "S08", "S09", "S10"].flatMap((s) => [`${s}L`, `${s}S`]);

/** Display name and colour per bot (the species), used everywhere a bot is shown. */
export const CREATURES: Record<string, { name: string; colors: { a: string } }> = Object.fromEntries(
  [...BOTS, "ASSESSOR"].map((b) => {
    const art = alienArt(b);
    const body = art.px.filter((p) => p.layer === "body" && p.c !== "#14131F");
    const counts: Record<string, number> = {};
    for (const p of body) counts[p.c] = (counts[p.c] ?? 0) + 1;
    const a = Object.entries(counts).sort((x, y) => y[1] - x[1])[0]?.[0] ?? "#ccc";
    return [b, { name: b === "ASSESSOR" ? "The Assessor" : SPECIES[b] ?? b, colors: { a } }];
  }),
);

export function Creature({ bot, size = 96, mood = "idle", label, scene = false }: {
  bot: string; size?: number; mood?: Mood; label?: string; scene?: boolean;
}) {
  const art = useMemo(() => (CREATURES[bot] ? alienArt(bot) : null), [bot]);
  if (!art) return null;
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
