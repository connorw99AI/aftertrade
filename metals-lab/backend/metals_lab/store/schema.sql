-- Durable operational state. Account numbers are never stored: only salted fingerprints.
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS control (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT);

CREATE TABLE IF NOT EXISTS activation (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  fingerprint TEXT NOT NULL,
  server TEXT,
  armed_at TEXT NOT NULL,
  disarmed_at TEXT,
  disarm_reason TEXT,
  active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS capital_snapshot (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  fingerprint TEXT NOT NULL UNIQUE,
  starting_balance REAL NOT NULL,
  currency TEXT,
  bot_fraction REAL NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bots (
  bot_id TEXT PRIMARY KEY,
  enabled INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'STOPPED',          -- STOPPED | RUNNING | PAUSED | PAUSED_RISK
  pause_reason TEXT,
  params_json TEXT NOT NULL DEFAULT '{}',
  config_version INTEGER NOT NULL DEFAULT 1,
  updated_at TEXT
);

CREATE TABLE IF NOT EXISTS config_audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  bot_id TEXT, action TEXT, old_json TEXT, new_json TEXT, operator TEXT, created_at TEXT
);

CREATE TABLE IF NOT EXISTS ledger (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  bot_id TEXT NOT NULL,
  ts TEXT NOT NULL,
  kind TEXT NOT NULL,                               -- ALLOCATION | REALIZED_PNL | COMMISSION | SWAP | FEE | ADJUSTMENT
  amount REAL NOT NULL,
  ref TEXT,                                         -- e.g. deal ticket; makes reconciliation idempotent
  note TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_ledger_kind_ref ON ledger(kind, ref) WHERE ref IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_ledger_bot ON ledger(bot_id);

CREATE TABLE IF NOT EXISTS intents (
  intent_id TEXT PRIMARY KEY,
  bot_id TEXT NOT NULL,
  signal_id TEXT NOT NULL,
  intent TEXT NOT NULL,
  symbol TEXT NOT NULL,
  side TEXT NOT NULL,
  volume REAL NOT NULL,
  position_key TEXT,
  group_id TEXT,
  state TEXT NOT NULL,                              -- CREATED | SUBMITTING | FILLED | PARTIAL | REJECTED | UNKNOWN | CANCELLED
  mt5_order INTEGER, mt5_deal INTEGER, position_ticket INTEGER,
  filled_volume REAL, fill_price REAL, retcode INTEGER, comment_tag TEXT,
  proposal_json TEXT, error TEXT,
  created_at TEXT NOT NULL, updated_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_intent_signal ON intents(bot_id, signal_id);

CREATE TABLE IF NOT EXISTS approvals (
  approval_id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,                               -- AUDIT | RISK
  proposal_hash TEXT NOT NULL,
  decision TEXT NOT NULL,
  fingerprint TEXT,
  ruleset_version TEXT,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  used_at TEXT,
  revoked INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS audit_decisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT, bot_id TEXT, symbol TEXT, intent TEXT, decision TEXT, triggered_json TEXT, reasons_json TEXT,
  evidence_json TEXT, data_quality TEXT, news_status TEXT, ruleset_version TEXT, reassess_at TEXT,
  proposal_hash TEXT, mode TEXT
);

CREATE TABLE IF NOT EXISTS risk_decisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT, bot_id TEXT, symbol TEXT, intent TEXT, decision TEXT, reasons_json TEXT, metrics_json TEXT,
  proposal_hash TEXT, mode TEXT
);

CREATE TABLE IF NOT EXISTS positions (
  position_key TEXT PRIMARY KEY,
  bot_id TEXT NOT NULL,
  ticket INTEGER UNIQUE,
  symbol TEXT, role TEXT, side TEXT,
  volume_initial REAL, volume_open REAL,
  entry_price REAL, sl REAL, tp REAL,
  opened_at TEXT, closed_at TEXT,
  status TEXT NOT NULL,                             -- OPEN | CLOSED
  group_id TEXT, reserved_margin REAL, planned_risk REAL, max_hold_bars INTEGER,
  bars_timeframe TEXT, signal_json TEXT, intent_id TEXT,
  config_version INTEGER                            -- the bot's rule version when it opened
);

CREATE TABLE IF NOT EXISTS deals (
  ticket INTEGER PRIMARY KEY,
  order_ticket INTEGER, position_id INTEGER, bot_id TEXT, symbol TEXT,
  type INTEGER, entry INTEGER, volume REAL, price REAL,
  profit REAL, commission REAL, swap REAL, fee REAL,
  magic INTEGER, time INTEGER, comment TEXT,
  attributed INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS trades (
  trade_id TEXT PRIMARY KEY,
  bot_id TEXT NOT NULL, group_id TEXT, position_keys_json TEXT,
  symbol TEXT, side TEXT, volume REAL,
  entry_time TEXT, exit_time TEXT, entry_price REAL, exit_price REAL,
  gross REAL, commission REAL, swap REAL, fee REAL, net REAL,
  provisional INTEGER NOT NULL DEFAULT 1, finalized_at TEXT,
  entry_rationale TEXT, exit_rationale TEXT,
  config_version INTEGER                            -- the bot's rule version when the trade opened
);

CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL, kind TEXT NOT NULL, bot_id TEXT, payload_json TEXT
);

CREATE TABLE IF NOT EXISTS alerts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL, severity TEXT, code TEXT, message TEXT,
  blocks_entries INTEGER NOT NULL DEFAULT 0, resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS signals_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT, bot_id TEXT, mode TEXT, outcome TEXT, signal_json TEXT, explanation TEXT
);

CREATE TABLE IF NOT EXISTS unallocated_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT, amount REAL, deal_ticket INTEGER UNIQUE, kind TEXT, acknowledged INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS equity_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT, bot_id TEXT, equity REAL, realized REAL, unrealized REAL, reserved_margin REAL
);
CREATE TABLE IF NOT EXISTS bot_day_start (
  bot_id TEXT, day TEXT, equity REAL,
  high REAL,                                        -- best equity so far that day (profit lock)
  stopped TEXT,                                     -- the daily stop that ended the day, once one has; it holds until the day ends
  PRIMARY KEY (bot_id, day)
);
CREATE TABLE IF NOT EXISTS bot_peak (bot_id TEXT PRIMARY KEY, peak REAL);
CREATE TABLE IF NOT EXISTS bot_week_start (
  bot_id TEXT, week TEXT, equity REAL,             -- week: Monday (UTC) of the week
  stopped TEXT,                                     -- the weekly stop, once hit; holds until the week ends
  PRIMARY KEY (bot_id, week)
);

CREATE TABLE IF NOT EXISTS experiments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT, strategy_id TEXT, dataset_key TEXT, kind TEXT, params_json TEXT, period TEXT,
  metrics_json TEXT, status TEXT, notes TEXT, report_json TEXT
);

CREATE TABLE IF NOT EXISTS holdout_usage (
  strategy_id TEXT, dataset_key TEXT, params_json TEXT, used_at TEXT,
  PRIMARY KEY (strategy_id, dataset_key)
);

CREATE TABLE IF NOT EXISTS backtests (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT, strategy_id TEXT, dataset_key TEXT, params_json TEXT, config_json TEXT,
  metrics_json TEXT, diagnostics_json TEXT, reliability_json TEXT, trades_json TEXT, equity_json TEXT
);

CREATE TABLE IF NOT EXISTS reviews (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL, bot_id TEXT NOT NULL, source TEXT NOT NULL, trigger TEXT, review_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_reviews_bot ON reviews(bot_id, source, id);

-- assessor suggestions the operator applied: the same suggestion (bot, id, change) is shown folded as applied
CREATE TABLE IF NOT EXISTS applied_suggestions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  bot_id TEXT NOT NULL, suggestion_id TEXT NOT NULL, change_json TEXT NOT NULL,
  review_id INTEGER, source TEXT, config_version INTEGER, operator TEXT, applied_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_applied_bot ON applied_suggestions(bot_id, suggestion_id);

-- the assessor's tests of candidate changes (optimise.py): one row per run, newest per bot is shown
CREATE TABLE IF NOT EXISTS optimisations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  bot_id TEXT NOT NULL, created_at TEXT NOT NULL, finished_at TEXT,
  status TEXT NOT NULL,                 -- QUEUED | RUNNING | DONE | FAILED
  progress TEXT, trigger TEXT, dataset TEXT, start TEXT, "end" TEXT,
  config_version INTEGER, params_json TEXT, result_json TEXT, error TEXT
);
CREATE INDEX IF NOT EXISTS ix_optimisations_bot ON optimisations(bot_id, id);
