//+------------------------------------------------------------------+
//| ExportDeals.mq5 - script. Writes the account's deal history to a |
//| CSV in the Common/Files folder for scripts/review.py.            |
//| Run it from the Navigator: drag onto any chart. Not compiled by  |
//| the author; compile in MetaEditor first.                         |
//+------------------------------------------------------------------+
#property copyright "Metal Lab"
#property version   "1.00"
#property script_show_inputs

input int    InpDaysBack = 365;
input string InpFile     = "MetalLab_deals.csv";

void OnStart()
  {
   datetime to   = TimeCurrent();
   datetime from = (datetime)((long)to - (long)InpDaysBack * 86400);
   if(!HistorySelect(from, to))
     {
      Print("HistorySelect failed");
      return;
     }
   int h = FileOpen(InpFile, FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_COMMON);
   if(h == INVALID_HANDLE)
     {
      Print("Cannot open ", InpFile, " for writing, error ", GetLastError());
      return;
     }
   FileWriteString(h, "ticket,position_id,time,symbol,type,entry,volume,price,commission,swap,fee,profit,magic,comment\r\n");

   int n = HistoryDealsTotal();
   for(int i = 0; i < n; i++)
     {
      ulong tk = HistoryDealGetTicket(i);
      if(tk == 0)
         continue;
      string comment = HistoryDealGetString(tk, DEAL_COMMENT);
      StringReplace(comment, ",", ";");
      StringReplace(comment, "\"", "");
      string line = StringFormat("%I64u,%I64d,%s,%s,%d,%d,%.2f,%.5f,%.2f,%.2f,%.2f,%.2f,%I64d,%s\r\n",
                                 tk,
                                 HistoryDealGetInteger(tk, DEAL_POSITION_ID),
                                 TimeToString((datetime)HistoryDealGetInteger(tk, DEAL_TIME), TIME_DATE | TIME_SECONDS),
                                 HistoryDealGetString(tk, DEAL_SYMBOL),
                                 (int)HistoryDealGetInteger(tk, DEAL_TYPE),
                                 (int)HistoryDealGetInteger(tk, DEAL_ENTRY),
                                 HistoryDealGetDouble(tk, DEAL_VOLUME),
                                 HistoryDealGetDouble(tk, DEAL_PRICE),
                                 HistoryDealGetDouble(tk, DEAL_COMMISSION),
                                 HistoryDealGetDouble(tk, DEAL_SWAP),
                                 HistoryDealGetDouble(tk, DEAL_FEE),
                                 HistoryDealGetDouble(tk, DEAL_PROFIT),
                                 HistoryDealGetInteger(tk, DEAL_MAGIC),
                                 comment);
      FileWriteString(h, line);
     }
   FileClose(h);
   Print("Wrote ", n, " deals to ", TerminalInfoString(TERMINAL_COMMONDATA_PATH), "\\Files\\", InpFile);
  }
