//+------------------------------------------------------------------+
//| MetalLabRiskGuard.mqh                                            |
//| Account-level risk guard for unattended MT5 expert advisors.     |
//|                                                                  |
//| Install: copy to <MT5 data folder>/MQL5/Include/ and             |
//|          #include <MetalLabRiskGuard.mqh>                        |
//|                                                                  |
//| STATUS: written without access to MetaEditor. It has NOT been    |
//| compiled or run. Compile it, fix any errors, and test it on a    |
//| DEMO account (see references/guard-integration.md) before any    |
//| live use.                                                        |
//|                                                                  |
//| Design rule: every entry goes through MLG_Open() (or at least    |
//| MLG_AllowEntry()), and MLG_Check() runs on every tick and timer. |
//| Limits are account-wide, so they hold across all bots that use   |
//| this file on the same account.                                   |
//+------------------------------------------------------------------+
#property copyright "Metal Lab"
#property version   "1.00"

#include <Trade\Trade.mqh>

#define MLG_FOREVER 4102444800.0   // 2100-01-01: "until a human resets it"

//--- halt reason codes stored in a terminal global variable
#define MLG_CODE_DAILY    1
#define MLG_CODE_WEEKLY   2
#define MLG_CODE_DRAWDOWN 3

struct MLGuardConfig
  {
   double   risk_per_trade_pct;        // % of equity risked at the stop, per trade
   double   max_daily_loss_pct;        // vs equity at start of server day (includes floating P/L)
   double   max_weekly_loss_pct;       // vs equity at start of server week (Monday)
   double   max_drawdown_pct;          // vs peak equity; halts until a human resets it
   int      max_open_positions;        // account-wide
   double   max_lots_per_trade;
   double   max_total_lots;            // account-wide, sum of open volume
   int      max_entries_per_day;       // entries opened since server-day start
   int      max_consecutive_losses;    // 0 = off
   int      cooldown_minutes_after_loss;
   double   max_spread_points;         // 0 = off
   double   min_margin_level_pct;      // margin level AFTER the new trade; 0 = off
   int      session_start_hour;        // SERVER time, -1 = off
   int      session_end_hour;          // SERVER time, -1 = off
   int      friday_cutoff_hour;        // no new entries on Friday from this SERVER hour, -1 = off
   bool     close_on_breach;           // flatten everything when a loss limit trips
   bool     push_notify;               // SendNotification on halts (needs MetaQuotes ID set up)
  };

MLGuardConfig g_mlg_cfg;
string   g_mlg_log          = "MetalLab_guard_log.csv";
bool     g_mlg_ready        = false;
datetime g_mlg_day          = 0;
double   g_mlg_day_equity   = 0.0;
datetime g_mlg_week         = 0;
double   g_mlg_week_equity  = 0.0;
double   g_mlg_peak         = 0.0;

//+------------------------------------------------------------------+
string MLG_Key(const string name)
  {
   return "MLG_" + (string)AccountInfoInteger(ACCOUNT_LOGIN) + "_" + name;
  }

datetime MLG_DayStart(const datetime t)
  {
   return (datetime)(((long)t / 86400) * 86400);
  }

datetime MLG_WeekStart(const datetime t)
  {
   MqlDateTime dt;
   TimeToStruct(t, dt);
   int since_monday = (dt.day_of_week + 6) % 7;   // day_of_week: 0 = Sunday
   return (datetime)((long)MLG_DayStart(t) - (long)since_monday * 86400);
  }

//+------------------------------------------------------------------+
//| Append one line to the guard log (Common/Files). review.py reads |
//| this file.                                                       |
//+------------------------------------------------------------------+
void MLG_Log(const string ev, const string sym, string detail)
  {
   StringReplace(detail, ",", ";");
   int h = FileOpen(g_mlg_log, FILE_READ | FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_COMMON |
                    FILE_SHARE_READ | FILE_SHARE_WRITE);
   if(h == INVALID_HANDLE)
      return;
   if(FileSize(h) == 0)
      FileWriteString(h, "time,event,symbol,detail\r\n");
   FileSeek(h, 0, SEEK_END);
   FileWriteString(h, TimeToString(TimeCurrent(), TIME_DATE | TIME_SECONDS) + "," + ev + "," +
                   sym + "," + detail + "\r\n");
   FileClose(h);
  }

//+------------------------------------------------------------------+
void MLG_Defaults(MLGuardConfig &c)
  {
   c.risk_per_trade_pct          = 0.5;
   c.max_daily_loss_pct          = 2.0;
   c.max_weekly_loss_pct         = 4.0;
   c.max_drawdown_pct            = 8.0;
   c.max_open_positions          = 3;
   c.max_lots_per_trade          = 0.5;
   c.max_total_lots              = 1.0;
   c.max_entries_per_day         = 6;
   c.max_consecutive_losses      = 4;
   c.cooldown_minutes_after_loss = 30;
   c.max_spread_points           = 40;
   c.min_margin_level_pct        = 400;
   c.session_start_hour          = -1;
   c.session_end_hour            = -1;
   c.friday_cutoff_hour          = -1;
   c.close_on_breach             = true;
   c.push_notify                 = true;
  }

//+------------------------------------------------------------------+
bool MLG_KillOn()
  {
   return GlobalVariableCheck(MLG_Key("KILL")) && GlobalVariableGet(MLG_Key("KILL")) > 0.0;
  }

bool MLG_IsHalted(string &why)
  {
   why = "";
   if(MLG_KillOn())
     {
      why = "kill switch is on (global variable KILL)";
      return true;
     }
   if(!GlobalVariableCheck(MLG_Key("HALT_UNTIL")))
      return false;
   double until = GlobalVariableGet(MLG_Key("HALT_UNTIL"));
   if(until <= (double)TimeCurrent())
      return false;
   int code = (int)GlobalVariableGet(MLG_Key("HALT_CODE"));
   switch(code)
     {
      case MLG_CODE_DAILY:    why = "daily loss limit hit (resumes next server day)";            break;
      case MLG_CODE_WEEKLY:   why = "weekly loss limit hit (resumes next server week)";          break;
      case MLG_CODE_DRAWDOWN: why = "max drawdown hit (needs a manual reset, see the docs)";     break;
      default:                why = "halted";                                                    break;
     }
   return true;
  }

//+------------------------------------------------------------------+
//| Close every open position and delete pending orders on the       |
//| account, whatever bot opened them.                               |
//+------------------------------------------------------------------+
void MLG_CloseAll(const string why)
  {
   CTrade t;
   t.SetDeviationInPoints(300);   // closing matters more than price; widen if your broker needs it
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong tk = PositionGetTicket(i);
      if(tk == 0)
         continue;
      string sym = PositionGetString(POSITION_SYMBOL);
      if(t.PositionClose(tk))
         MLG_Log("CLOSE", sym, "ticket=" + (string)tk + " reason=" + why);
      else
         MLG_Log("CLOSE_FAILED", sym, "ticket=" + (string)tk + " retcode=" + (string)t.ResultRetcode());
     }
   for(int i = OrdersTotal() - 1; i >= 0; i--)
     {
      ulong tk = OrderGetTicket(i);
      if(tk == 0)
         continue;
      t.OrderDelete(tk);
     }
  }

//+------------------------------------------------------------------+
void MLG_Trip(const int code, const double until, const string text)
  {
   GlobalVariableSet(MLG_Key("HALT_UNTIL"), until);
   GlobalVariableSet(MLG_Key("HALT_CODE"), (double)code);
   MLG_Log("HALT", "", text);
   Print("MetalLabRiskGuard HALT: ", text);
   if(g_mlg_cfg.push_notify)
      SendNotification("Metal Lab HALT: " + text);
   if(g_mlg_cfg.close_on_breach)
      MLG_CloseAll(text);
  }

//+------------------------------------------------------------------+
//| Call once from OnInit. reset_peak = true only for a deliberate   |
//| restart after a drawdown halt (and after clearing HALT_UNTIL).   |
//+------------------------------------------------------------------+
bool MLG_Init(const MLGuardConfig &cfg, const string log_file = "MetalLab_guard_log.csv",
              const bool reset_peak = false)
  {
   g_mlg_cfg = cfg;
   g_mlg_log = log_file;
   double eq = AccountInfoDouble(ACCOUNT_EQUITY);
   if(eq <= 0.0)
      return false;

   datetime now = TimeCurrent();
   g_mlg_day  = MLG_DayStart(now);
   g_mlg_week = MLG_WeekStart(now);

   // Anchors survive EA/terminal restarts so a restart cannot reset the day's loss.
   if(GlobalVariableCheck(MLG_Key("DAY")) && (datetime)GlobalVariableGet(MLG_Key("DAY")) == g_mlg_day)
      g_mlg_day_equity = GlobalVariableGet(MLG_Key("DAY_EQ"));
   else
     {
      g_mlg_day_equity = eq;
      GlobalVariableSet(MLG_Key("DAY"), (double)g_mlg_day);
      GlobalVariableSet(MLG_Key("DAY_EQ"), eq);
     }

   if(GlobalVariableCheck(MLG_Key("WEEK")) && (datetime)GlobalVariableGet(MLG_Key("WEEK")) == g_mlg_week)
      g_mlg_week_equity = GlobalVariableGet(MLG_Key("WEEK_EQ"));
   else
     {
      g_mlg_week_equity = eq;
      GlobalVariableSet(MLG_Key("WEEK"), (double)g_mlg_week);
      GlobalVariableSet(MLG_Key("WEEK_EQ"), eq);
     }

   if(reset_peak || !GlobalVariableCheck(MLG_Key("PEAK")))
     {
      g_mlg_peak = eq;
      GlobalVariableSet(MLG_Key("PEAK"), eq);
     }
   else
      g_mlg_peak = GlobalVariableGet(MLG_Key("PEAK"));

   g_mlg_ready = true;
   MLG_Log("INIT", "", StringFormat("equity=%.2f day_eq=%.2f week_eq=%.2f peak=%.2f reset_peak=%d",
                                    eq, g_mlg_day_equity, g_mlg_week_equity, g_mlg_peak, (int)reset_peak));
   return true;
  }

//+------------------------------------------------------------------+
//| Roll day/week anchors and track the equity peak.                 |
//| NOTE: deposits and withdrawals distort all three baselines; use  |
//| reset_peak at the next EA restart after moving money.            |
//+------------------------------------------------------------------+
void MLG_Roll()
  {
   datetime now = TimeCurrent();
   double eq = AccountInfoDouble(ACCOUNT_EQUITY);

   datetime d = MLG_DayStart(now);
   if(d != g_mlg_day)
     {
      g_mlg_day = d;
      g_mlg_day_equity = eq;
      GlobalVariableSet(MLG_Key("DAY"), (double)d);
      GlobalVariableSet(MLG_Key("DAY_EQ"), eq);
      MLG_Log("NEW_DAY", "", "equity=" + DoubleToString(eq, 2));
     }
   datetime w = MLG_WeekStart(now);
   if(w != g_mlg_week)
     {
      g_mlg_week = w;
      g_mlg_week_equity = eq;
      GlobalVariableSet(MLG_Key("WEEK"), (double)w);
      GlobalVariableSet(MLG_Key("WEEK_EQ"), eq);
      MLG_Log("NEW_WEEK", "", "equity=" + DoubleToString(eq, 2));
     }
   if(eq > g_mlg_peak)
     {
      g_mlg_peak = eq;
      GlobalVariableSet(MLG_Key("PEAK"), eq);
     }
  }

double MLG_DailyLossPct()
  {
   if(g_mlg_day_equity <= 0.0) return 0.0;
   return (g_mlg_day_equity - AccountInfoDouble(ACCOUNT_EQUITY)) / g_mlg_day_equity * 100.0;
  }

double MLG_WeeklyLossPct()
  {
   if(g_mlg_week_equity <= 0.0) return 0.0;
   return (g_mlg_week_equity - AccountInfoDouble(ACCOUNT_EQUITY)) / g_mlg_week_equity * 100.0;
  }

double MLG_DrawdownPct()
  {
   if(g_mlg_peak <= 0.0) return 0.0;
   return (g_mlg_peak - AccountInfoDouble(ACCOUNT_EQUITY)) / g_mlg_peak * 100.0;
  }

//+------------------------------------------------------------------+
//| Call on every tick AND from OnTimer (metals can go quiet while   |
//| a position bleeds). Returns true when trading is allowed.        |
//+------------------------------------------------------------------+
bool MLG_Check()
  {
   if(!g_mlg_ready)
      return false;
   MLG_Roll();

   // Manual flatten switch: set global variable FLATTEN to 1 (F3 in the terminal).
   if(GlobalVariableCheck(MLG_Key("FLATTEN")) && GlobalVariableGet(MLG_Key("FLATTEN")) > 0.0)
     {
      MLG_CloseAll("FLATTEN switch");
      GlobalVariableSet(MLG_Key("FLATTEN"), 0.0);
     }

   string why;
   if(MLG_IsHalted(why))
     {
      // While halted by a loss limit, flatten stragglers (including positions from bots that do not
      // use this guard). The kill switch only blocks entries; it does not close anything.
      if(g_mlg_cfg.close_on_breach && !MLG_KillOn() && PositionsTotal() > 0)
         MLG_CloseAll("halted: " + why);
      return false;
     }

   if(g_mlg_cfg.max_drawdown_pct > 0.0 && MLG_DrawdownPct() >= g_mlg_cfg.max_drawdown_pct)
     {
      MLG_Trip(MLG_CODE_DRAWDOWN, MLG_FOREVER,
               StringFormat("drawdown %.2f%% >= limit %.2f%%", MLG_DrawdownPct(), g_mlg_cfg.max_drawdown_pct));
      return false;
     }
   if(g_mlg_cfg.max_weekly_loss_pct > 0.0 && MLG_WeeklyLossPct() >= g_mlg_cfg.max_weekly_loss_pct)
     {
      MLG_Trip(MLG_CODE_WEEKLY, (double)((long)g_mlg_week + 7 * 86400),
               StringFormat("weekly loss %.2f%% >= limit %.2f%%", MLG_WeeklyLossPct(), g_mlg_cfg.max_weekly_loss_pct));
      return false;
     }
   if(g_mlg_cfg.max_daily_loss_pct > 0.0 && MLG_DailyLossPct() >= g_mlg_cfg.max_daily_loss_pct)
     {
      MLG_Trip(MLG_CODE_DAILY, (double)((long)g_mlg_day + 86400),
               StringFormat("daily loss %.2f%% >= limit %.2f%%", MLG_DailyLossPct(), g_mlg_cfg.max_daily_loss_pct));
      return false;
     }
   return true;
  }

//+------------------------------------------------------------------+
void MLG_OpenStats(int &count, double &lots)
  {
   count = 0;
   lots  = 0.0;
   for(int i = 0; i < PositionsTotal(); i++)
     {
      ulong tk = PositionGetTicket(i);
      if(tk == 0)
         continue;
      count++;
      lots += PositionGetDouble(POSITION_VOLUME);
     }
  }

//+------------------------------------------------------------------+
//| Entries today, current run of consecutive losing closes, and the |
//| time of the most recent loss. Net = profit + swap + commission + |
//| fee on the closing deal. Some brokers book commission on the     |
//| entry deal, so a tiny win can look like a loss here; the         |
//| position-level numbers in review.py are the accurate ones.       |
//+------------------------------------------------------------------+
void MLG_HistoryStats(int &entries_today, int &consec_losses, datetime &last_loss_time)
  {
   entries_today  = 0;
   consec_losses  = 0;
   last_loss_time = 0;
   datetime from = (datetime)((long)g_mlg_day - 14 * 86400);
   if(!HistorySelect(from, TimeCurrent() + 60))
      return;
   bool streak_open = true;
   for(int i = HistoryDealsTotal() - 1; i >= 0; i--)
     {
      ulong tk = HistoryDealGetTicket(i);
      if(tk == 0)
         continue;
      long type = HistoryDealGetInteger(tk, DEAL_TYPE);
      if(type != DEAL_TYPE_BUY && type != DEAL_TYPE_SELL)
         continue;
      long entry = HistoryDealGetInteger(tk, DEAL_ENTRY);
      datetime tm = (datetime)HistoryDealGetInteger(tk, DEAL_TIME);
      if(entry == DEAL_ENTRY_IN && tm >= g_mlg_day)
         entries_today++;
      if(streak_open && (entry == DEAL_ENTRY_OUT || entry == DEAL_ENTRY_INOUT || entry == DEAL_ENTRY_OUT_BY))
        {
         double net = HistoryDealGetDouble(tk, DEAL_PROFIT) + HistoryDealGetDouble(tk, DEAL_SWAP) +
                      HistoryDealGetDouble(tk, DEAL_COMMISSION) + HistoryDealGetDouble(tk, DEAL_FEE);
         if(net < 0.0)
           {
            consec_losses++;
            if(last_loss_time == 0)
               last_loss_time = tm;
           }
         else
            streak_open = false;
        }
     }
  }

//+------------------------------------------------------------------+
bool MLG_InHours(const int start_h, const int end_h, const int hour)
  {
   if(start_h < 0 || end_h < 0)
      return true;
   if(start_h < end_h)
      return (hour >= start_h && hour < end_h);
   return (hour >= start_h || hour < end_h);   // window wraps midnight
  }

int MLG_StepDigits(const double step)
  {
   int d = 0;
   double s = step;
   while(d < 8 && MathAbs(s - MathRound(s)) > 1e-9)
     {
      s *= 10.0;
      d++;
     }
   return d;
  }

//+------------------------------------------------------------------+
//| Lot size so that a stop-out costs risk_pct of equity. Rounds     |
//| DOWN to the volume step and returns 0 if even the minimum lot    |
//| would risk more than allowed (never rounds up). extra_points     |
//| widens the stop distance to budget for spread and slippage.      |
//+------------------------------------------------------------------+
double MLG_LotsForRisk(const string sym, const ENUM_ORDER_TYPE type, double entry, const double sl,
                       const double risk_pct, const double extra_points = 0.0)
  {
   if(sl <= 0.0 || risk_pct <= 0.0)
      return 0.0;
   if(entry <= 0.0)
      entry = (type == ORDER_TYPE_BUY) ? SymbolInfoDouble(sym, SYMBOL_ASK) : SymbolInfoDouble(sym, SYMBOL_BID);
   double pt = SymbolInfoDouble(sym, SYMBOL_POINT);
   double sl_eff = (type == ORDER_TYPE_BUY) ? sl - extra_points * pt : sl + extra_points * pt;

   double profit = 0.0;
   if(!OrderCalcProfit(type, sym, 1.0, entry, sl_eff, profit))
      return 0.0;
   double loss_per_lot = -profit;
   if(loss_per_lot <= 0.0)
      return 0.0;                       // stop on the wrong side of price

   double risk_money = AccountInfoDouble(ACCOUNT_EQUITY) * risk_pct / 100.0;
   double vmin  = SymbolInfoDouble(sym, SYMBOL_VOLUME_MIN);
   double vmax  = SymbolInfoDouble(sym, SYMBOL_VOLUME_MAX);
   double vstep = SymbolInfoDouble(sym, SYMBOL_VOLUME_STEP);
   if(vstep <= 0.0)
      return 0.0;

   double lots = MathFloor(risk_money / loss_per_lot / vstep + 1e-9) * vstep;
   if(lots < vmin - 1e-9)
      return 0.0;                       // minimum lot already risks too much
   double cap = MathMin(vmax, g_mlg_cfg.max_lots_per_trade);
   if(lots > cap)
      lots = MathFloor(cap / vstep + 1e-9) * vstep;
   return NormalizeDouble(lots, MLG_StepDigits(vstep));
  }

//+------------------------------------------------------------------+
//| Every pre-trade rule in one place. Returns false and a reason.   |
//+------------------------------------------------------------------+
bool MLG_AllowEntry(const string sym, const ENUM_ORDER_TYPE type, const double lots,
                    const double sl, string &reason)
  {
   reason = "";
   if(!g_mlg_ready)                    { reason = "guard not initialised"; return false; }
   if(!MLG_Check())
     {
      string why;
      MLG_IsHalted(why);
      reason = (why == "") ? "guard not ready" : why;
      return false;
     }
   if(type != ORDER_TYPE_BUY && type != ORDER_TYPE_SELL)
                                       { reason = "only market buy/sell supported"; return false; }
   if(sl <= 0.0)                       { reason = "no stop loss: every entry needs one"; return false; }
   if(lots <= 0.0)                     { reason = "lot size is zero"; return false; }

   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);
   if(!MLG_InHours(g_mlg_cfg.session_start_hour, g_mlg_cfg.session_end_hour, dt.hour))
                                       { reason = "outside trading session (server hours)"; return false; }
   if(g_mlg_cfg.friday_cutoff_hour >= 0 && dt.day_of_week == 5 && dt.hour >= g_mlg_cfg.friday_cutoff_hour)
                                       { reason = "Friday cutoff: no new entries before the weekend"; return false; }

   if(g_mlg_cfg.max_spread_points > 0.0)
     {
      long spread = SymbolInfoInteger(sym, SYMBOL_SPREAD);
      if((double)spread > g_mlg_cfg.max_spread_points)
                                       { reason = StringFormat("spread %d points > limit %.0f", (int)spread, g_mlg_cfg.max_spread_points); return false; }
     }

   if(lots > g_mlg_cfg.max_lots_per_trade + 1e-9)
                                       { reason = StringFormat("lots %.2f > per-trade cap %.2f", lots, g_mlg_cfg.max_lots_per_trade); return false; }

   int open_n; double open_lots;
   MLG_OpenStats(open_n, open_lots);
   if(open_n >= g_mlg_cfg.max_open_positions)
                                       { reason = StringFormat("%d open positions already (cap %d)", open_n, g_mlg_cfg.max_open_positions); return false; }
   if(open_lots + lots > g_mlg_cfg.max_total_lots + 1e-9)
                                       { reason = StringFormat("total lots would be %.2f (cap %.2f)", open_lots + lots, g_mlg_cfg.max_total_lots); return false; }

   int entries; int consec; datetime last_loss;
   MLG_HistoryStats(entries, consec, last_loss);
   if(g_mlg_cfg.max_entries_per_day > 0 && entries >= g_mlg_cfg.max_entries_per_day)
                                       { reason = StringFormat("%d entries today (cap %d)", entries, g_mlg_cfg.max_entries_per_day); return false; }
   if(g_mlg_cfg.max_consecutive_losses > 0 && consec >= g_mlg_cfg.max_consecutive_losses)
                                       { reason = StringFormat("%d consecutive losses (cap %d): needs a human look", consec, g_mlg_cfg.max_consecutive_losses); return false; }
   if(g_mlg_cfg.cooldown_minutes_after_loss > 0 && consec > 0 && last_loss > 0 &&
      TimeCurrent() < last_loss + g_mlg_cfg.cooldown_minutes_after_loss * 60)
                                       { reason = "cooling down after a loss"; return false; }

   // Loss if the stop is hit, versus equity.
   double price = (type == ORDER_TYPE_BUY) ? SymbolInfoDouble(sym, SYMBOL_ASK) : SymbolInfoDouble(sym, SYMBOL_BID);
   double profit = 0.0;
   if(!OrderCalcProfit(type, sym, lots, price, sl, profit))
                                       { reason = "cannot compute risk at the stop (OrderCalcProfit failed)"; return false; }
   double loss = -profit;
   if(loss <= 0.0)                     { reason = "stop is on the wrong side of price"; return false; }
   double eq = AccountInfoDouble(ACCOUNT_EQUITY);
   double risk_pct = loss / eq * 100.0;
   if(risk_pct > g_mlg_cfg.risk_per_trade_pct * 1.05)
                                       { reason = StringFormat("risk at stop %.2f%% > allowed %.2f%%", risk_pct, g_mlg_cfg.risk_per_trade_pct); return false; }
   if(g_mlg_cfg.max_daily_loss_pct > 0.0 && MLG_DailyLossPct() + risk_pct > g_mlg_cfg.max_daily_loss_pct)
                                       { reason = StringFormat("today's loss %.2f%% + this trade's %.2f%% would pass the daily limit", MLG_DailyLossPct(), risk_pct); return false; }

   if(g_mlg_cfg.min_margin_level_pct > 0.0)
     {
      double m = 0.0;
      if(!OrderCalcMargin(type, sym, lots, price, m))
                                       { reason = "cannot compute margin (OrderCalcMargin failed)"; return false; }
      double used = AccountInfoDouble(ACCOUNT_MARGIN);
      double level_after = eq / (used + m) * 100.0;
      if(level_after < g_mlg_cfg.min_margin_level_pct)
                                       { reason = StringFormat("margin level after entry %.0f%% < %.0f%%", level_after, g_mlg_cfg.min_margin_level_pct); return false; }
     }
   return true;
  }

//+------------------------------------------------------------------+
//| The only way a bot should open a trade. Checks, sends, logs.     |
//| If your broker (market execution) rejects an SL on the entry,    |
//| open then PositionModify, and flatten if the stop cannot be set: |
//| never leave a position without a server-side stop.               |
//+------------------------------------------------------------------+
bool MLG_Open(CTrade &trade, const string sym, const ENUM_ORDER_TYPE type, const double lots,
              const double sl, const double tp, const string comment = "")
  {
   string why;
   if(!MLG_AllowEntry(sym, type, lots, sl, why))
     {
      MLG_Log("REJECT", sym, why);
      return false;
     }
   bool sent = (type == ORDER_TYPE_BUY) ? trade.Buy(lots, sym, 0.0, sl, tp, comment)
                                        : trade.Sell(lots, sym, 0.0, sl, tp, comment);
   uint rc = trade.ResultRetcode();
   bool ok = sent && (rc == TRADE_RETCODE_DONE || rc == TRADE_RETCODE_PLACED);
   MLG_Log(ok ? "OPEN" : "OPEN_FAILED", sym,
           StringFormat("type=%d lots=%.2f sl=%.5f tp=%.5f retcode=%u", (int)type, lots, sl, tp, rc));
   return ok;
  }
//+------------------------------------------------------------------+
