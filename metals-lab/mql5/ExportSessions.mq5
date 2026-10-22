//+------------------------------------------------------------------+
//| ExportSessions.mq5                                               |
//| Read-only script. Exports trade/quote session windows for the    |
//| listed symbols using the documented SymbolInfoSessionTrade /     |
//| SymbolInfoSessionQuote functions, because the MetaTrader5 Python |
//| package does not expose session schedules.                       |
//|                                                                  |
//| Usage: copy to <Data Folder>\MQL5\Scripts, compile in MetaEditor,|
//| drag onto any chart. Output: <Common Data Folder>\Files\         |
//|   metals_lab_sessions.json                                       |
//| Times are seconds from the start of the day in SERVER time.      |
//| Day numbering: 0=Sunday ... 6=Saturday (ENUM_DAY_OF_WEEK).        |
//| This script places no orders.                                    |
//+------------------------------------------------------------------+
#property script_show_inputs
input string InpSymbols = "XAUUSD,XAGUSD"; // comma-separated, exact terminal names

void WriteSessions(int h, string sym, bool trade)
  {
   bool first = true;
   for(int d = 0; d < 7; d++)
     {
      for(uint i = 0; i < 10; i++)
        {
         datetime from, to;
         bool ok = trade ? SymbolInfoSessionTrade(sym, (ENUM_DAY_OF_WEEK)d, i, from, to)
                         : SymbolInfoSessionQuote(sym, (ENUM_DAY_OF_WEEK)d, i, from, to);
         if(!ok) break;
         if(!first) FileWriteString(h, ",");
         first = false;
         FileWriteString(h, StringFormat("{\"day\":%d,\"index\":%d,\"from\":%d,\"to\":%d}", d, i, (long)from, (long)to));
        }
     }
  }

void OnStart()
  {
   string syms[];
   int n = StringSplit(InpSymbols, ',', syms);
   int h = FileOpen("metals_lab_sessions.json", FILE_WRITE | FILE_TXT | FILE_COMMON | FILE_ANSI);
   if(h == INVALID_HANDLE) { Print("cannot open output file: ", GetLastError()); return; }
   FileWriteString(h, StringFormat("{\"exported_at\":\"%s\",\"server\":\"%s\",\"symbols\":{",
                   TimeToString(TimeCurrent(), TIME_DATE | TIME_SECONDS), AccountInfoString(ACCOUNT_SERVER)));
   for(int k = 0; k < n; k++)
     {
      string s = syms[k];
      StringTrimLeft(s); StringTrimRight(s);
      if(!SymbolSelect(s, true)) { Print("symbol not found: ", s); continue; }
      if(k > 0) FileWriteString(h, ",");
      FileWriteString(h, StringFormat("\"%s\":{\"trade\":[", s));
      WriteSessions(h, s, true);
      FileWriteString(h, "],\"quote\":[");
      WriteSessions(h, s, false);
      FileWriteString(h, "]}");
     }
   FileWriteString(h, "}}");
   FileClose(h);
   Print("metals_lab_sessions.json written to the Common\\Files folder");
  }
