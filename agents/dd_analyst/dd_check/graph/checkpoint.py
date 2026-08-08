"""持久化 LangGraph checkpointer（SQLite，运维预建表）。

配置 DD_CHECK_CHECKPOINT_SQLITE_PATH；表结构见 deploy/ddl_langgraph_checkpoint.sql。
本模块不执行 CREATE TABLE。
"""
from __future__ import annotations

import pickle
import sqlite3
from pathlib import Path
from typing import Any, Optional, Union

from langgraph.checkpoint.sqlite import SqliteSaver

_REQUIRED_TABLES = ("checkpoints", "writes")

# 进程内单例
_CONN: Optional[sqlite3.Connection] = None
_SAVER: Optional["OpsOwnedSqliteSaver"] = None
_SAVER_PATH: Optional[str] = None


class PickleSerde:
    """允许 Settings / Strategy / Pydantic 进入 checkpoint。"""

    def dumps_typed(self, obj: Any) -> tuple[str, bytes]:
        return "pickle", pickle.dumps(obj)

    def loads_typed(self, data: tuple[str, bytes]) -> Any:
        return pickle.loads(data[1])


# 兼容旧名
_PickleSerde = PickleSerde


class CheckpointSchemaError(RuntimeError):
    """checkpoint 库缺少运维应预建的表。"""


class OpsOwnedSqliteSaver(SqliteSaver):
    """SqliteSaver：setup 只校验表存在，不 CREATE。"""

    def setup(self) -> None:
        if self.is_setup:
            return
        cur = self.conn.cursor()
        try:
            cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name IN (?, ?)",
                _REQUIRED_TABLES,
            )
            found = {row[0] for row in cur.fetchall()}
        finally:
            cur.close()
        missing = [t for t in _REQUIRED_TABLES if t not in found]
        if missing:
            raise CheckpointSchemaError(
                "LangGraph checkpoint tables missing: "
                + ", ".join(missing)
                + ". Run agents/dd_analyst/deploy/ddl_langgraph_checkpoint.sql "
                "against DD_CHECK_CHECKPOINT_SQLITE_PATH before starting."
            )
        self.is_setup = True


def get_sqlite_checkpointer(
    path: Union[str, Path],
    *,
    serde: Any = None,
) -> OpsOwnedSqliteSaver:
    """获取（或打开）指向 path 的进程内单例 checkpointer。"""
    global _CONN, _SAVER, _SAVER_PATH
    resolved = str(Path(path).expanduser().resolve())
    if _SAVER is not None and _SAVER_PATH == resolved:
        return _SAVER
    close_checkpointer()
    conn = sqlite3.connect(resolved, check_same_thread=False)
    saver = OpsOwnedSqliteSaver(conn, serde=serde or PickleSerde())
    # 立即校验表，便于启动期失败
    saver.setup()
    _CONN = conn
    _SAVER = saver
    _SAVER_PATH = resolved
    return saver


def close_checkpointer() -> None:
    """测试用：关闭连接并清空单例。"""
    global _CONN, _SAVER, _SAVER_PATH
    if _CONN is not None:
        try:
            _CONN.close()
        except Exception:
            pass
    _CONN = None
    _SAVER = None
    _SAVER_PATH = None


def apply_checkpoint_ddl(path: Union[str, Path]) -> None:
    """测试辅助：对空库应用官方 DDL（生产环境由运维执行，不经业务路径调用）。"""
    ddl = (
        Path(__file__).resolve().parents[2]
        / "deploy"
        / "ddl_langgraph_checkpoint.sql"
    )
    sql = ddl.read_text(encoding="utf-8")
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p))
    try:
        conn.executescript(sql)
        conn.commit()
    finally:
        conn.close()
