//+------------------------------------------------------------------+
//| ExampleEA.mq5 - skeleton showing how a Metal Lab bot plugs into  |
//| MetalLabRiskGuard.mqh. It has NO strategy: Signal() returns 0.   |
//| Not compiled by the author; compile and demo-test first.         |
//+------------------------------------------------------------------+
#property copyright "Metal Lab"
#property version   "1.00"

#include <Trade\Trade.mqh>
#include <MetalLabRiskGuard.mqh>

input long   InpMagic                 = 1001;     // unique per bot
input double InpStopPoints            = 800;      // placeholder stop distance (points)
input double InpTakeProfitPoints      = 1200;     // placeholder target distance (points)
input double InpExtraStopPoints       = 30;       // budget for spread + slippage when sizing

input group "Metal Lab risk guard (account-wide)"
input double InpRiskPerTradePct       = 0.5;
input double InpMaxDailyLossPct       = 2.0;
input double InpMaxWeeklyLossPct      = 4.0;
input double InpMaxDrawdownPct        = 8.0;
input int    InpMaxOpenPositions      = 3;
input double InpMaxLotsPerTrade       = 0.5;
input double InpMaxTotalLots          = 1.0;
input int    InpMaxEntriesPerDay      = 6;
input int    InpMaxConsecLosses       = 4;
input int    InpCooldownMinutes       = 30;
input double InpMaxSpreadPoints       = 40;
input double InpMinMarginLevelPct     = 400;
input int    InpSessionStartHour      = -1;       // SERVER time, -1 = off
input int    InpSessionEndHour        = -1;
input int    InpFridayCutoffHour      = -1;
input bool   InpCloseOnBreach         = true;
input bool   InpResetPeak             = false;    // true ONCE after a deliberate drawdown reset

CTrade   trade;
datetime g_last_bar = 0;

//--- YOUR BOT LOGIC GOES HERE: +1 buy, -1 sell, 0 nothing
int Signal()
  {
   return 0;
  }

int OnInit()
  {
   trade.SetExpertMagicNumber(InpMagic);
   trade.SetDeviationInPoints(50);

   MLGuardConfig c;
   MLG_Defaults(c);
   c.risk_per_trade_pct          = InpRiskPerTradePct;
   c.max_daily_loss_pct          = InpMaxDailyLossPct;
   c.max_weekly_loss_pct         = InpMaxWeeklyLossPct;
   c.max_drawdown_pct            = InpMaxDrawdownPct;
   c.max_open_positions          = InpMaxOpenPositions;
   c.max_lots_per_trade          = InpMaxLotsPerTrade;
   c.max_total_lots              = InpMaxTotalLots;
   c.max_entries_per_day         = InpMaxEntriesPerDay;
   c.max_consecutive_losses      = InpMaxConsecLosses;
   c.cooldown_minutes_after_loss = InpCooldownMinutes;
   c.max_spread_points           = InpMaxSpreadPoints;
   c.min_margin_level_pct        = InpMinMarginLevelPct;
   c.session_start_hour          = InpSessionStartHour;
   c.session_end_hour            = InpSessionEndHour;
   c.friday_cutoff_hour          = InpFridayCutoffHour;
   c.close_on_breach             = InpCloseOnBreach;

   if(!MLG_Init(c, "MetalLab_guard_log.csv", InpResetPeak))
     {
      Print("MetalLabRiskGuard failed to initialise");
      return INIT_FAILED;
     }
   EventSetTimer(5);   // the guard must keep running when the market is quiet
   return INIT_SUCCEEDED;
  }

void OnDeinit(const int reason)
  {
   EventKillTimer();
  }

void OnTimer()
  {
   MLG_Check();
  }

void OnTick()
  {
   if(!MLG_Check())
      return;                                   // halted, or guard not ready

   datetime bar = iTime(_Symbol, PERIOD_CURRENT, 0);
   if(bar == g_last_bar)
      return;                                   // one decision per bar
   g_last_bar = bar;

   int signal = Signal();
   if(signal == 0)
      return;

   ENUM_ORDER_TYPE type = (signal > 0) ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
   double pt    = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   double entry = (signal > 0) ? SymbolInfoDouble(_Symbol, SYMBOL_ASK) : SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double sl    = (signal > 0) ? entry - InpStopPoints * pt       : entry + InpStopPoints * pt;
   double tp    = (signal > 0) ? entry + InpTakeProfitPoints * pt : entry - InpTakeProfitPoints * pt;

   double lots = MLG_LotsForRisk(_Symbol, type, entry, sl, InpRiskPerTradePct, InpExtraStopPoints);
   if(lots <= 0.0)
     {
      MLG_Log("SKIP", _Symbol, "lot size is zero: minimum lot would risk too much, or risk could not be computed");
      return;
     }
   MLG_Open(trade, _Symbol, type, lots, sl, tp, "MetalLab");
  }
