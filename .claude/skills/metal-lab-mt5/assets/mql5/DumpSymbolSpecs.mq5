//+------------------------------------------------------------------+
//| DumpSymbolSpecs.mq5 - script. Writes your broker's contract specs|
//| for the listed symbols to JSON in Common/Files, for size.py.     |
//| Tick value, lot step and margin differ per broker, so dump them  |
//| from the account you trade rather than trusting examples.        |
//| Not compiled by the author; compile in MetaEditor first.         |
//+------------------------------------------------------------------+
#property copyright "Metal Lab"
#property version   "1.00"
#property script_show_inputs

input string InpSymbols = "XAUUSD,XAGUSD";   // your broker's exact symbol names (suffixes included)
input string InpFile    = "MetalLab_specs.json";

string Num(const double v)
  {
   return DoubleToString(v, 10);
  }

void OnStart()
  {
   string syms[];
   int n = StringSplit(InpSymbols, ',', syms);

   string json = "{\n  \"account\": {\"currency\": \"" + AccountInfoString(ACCOUNT_CURRENCY) +
                 "\", \"leverage\": " + (string)AccountInfoInteger(ACCOUNT_LEVERAGE) +
                 ", \"margin_mode\": " + (string)AccountInfoInteger(ACCOUNT_MARGIN_MODE) + "},\n  \"symbols\": {\n";
   bool first = true;
   for(int i = 0; i < n; i++)
     {
      string s = syms[i];
      StringTrimLeft(s);
      StringTrimRight(s);
      if(s == "")
         continue;
      if(!SymbolSelect(s, true))
        {
         Print("Unknown symbol: ", s);
         continue;
        }
      if(!first)
         json += ",\n";
      first = false;
      json += "    \"" + s + "\": {" +
              "\"digits\": " + (string)SymbolInfoInteger(s, SYMBOL_DIGITS) +
              ", \"point\": " + Num(SymbolInfoDouble(s, SYMBOL_POINT)) +
              ", \"tick_size\": " + Num(SymbolInfoDouble(s, SYMBOL_TRADE_TICK_SIZE)) +
              ", \"tick_value\": " + Num(SymbolInfoDouble(s, SYMBOL_TRADE_TICK_VALUE)) +
              ", \"tick_value_loss\": " + Num(SymbolInfoDouble(s, SYMBOL_TRADE_TICK_VALUE_LOSS)) +
              ", \"tick_value_profit\": " + Num(SymbolInfoDouble(s, SYMBOL_TRADE_TICK_VALUE_PROFIT)) +
              ", \"contract_size\": " + Num(SymbolInfoDouble(s, SYMBOL_TRADE_CONTRACT_SIZE)) +
              ", \"volume_min\": " + Num(SymbolInfoDouble(s, SYMBOL_VOLUME_MIN)) +
              ", \"volume_max\": " + Num(SymbolInfoDouble(s, SYMBOL_VOLUME_MAX)) +
              ", \"volume_step\": " + Num(SymbolInfoDouble(s, SYMBOL_VOLUME_STEP)) +
              ", \"spread_points\": " + (string)SymbolInfoInteger(s, SYMBOL_SPREAD) +
              ", \"stops_level_points\": " + (string)SymbolInfoInteger(s, SYMBOL_TRADE_STOPS_LEVEL) +
              ", \"margin_initial\": " + Num(SymbolInfoDouble(s, SYMBOL_MARGIN_INITIAL)) +
              ", \"swap_long\": " + Num(SymbolInfoDouble(s, SYMBOL_SWAP_LONG)) +
              ", \"swap_short\": " + Num(SymbolInfoDouble(s, SYMBOL_SWAP_SHORT)) +
              ", \"calc_mode\": " + (string)SymbolInfoInteger(s, SYMBOL_TRADE_CALC_MODE) +
              ", \"exec_mode\": " + (string)SymbolInfoInteger(s, SYMBOL_TRADE_EXEMODE) +
              ", \"currency_profit\": \"" + SymbolInfoString(s, SYMBOL_CURRENCY_PROFIT) + "\"}";
     }
   json += "\n  }\n}\n";

   int h = FileOpen(InpFile, FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_COMMON);
   if(h == INVALID_HANDLE)
     {
      Print("Cannot open ", InpFile, " for writing, error ", GetLastError());
      return;
     }
   FileWriteString(h, json);
   FileClose(h);
   Print("Wrote specs to ", TerminalInfoString(TERMINAL_COMMONDATA_PATH), "\\Files\\", InpFile);
  }
