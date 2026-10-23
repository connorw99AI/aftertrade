"""Independent market/news auditor.

The auditor receives a read-only market view and a proposal. It holds no
trading gateway and therefore cannot submit orders. Its deterministic rules are
versioned; any AI/LLM classification would only be attached as evidence and
cannot override them (none is used in this version).

Openings (OPEN_LONG / OPEN_SHORT) receive the full entry checks. Risk-reducing
closes/covers are only blocked when the market is closed (the order could not
execute) - everything else is attached as evidence so exits stay possible.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional, Protocol

import numpy as np
import pandas as pd

from .. import indicators as ind
from ..domain import Decision
from ..instruments import InstrumentSpec
from .news import NewsProvider, NewsResult

_ORDER = {Decision.APPROVED: 0, Decision.DELAYED: 1, Decision.MANUAL_REVIEW_REQUIRED: 2, Decision.REJECTED: 3}


class MarketView(Protocol):
    def now(self) -> datetime: ...
    def tick(self, role: str) -> Optional[dict]: ...           # {"bid","ask","time_utc"}
    def bars(self, role: str, tf: str, n: int) -> pd.DataFrame: ...
    def spec(self, role: str) -> InstrumentSpec: ...
    def session_open(self, role: str, t: datetime) -> Optional[bool]: ...
    def spread_baseline(self, role: str, hour_utc: int) -> Optional[float]: ...
    def reference_bars(self) -> dict[str, pd.DataFrame]: ...


@dataclass
class AuditResult:
    decision: Decision
    triggered: list[str]
    reasons: list[str]
    evidence: dict
    data_quality: str
    news_status: str
    ruleset_version: str
    reassess_at: Optional[datetime]
    proposal_hash: str
    approval: object = None
    flags: list[str] = field(default_factory=list)

    def public(self) -> dict:
        return {"decision": self.decision.value, "triggered": self.triggered, "reasons": self.reasons,
                "evidence": self.evidence, "data_quality": self.data_quality, "news_status": self.news_status,
                "ruleset_version": self.ruleset_version, "flags": self.flags,
                "reassess_at": self.reassess_at.isoformat() if self.reassess_at else None, "proposal_hash": self.proposal_hash}


class MarketAuditor:
    def __init__(self, cfg: dict, news: NewsProvider, authority=None, operator_alerts=None):
        self.cfg = cfg["auditor"]
        self.ncfg = cfg["news"]
        self.rcfg = cfg.get("risk", {})
        self.news = news
        self.authority = authority                # ApprovalAuthority("AUDIT") or None (research)
        self.operator_alerts = operator_alerts or (lambda: [])
        self.version = self.cfg["ruleset_version"]

    def assess(self, view: MarketView, role: str, intent_is_opening: bool, proposal_hash: str = "",
               fingerprint: str = "", point_in_time_news: bool = False, primary_tf: str = "M5", proposal=None) -> AuditResult:
        now = view.now()
        dec = Decision.APPROVED
        trig: list[str] = []
        reasons: list[str] = []
        flags: list[str] = []
        ev: dict = {"role": role, "assessed_at": now.isoformat()}
        reassess: Optional[datetime] = None
        quality = "OK"

        def hit(rule: str, d: Decision, why: str, wait_s: Optional[int] = None):
            nonlocal dec, reassess
            trig.append(rule)
            reasons.append(why)
            if _ORDER[d] > _ORDER[dec]:
                dec = d
            if wait_s is not None:
                t = now + timedelta(seconds=wait_s)
                reassess = max(reassess, t) if reassess else t

        spec = view.spec(role)
        # --- session --------------------------------------------------------------
        so = view.session_open(role, now)
        ev["session_open"] = so
        if so is False:
            hit("R01_SESSION_CLOSED", Decision.DELAYED, "market session closed per configured/exported schedule", 900)
        elif so is None:
            flags.append("session schedule unknown (no MQL5 export/config)")
        # --- quote freshness -----------------------------------------------------
        tk = view.tick(role)
        if tk is None:
            quality = "BAD"
            hit("R02_NO_QUOTE", Decision.DELAYED if not intent_is_opening else Decision.REJECTED, "no current quote", 60)
        else:
            age = (now - tk["time_utc"]).total_seconds()
            ev["quote_age_s"] = round(age, 1)
            ev["bid"], ev["ask"] = tk["bid"], tk["ask"]
            spr_pts = (tk["ask"] - tk["bid"]) / spec.point if spec.point else None
            ev["spread_points"] = round(spr_pts, 1) if spr_pts is not None else None
            if age > self.cfg["max_quote_age_seconds"]:
                quality = "DEGRADED"
                if intent_is_opening:
                    hit("R03_STALE_QUOTE", Decision.DELAYED, f"quote is {age:.0f}s old", 60)
                else:
                    flags.append("stale quote on exit")
            base = view.spread_baseline(role, now.hour)
            ev["spread_baseline_points"] = base
            if spr_pts is not None and base:
                ratio = spr_pts / base
                ev["spread_ratio"] = round(ratio, 2)
                if ratio > self.cfg["spread_baseline_mult_delay"] and intent_is_opening:
                    hit("R04_SPREAD_EXPANSION", Decision.DELAYED, f"spread {ratio:.1f}x its hourly baseline", self.cfg["delay_seconds"])
            elif base is None:
                flags.append("no spread baseline")
        # --- price behaviour (instrument-specific baselines) ------------------------
        bars = view.bars(role, primary_tf, 300)
        if bars is None or len(bars) < 50:
            quality = "BAD" if quality == "OK" else quality
            if intent_is_opening:
                hit("R05_INSUFFICIENT_DATA", Decision.DELAYED, "insufficient recent bars to assess market state", 120)
        else:
            self._price_rules(bars, ev, hit, intent_is_opening, prefix="")
        # --- correlated metals shock ------------------------------------------------
        other = "SILVER" if role == "GOLD" else "GOLD"
        try:
            ob = view.bars(other, primary_tf, 300)
        except KeyError:
            ob = None
        if ob is not None and bars is not None and len(ob) > 50 and len(bars) > 50 and intent_is_opening:
            m1, m2 = self._last_move_atr(bars), self._last_move_atr(ob)
            ev["move_atr_self"], ev["move_atr_other"] = round(m1, 2), round(m2, 2)
            k = self.cfg["correlated_shock_atr"]
            if abs(m1) > k and abs(m2) > k and np.sign(m1) == np.sign(m2):
                hit("R10_CORRELATED_SHOCK", Decision.DELAYED, "gold and silver moved abnormally together (macro/dollar shock)",
                    self.cfg["delay_seconds"])
        # --- dollar/rate reference instruments (read-only, optional) ---------------
        for name, rb in (view.reference_bars() or {}).items():
            if rb is not None and len(rb) > 50 and intent_is_opening:
                m = self._last_move_atr(rb)
                ev[f"ref_{name}_move_atr"] = round(m, 2)
                if abs(m) > self.cfg["reference_move_atr"]:
                    hit("R11_REFERENCE_SHOCK", Decision.DELAYED, f"abnormal move in reference {name}", self.cfg["delay_seconds"])
        # --- news ---------------------------------------------------------------------
        news_status = "NOT_CHECKED"
        if not self.ncfg.get("enabled", True):
            news_status = "OFF"
        elif intent_is_opening and self.ncfg.get("filter_enabled", True):
            pre, post = timedelta(minutes=self.ncfg["pre_minutes"]), timedelta(minutes=self.ncfg["post_minutes"])
            nr: NewsResult = self.news.events(now - post, now + pre + timedelta(hours=6), now, point_in_time_news)
            news_status = nr.status
            ev["news_source"], ev["news_reason"] = nr.source, nr.reason
            if nr.status != "OK":
                pol = self.ncfg.get("unknown_policy", "manual_review")
                why = f"news status UNKNOWN ({nr.reason}) - absence of data is not absence of news"
                if pol == "manual_review":
                    hit("R12_NEWS_UNKNOWN", Decision.MANUAL_REVIEW_REQUIRED, why)
                elif pol == "delay":
                    hit("R12_NEWS_UNKNOWN", Decision.DELAYED, why, self.cfg["delay_seconds"])
                else:
                    flags.append("NEWS_UNKNOWN_ALLOWED_BY_POLICY")
            else:
                imp = set(self.ncfg.get("impacts", ["HIGH"]))
                near = [e for e in nr.events if e.impact in imp and e.time - pre <= now <= e.time + post]
                upcoming = [e for e in nr.events if e.impact in imp and now < e.time - pre]
                ev["news_events_in_window"] = [e.public() for e in near]
                ev["next_high_impact"] = upcoming[0].public() if upcoming else None
                if near:
                    until = max(e.time + post for e in near)
                    hit("R13_NEWS_BLACKOUT", Decision.DELAYED, "inside high-impact release window: " + ", ".join(e.title for e in near),
                        int((until - now).total_seconds()) + 1)
        # --- operator market alerts (geopolitics etc.) ---------------------------
        for a in self.operator_alerts():
            if intent_is_opening:
                hit("R14_OPERATOR_ALERT", Decision.MANUAL_REVIEW_REQUIRED, f"operator market alert: {a.get('message')}")
        # --- instrument permissions ------------------------------------------------
        if intent_is_opening and not spec.can_open_long and not spec.can_open_short:
            hit("R15_TRADING_RESTRICTED", Decision.REJECTED, "symbol trade mode does not allow new positions")
        # --- trade setup: is the order itself built correctly? ---------------------
        if proposal is not None and intent_is_opening:
            self._setup_rules(proposal, spec, tk, bars, ev, hit)
        res = AuditResult(dec, trig, reasons, ev, quality, news_status, self.version, reassess if dec == Decision.DELAYED else None,
                          proposal_hash, flags=flags)
        if dec == Decision.APPROVED and self.authority is not None:
            res.approval = self.authority.issue(proposal_hash, "APPROVED", fingerprint)
        return res

    # ------------------------------------------------------------------ helpers
    def _setup_rules(self, p, spec, tk, bars, ev, hit):
        """Checks the order itself: lot size, stop and target present, on the right side and at sane distances."""
        long = p.intent.order_side == "BUY"
        setup = {"side": "LONG" if long else "SHORT", "volume": p.volume, "sl": p.sl, "tp": p.tp}
        ev["setup"] = setup
        rej = Decision.REJECTED
        if p.volume <= 0 or spec.round_volume_down(p.volume) != p.volume:
            hit("R17_VOLUME_INVALID", rej, f"volume {p.volume} is not a valid lot for {spec.symbol} "
                f"(min {spec.volume_min}, step {spec.volume_step}, max {spec.volume_max})")
        ml = self.rcfg.get("min_lot", 0) or 0
        floor = float(ml.get(p.role, 0) if isinstance(ml, dict) else ml)
        setup["min_lot"] = floor
        if floor and p.volume + 1e-9 < floor:
            hit("R18_BELOW_MIN_LOT", rej, f"volume {p.volume} is below the configured minimum {floor}")
        if p.sl is None:
            hit("R19_NO_STOP", rej, "opening order has no stop-loss")
        if tk is None:
            return
        entry = tk["ask"] if long else tk["bid"]
        setup["entry_ref"] = entry
        min_d = max(spec.stops_level_points, spec.freeze_level_points) * spec.point
        if p.sl is not None:
            dist = (entry - p.sl) if long else (p.sl - entry)
            setup["stop_distance"] = round(dist, 6)
            atr = float(ind.atr(bars, 14).iloc[-1]) if bars is not None and len(bars) > 15 else float("nan")
            if np.isfinite(atr) and atr > 0:
                setup["stop_atr"] = round(dist / atr, 2)
            if dist <= 0:
                hit("R20_STOP_WRONG_SIDE", rej, f"stop {p.sl} is on the wrong side of entry {entry}")
            elif dist < min_d:
                hit("R21_STOP_INSIDE_BROKER_LEVEL", rej, f"stop {dist:.5g} from entry is inside the broker minimum {min_d:.5g}")
            elif np.isfinite(atr) and atr > 0 and dist > self.cfg.get("max_stop_atr", 15.0) * atr:
                hit("R22_STOP_ABNORMAL", rej, f"stop is {dist / atr:.1f} ATR away (limit {self.cfg.get('max_stop_atr', 15.0)})")
        if p.tp is not None:
            tdist = (p.tp - entry) if long else (entry - p.tp)
            setup["target_distance"] = round(tdist, 6)
            if tdist <= 0:
                hit("R23_TARGET_WRONG_SIDE", rej, f"take-profit {p.tp} is on the wrong side of entry {entry}")
            elif tdist < min_d:
                hit("R24_TARGET_INSIDE_BROKER_LEVEL", rej, f"take-profit {tdist:.5g} from entry is inside the broker minimum {min_d:.5g}")

    @staticmethod
    def _last_move_atr(b: pd.DataFrame) -> float:
        a = ind.atr(b, 14).iloc[-2] if len(b) > 16 else np.nan
        mv = b["close"].iloc[-1] - b["open"].iloc[-1]
        return float(mv / a) if a and np.isfinite(a) and a > 0 else 0.0

    def _price_rules(self, b: pd.DataFrame, ev: dict, hit, opening: bool, prefix: str):
        if not opening:
            return
        atr = ind.atr(b, 14)
        a = float(atr.iloc[-2]) if np.isfinite(atr.iloc[-2]) else None
        if not a:
            return
        last = b.iloc[-1]
        prev = b.iloc[-2]
        move = abs(last["close"] - last["open"]) / a
        gap = abs(last["open"] - prev["close"]) / a
        ev["last_bar_move_atr"], ev["last_gap_atr"] = round(move, 2), round(gap, 2)
        if move > self.cfg["atr_move_mult"]:
            hit("R06_ATR_MOVE", Decision.DELAYED, f"last bar moved {move:.1f} ATR", self.cfg["delay_seconds"])
        if gap > self.cfg["gap_atr_mult"]:
            hit("R07_GAP", Decision.DELAYED, f"price gap of {gap:.1f} ATR", self.cfg["delay_seconds"])
        r = np.log(b["close"]).diff()
        rv_s, rv_l = r.iloc[-12:].std(), r.iloc[-288:].std()
        if rv_l and np.isfinite(rv_l) and rv_l > 0:
            ratio = float(rv_s / rv_l)
            ev["rv_ratio_12_vs_288"] = round(ratio, 2)
            if ratio > self.cfg["rv_spike_ratio"]:
                hit("R08_RV_SPIKE", Decision.DELAYED, f"realized volatility {ratio:.1f}x baseline", self.cfg["delay_seconds"])
        tv = b["tick_volume"].astype(float)
        if tv.iloc[:-1].std() > 0:
            z = float((tv.iloc[-1] - tv.iloc[:-1].mean()) / tv.iloc[:-1].std())
            ev["tick_activity_z"] = round(z, 2)
            ev["tick_activity_note"] = "tick activity = count of quote updates, not traded volume"
            if z > self.cfg["tick_activity_z"] and move > 1.5:
                hit("R09_TICK_ACTIVITY", Decision.DELAYED, f"abnormal tick activity (z={z:.1f}) with a large move", self.cfg["delay_seconds"])
        w = b.iloc[-4:]
        if len(w) == 4:
            up = (w["high"].max() - w["open"].iloc[0]) / a
            dn = (w["high"].max() - w["close"].iloc[-1]) / a
            up2 = (w["close"].iloc[-1] - w["low"].min()) / a
            dn2 = (w["open"].iloc[0] - w["low"].min()) / a
            if (up > self.cfg["reversal_atr"] and dn > 0.75 * self.cfg["reversal_atr"]) or \
               (dn2 > self.cfg["reversal_atr"] and up2 > 0.75 * self.cfg["reversal_atr"]):
                hit("R16_RAPID_REVERSAL", Decision.DELAYED, "rapid spike-and-reverse in last 4 bars", self.cfg["delay_seconds"])


class FrameMarketView:
    """MarketView over historical frames (backtests) or over freshly fetched live bars."""

    def __init__(self, frames: dict, specs: dict[str, InstrumentSpec], now: datetime, ticks: Optional[dict] = None,
                 sessions: Optional[dict] = None, clock=None, spread_baselines: Optional[dict] = None, reference: Optional[dict] = None):
        self._frames, self._specs, self._now = frames, specs, now
        self._ticks = ticks
        self._sessions = sessions or {}
        self._clock = clock
        self._sb = spread_baselines or {}
        self._ref = reference or {}

    def now(self):
        return self._now

    def spec(self, role):
        return self._specs[role]

    def tick(self, role):
        if self._ticks is not None:
            return self._ticks.get(role)
        b = self.bars(role, next(tf for (r, tf) in self._frames if r == role), 1)
        if b is None or not len(b):
            return None
        row = b.iloc[-1]
        sp = self._specs[role]
        return {"bid": float(row["close"]), "ask": float(row["close"] + row["spread"] * sp.point), "time_utc": self._now}

    def bars(self, role, tf, n):
        df = self._frames.get((role, tf))
        if df is None:
            raise KeyError((role, tf))
        return df.iloc[-n:]

    def session_open(self, role, t):
        cal = self._sessions.get(role)
        if cal is None or self._clock is None:
            return None
        return cal.is_open_server(self._clock.utc_to_server_naive(t))

    def spread_baseline(self, role, hour):
        b = self._sb.get(role)
        return None if b is None else b.get(hour)

    def reference_bars(self):
        return self._ref


def spread_baseline_from_bars(df: pd.DataFrame) -> dict[int, float]:
    d = df[df["spread"] > 0]
    if d.empty:
        return {}
    return {int(h): float(v) for h, v in d.groupby(d["time"].dt.hour)["spread"].median().items()}


def make_backtest_audit_fn(auditor: MarketAuditor, frames_prepared: dict, specs: dict, primary_tf: str):
    """Build an AuditFn for the backtester using point-in-time news and bars up to T."""
    close_idx = {k: (df["time"] + pd.Timedelta(minutes={"M1": 1, "M5": 5, "M15": 15, "H1": 60, "H4": 240, "D1": 1440}[k[1]]))
                 for k, df in frames_prepared.items()}
    baselines = {r: spread_baseline_from_bars(df) for (r, tf), df in frames_prepared.items() if tf == primary_tf}

    def fn(sig, T):
        trunc = {k: df[close_idx[k] <= T] for k, df in frames_prepared.items()}
        view = FrameMarketView(trunc, specs, T.to_pydatetime() if hasattr(T, "to_pydatetime") else T, spread_baselines=baselines)
        r = auditor.assess(view, sig.role, sig.intent.is_opening, point_in_time_news=True, primary_tf=primary_tf)
        return r.decision.value, r.triggered

    return fn
