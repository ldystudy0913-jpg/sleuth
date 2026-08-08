"""Optional MySQL ddp_file metadata reader (lazy import)."""
from __future__ import annotations

from typing import List

from ..config import Settings
from . import AttachmentMeta


class MysqlDdpFileStore:
    def __init__(self, settings: Settings):
        self.settings = settings

    def configured(self) -> bool:
        s = self.settings
        return bool(s.mysql_host and s.mysql_user and s.mysql_database)

    def list_by_invest_id(self, invest_id: str) -> List[AttachmentMeta]:
        if not self.configured():
            return []
        try:
            import pymysql
        except ImportError as exc:
            raise RuntimeError("PyMySQL required: pip install dd-check[mysql]") from exc

        s = self.settings
        table = s.mysql_ddp_file_table
        col_invest = s.mysql_invest_id_column
        col_path = s.mysql_location_path_column
        sql = (
            f"SELECT {col_path} AS location_path "
            f"FROM {table} WHERE {col_invest}=%s"
        )
        conn = pymysql.connect(
            host=s.mysql_host,
            port=s.mysql_port,
            user=s.mysql_user,
            password=s.mysql_password or "",
            database=s.mysql_database,
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
        )
        try:
            with conn.cursor() as cur:
                cur.execute(sql, (invest_id,))
                rows = cur.fetchall() or []
        finally:
            conn.close()
        out: List[AttachmentMeta] = []
        for i, row in enumerate(rows):
            path = str(row.get("location_path") or "")
            if not path:
                continue
            out.append(AttachmentMeta(file_id=f"{invest_id}-{i}", location_path=path))
        return out
