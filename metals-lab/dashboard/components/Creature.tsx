"use client";

export type Mood = "idle" | "still" | "asleep" | "hurt" | "busy" | "happy";

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
  S07L: "#62EAB2", S07S: "#EA302E", S08L: "#2A44B8", S08S: "#EAA12E", S09L: "#4FEA51", S09S: "#EA5059",
  S10L: "#35EADC", S10S: "#EA5F2E",
};

/** Display name and colour per bot, used everywhere a bot is shown. */
export const CREATURES: Record<string, { name: string; colors: { a: string } }> = {
  ...Object.fromEntries(BOTS.map((b) => [b, { name: `${b.endsWith("L") ? "Bull" : "Bear"} ${SPECIES[b.slice(0, 3)]}`, colors: { a: COLOUR[b] } }])),
  ASSESSOR: { name: "The Assessor", colors: { a: "#3D5BD9" } },
};

/** Bull (long) or bear (short) badge for a bot. */
export function SideIcon({ bot, size = 44 }: { bot: string; size?: number }) {
  const long = bot.endsWith("L");
  return <img className="side-icon" src={`/creatures/${long ? "bull" : "bear"}.png`} width={size} height={size}
              alt={long ? "Long (bull)" : "Short (bear)"} title={long ? "Long half (bull)" : "Short half (bear)"} />;
}

/** Image for a bot or the Assessor. The Assessor uses its headshot everywhere except its own page (`full`). */
function spriteFor(bot: string, full: boolean): string | null {
  if (bot === "ASSESSOR") return full ? "/brand/assessor.png" : "/brand/assessor-head.png";
  return COLOUR[bot] ? `/creatures/${bot}.png` : null;
}

export function Creature(props: { bot: string; size?: number; mood?: Mood; label?: string; scene?: boolean; full?: boolean }) {
  const { bot, size = 96, mood = "idle", label, scene = false, full = false } = props;
  const src = spriteFor(bot, full);
  if (!src) return null;
  const name = CREATURES[bot].name;
  const sceneCls = scene ? `scene scene-${bot === "ASSESSOR" ? "A" : bot.slice(-1)}` : "";
  return (
    <span className={`creature sprite mood-${mood} ${sceneCls} ${bot === "ASSESSOR" && !full ? "headshot" : ""}`}
          style={{ width: size, height: size }} role="img" aria-label={label ?? `${name}${mood === "asleep" ? ", retired" : ""}`}>
      {mood === "busy" && <span className="glow" />}
      <img className="creature-body" src={src} alt="" draggable={false} />
      {mood === "happy" && size >= 60 && <span className="sparkle"><i /><i /><i /><i /></span>}
    </span>
  );
}

/* 8x8 pixel symbols for a bot's state: running, paused, stopped, kill switch. */
const SKULL = [".XXXXXX.", "XXXXXXXX", "X..XX..X", "X..XX..X", "XXX..XXX", ".XXXXXX.", ".X.XX.X.", "..XXXX.."];
const PIXELS: Record<string, string[]> = {
  run: ["..X.....", "..XX....", "..XXX...", "..XXXX..", "..XXXX..", "..XXX...", "..XX....", "..X....."],
  pause: ["........", ".XX..XX.", ".XX..XX.", ".XX..XX.", ".XX..XX.", ".XX..XX.", ".XX..XX.", "........"],
  stop: ["........", "........", "..XXXX..", "..XXXX..", "..XXXX..", "..XXXX..", "........", "........"],
  kill: SKULL,
};
const SYMBOL_TEXT: Record<string, string> = { run: "Running", pause: "Paused", stop: "Stopped", kill: "Kill switch on" };

/** Which symbol a bot shows. The kill switch overrides everything. */
export function stateSymbol(status: string, kill: boolean): "run" | "pause" | "stop" | "kill" {
  if (kill) return "kill";
  if (status === "RUNNING") return "run";
  if (status === "PAUSED" || status === "PAUSED_RISK") return "pause";
  return "stop";
}

export function StateSymbol({ state, size = 30 }: { state: "run" | "pause" | "stop" | "kill"; size?: number }) {
  return (
    <span className={`state-symbol st-${state}`} title={SYMBOL_TEXT[state]} role="img" aria-label={SYMBOL_TEXT[state]}
          style={{ width: size, height: size }}>
      <svg viewBox="0 0 8 8" width={size * 0.56} height={size * 0.56} shapeRendering="crispEdges">
        {PIXELS[state].flatMap((row, y) => [...row].map((c, x) => (c === "X" ? <rect key={`${x}-${y}`} x={x} y={y} width={1} height={1} /> : null)))}
      </svg>
    </span>
  );
}
