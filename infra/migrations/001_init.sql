-- Provenance v0 schema. SQLite dialect; NUMERIC columns hold Decimal strings.
-- Porting to Postgres: NUMERIC -> NUMERIC(20,8), TEXT json -> JSONB, embedding TEXT -> VECTOR(1536).
CREATE TABLE IF NOT EXISTS managers (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, pod TEXT NOT NULL, strategy TEXT NOT NULL, onboarded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS instruments (
  id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL UNIQUE, type TEXT NOT NULL CHECK (type IN ('stock','option')),
  underlying TEXT, strike NUMERIC, expiry TEXT, call_put TEXT, sector TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS trades (
  id INTEGER PRIMARY KEY AUTOINCREMENT, manager_id TEXT NOT NULL REFERENCES managers(id),
  instrument_id INTEGER NOT NULL REFERENCES instruments(id), qty NUMERIC NOT NULL, price NUMERIC NOT NULL,
  trade_time TEXT NOT NULL, source_file TEXT NOT NULL, run_id INTEGER NOT NULL, external_trade_id TEXT NOT NULL,
  UNIQUE (manager_id, external_trade_id)
);
CREATE TABLE IF NOT EXISTS positions (
  manager_id TEXT NOT NULL, instrument_id INTEGER NOT NULL, as_of_date TEXT NOT NULL,
  qty NUMERIC NOT NULL, avg_cost NUMERIC NOT NULL, run_id INTEGER NOT NULL,
  PRIMARY KEY (manager_id, instrument_id, as_of_date)
);
CREATE TABLE IF NOT EXISTS prices (
  instrument_id INTEGER NOT NULL, as_of_date TEXT NOT NULL, close NUMERIC NOT NULL, source TEXT NOT NULL,
  fetched_at TEXT NOT NULL, run_id INTEGER NOT NULL, PRIMARY KEY (instrument_id, as_of_date, source)
);
CREATE TABLE IF NOT EXISTS rates (
  as_of_date TEXT NOT NULL, tenor TEXT NOT NULL, rate NUMERIC NOT NULL, PRIMARY KEY (as_of_date, tenor)
);
CREATE TABLE IF NOT EXISTS quarantine (
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER NOT NULL, table_name TEXT NOT NULL, raw_row TEXT NOT NULL,
  rule TEXT NOT NULL, reason TEXT NOT NULL, manager_id TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pipeline_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_date TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
  status TEXT NOT NULL, rows_in INTEGER DEFAULT 0, rows_quarantined INTEGER DEFAULT 0, error TEXT, report TEXT
);
CREATE TABLE IF NOT EXISTS documents (
  id INTEGER PRIMARY KEY AUTOINCREMENT, manager_id TEXT, title TEXT NOT NULL, doc_type TEXT NOT NULL,
  source_url TEXT, published_at TEXT, content_hash TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS chunks (
  id INTEGER PRIMARY KEY AUTOINCREMENT, document_id INTEGER NOT NULL REFERENCES documents(id), chunk_index INTEGER NOT NULL,
  text TEXT NOT NULL, embedding TEXT, manager_id TEXT, UNIQUE (document_id, chunk_index)
);
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT NOT NULL UNIQUE, role TEXT NOT NULL CHECK (role IN ('pm','risk','engineer')),
  manager_id TEXT, token TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS copilot_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, question TEXT NOT NULL, tool_calls TEXT NOT NULL,
  answer TEXT NOT NULL, citations TEXT NOT NULL, latency_ms INTEGER NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_trades_mgr_time ON trades(manager_id, trade_time);
CREATE INDEX IF NOT EXISTS idx_prices_date ON prices(as_of_date);
CREATE INDEX IF NOT EXISTS idx_chunks_mgr ON chunks(manager_id);
