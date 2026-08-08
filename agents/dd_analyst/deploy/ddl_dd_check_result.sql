-- =============================================================================
-- dd_check_result：检查结果落库表
-- =============================================================================
-- 用途：可选 HTTP API（api.py）在检查完成后保存/查询结果。
-- 触发条件：配置了 DD_CHECK_SQLITE_PATH 时才用库；未配置则用内存，不需要本表。
-- MCP 主路径（run_dd_check）当前不写这张表。
--
-- 本仓库实现是 SQLite（文件路径 = DD_CHECK_SQLITE_PATH）。
-- 请先建库文件再建表，例如：
--   sqlite3 ./dd_check_results.sqlite3 < ddl_dd_check_result.sql
-- =============================================================================

CREATE TABLE IF NOT EXISTS dd_check_result (
  id         TEXT PRIMARY KEY,          -- 结果 ID（代码生成的 uuid hex）
  report_id  TEXT,                      -- 报告号
  invest_id  TEXT,                      -- 调查单号
  phase      TEXT,                      -- CHECK / RECHECK 等
  score      REAL,                      -- 综合分
  grade      TEXT,                      -- 等级 A–E
  payload    TEXT NOT NULL,             -- CheckResult 的 JSON 全文
  saved_at   REAL NOT NULL              -- Unix 时间戳（秒，浮点）
);

CREATE INDEX IF NOT EXISTS idx_dd_check_result_saved_at
  ON dd_check_result (saved_at DESC);

-- 代码读写：
-- INSERT INTO dd_check_result(id, report_id, invest_id, phase, score, grade, payload, saved_at) VALUES (?,?,?,?,?,?,?,?)
-- SELECT id, saved_at, payload FROM dd_check_result WHERE id=?
-- SELECT id, saved_at, payload FROM dd_check_result ORDER BY saved_at DESC LIMIT ?
