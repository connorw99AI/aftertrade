"use client";
import { ButtonHTMLAttributes } from "react";

export type Tone = "default" | "primary" | "go" | "pause" | "stop" | "danger" | "ghost";

/** The one button. Tone says what it does: go starts, pause pauses, stop stops, danger closes or kills. */
export function Button({ tone = "default", size, className = "", ...rest }: ButtonHTMLAttributes<HTMLButtonElement> & { tone?: Tone; size?: "sm" }) {
  return <button {...rest} className={`btn ${tone !== "default" ? `btn-${tone}` : ""} ${size === "sm" ? "btn-sm" : ""} ${className}`} />;
}
