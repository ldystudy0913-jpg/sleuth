-- =============================================================================
-- LangGraph SqliteSaver 表（dd_analyst 持久 checkpoint）
-- =============================================================================
-- 用途：按 thread_id 保存图节点状态，支持 HITL resume、跨进程续跑、时间旅行回滚。
-- 代码行为：只读写，不执行 CREATE（见 dd_check/graph/checkpoint.py）。
--
-- 配置：DD_CHECK_CHECKPOINT_SQLITE_PATH=./dd_check_checkpoints.sqlite3
-- 建库示例：
--   sqlite3 ./dd_check_checkpoints.sqlite3 < deploy/ddl_langgraph_checkpoint.sql
-- =============================================================================

PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS checkpoints (
  thread_id TEXT NOT NULL,
  checkpoint_ns TEXT NOT NULL DEFAULT '',
  checkpoint_id TEXT NOT NULL,
  parent_checkpoint_id TEXT,
  type TEXT,
  checkpoint BLOB,
  metadata BLOB,
  PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
);

CREATE TABLE IF NOT EXISTS writes (
  thread_id TEXT NOT NULL,
  checkpoint_ns TEXT NOT NULL DEFAULT '',
  checkpoint_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  idx INTEGER NOT NULL,
  channel TEXT NOT NULL,
  type TEXT,
  value BLOB,
  PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
);
