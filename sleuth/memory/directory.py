"""Identity directory on the session MySQL/SQLite database. Schema is hand-built."""
from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterable, List, Optional

from ..storage.sqlite import default_db_path
from ..util.ids import grant_id
from . import settings
from .models import GrantRecord, OrgRecord, RoleRecord, UserRecord

log = logging.getLogger("sleuth.memory.directory")


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _now_sql() -> str:
    return utc_now().strftime("%Y-%m-%d %H:%M:%S")


class Directory:
    def available(self) -> bool:
        return True

    def get_user(self, user_id: str) -> Optional[UserRecord]:
        raise NotImplementedError

    def upsert_user(self, rec: UserRecord) -> UserRecord:
        raise NotImplementedError

    def get_org(self, org_id: str) -> Optional[OrgRecord]:
        raise NotImplementedError

    def get_role(self, role_id: str) -> Optional[RoleRecord]:
        raise NotImplementedError

    def list_grants(
        self,
        *,
        resource_kind: Optional[str] = None,
        resource_id: Optional[str] = None,
        scope_kind: Optional[str] = None,
        scope_id: Optional[str] = None,
        active_only: bool = True,
    ) -> List[GrantRecord]:
        raise NotImplementedError

    def upsert_grant(self, rec: GrantRecord) -> GrantRecord:
        raise NotImplementedError

    def get_grant(self, grant_id_value: str) -> Optional[GrantRecord]:
        raise NotImplementedError


class NullDirectory(Directory):
    def available(self) -> bool:
        return False

    def get_user(self, user_id: str) -> Optional[UserRecord]:
        return None

    def upsert_user(self, rec: UserRecord) -> UserRecord:
        raise RuntimeError("directory tables are not available")

    def get_org(self, org_id: str) -> Optional[OrgRecord]:
        return None

    def get_role(self, role_id: str) -> Optional[RoleRecord]:
        return None

    def list_grants(
        self,
        *,
        resource_kind: Optional[str] = None,
        resource_id: Optional[str] = None,
        scope_kind: Optional[str] = None,
        scope_id: Optional[str] = None,
        active_only: bool = True,
    ) -> List[GrantRecord]:
        return []

    def upsert_grant(self, rec: GrantRecord) -> GrantRecord:
        raise RuntimeError("directory tables are not available")

    def get_grant(self, grant_id_value: str) -> Optional[GrantRecord]:
        return None


class InMemoryDirectory(Directory):
    def __init__(self, config=None):
        self.config = config
        self.users: dict[str, UserRecord] = {}
        self.orgs: dict[str, OrgRecord] = {}
        self.roles: dict[str, RoleRecord] = {}
        self.grants: dict[str, GrantRecord] = {}

    def available(self) -> bool:
        return True

    def get_user(self, user_id: str) -> Optional[UserRecord]:
        return self.users.get(user_id)

    def upsert_user(self, rec: UserRecord) -> UserRecord:
        self.users[rec.user_id] = rec
        return rec

    def get_org(self, org_id: str) -> Optional[OrgRecord]:
        return self.orgs.get(org_id)

    def get_role(self, role_id: str) -> Optional[RoleRecord]:
        return self.roles.get(role_id)

    def list_grants(
        self,
        *,
        resource_kind: Optional[str] = None,
        resource_id: Optional[str] = None,
        scope_kind: Optional[str] = None,
        scope_id: Optional[str] = None,
        active_only: bool = True,
    ) -> List[GrantRecord]:
        active = settings.acl_active(self.config) if self.config is not None else "active"
        out = []
        for g in self.grants.values():
            if resource_kind and g.resource_kind != resource_kind:
                continue
            if resource_id and g.resource_id != resource_id:
                continue
            if scope_kind and g.scope_kind != scope_kind:
                continue
            if scope_id and g.scope_id != scope_id:
                continue
            if active_only and g.row_status != active:
                continue
            out.append(g)
        return out

    def upsert_grant(self, rec: GrantRecord) -> GrantRecord:
        for existing in self.grants.values():
            if (
                existing.scope_kind == rec.scope_kind
                and existing.scope_id == rec.scope_id
                and existing.resource_kind == rec.resource_kind
                and existing.resource_id == rec.resource_id
            ):
                rec.grant_id = existing.grant_id
                break
        if not rec.grant_id:
            rec.grant_id = grant_id()
        self.grants[rec.grant_id] = rec
        return rec

    def get_grant(self, grant_id_value: str) -> Optional[GrantRecord]:
        return self.grants.get(grant_id_value)


class SqlDirectory(Directory):
    """MySQL or SQLite access to hand-built directory tables."""

    def __init__(self, config):
        self.config = config
        self._ok: Optional[bool] = None
        backend = (getattr(getattr(config, "storage", None), "backend", None) or "sqlite").lower()
        self.dialect = "mysql" if backend == "mysql" else "sqlite"

    def available(self) -> bool:
        if self._ok is not None:
            return self._ok
        try:
            with self._connect() as conn:
                self._ok = self._tables_exist(conn)
        except Exception as exc:
            log.warning("directory database unavailable: %s", exc)
            self._ok = False
        if not self._ok:
            log.warning("directory tables missing; ACL and identity degrade")
        return bool(self._ok)

    def _ph(self) -> str:
        return "%s" if self.dialect == "mysql" else "?"

    @contextmanager
    def _connect(self):
        storage = self.config.storage
        if self.dialect == "mysql":
            import os

            try:
                import pymysql
            except ImportError as exc:
                raise RuntimeError("PyMySQL is required for MySQL directory access") from exc
            password = storage.mysql_password
            if not password:
                env_name = storage.mysql_password_env or "SLEUTH_MYSQL_PASSWORD"
                password = os.environ.get(env_name) or os.environ.get("SLEUTH_MYSQL_PASSWORD") or ""
            conn = pymysql.connect(
                host=storage.mysql_host,
                port=int(storage.mysql_port),
                user=storage.mysql_user,
                password=password,
                database=storage.mysql_database,
                charset="utf8mb4",
                autocommit=False,
            )
        else:
            path = storage.sqlite_path or str(default_db_path())
            conn = sqlite3.connect(path)
        try:
            yield conn
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            conn.close()

    def _tables_exist(self, conn) -> bool:
        names = [
            settings.table_org(self.config),
            settings.table_role(self.config),
            settings.table_user(self.config),
            settings.table_grant(self.config),
        ]
        if self.dialect == "sqlite":
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name IN (?,?,?,?)",
                names,
            )
            row = cur.fetchone()
            return bool(row and int(row[0]) >= 4)
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema = %s AND table_name IN (%s,%s,%s,%s)",
            (self.config.storage.mysql_database, *names),
        )
        row = cur.fetchone()
        return bool(row and int(row[0]) >= 4)

    def get_user(self, user_id: str) -> Optional[UserRecord]:
        if not self.available() or not user_id:
            return None
        table = settings.table_user(self.config)
        ph = self._ph()
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                f"SELECT user_id, display_name, role_id, org_id, row_status FROM {table} "
                f"WHERE user_id = {ph}",
                (user_id,),
            )
            row = cur.fetchone()
        if not row:
            return None
        return UserRecord(
            user_id=row[0],
            display_name=row[1],
            role_id=row[2],
            org_id=row[3],
            row_status=row[4],
        )

    def upsert_user(self, rec: UserRecord) -> UserRecord:
        if not self.available():
            raise RuntimeError("directory tables are not available")
        table = settings.table_user(self.config)
        ph = self._ph()
        now = _now_sql()
        if not rec.row_status:
            rec.row_status = settings.acl_active(self.config)
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(f"SELECT user_id FROM {table} WHERE user_id = {ph}", (rec.user_id,))
            exists = cur.fetchone() is not None
            if exists:
                cur.execute(
                    f"UPDATE {table} SET display_name = {ph}, role_id = {ph}, org_id = {ph}, "
                    f"row_status = {ph}, updated_at = {ph} WHERE user_id = {ph}",
                    (rec.display_name, rec.role_id, rec.org_id, rec.row_status, now, rec.user_id),
                )
            else:
                cur.execute(
                    f"INSERT INTO {table} (user_id, display_name, role_id, org_id, row_status, "
                    f"created_at, updated_at) VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph})",
                    (rec.user_id, rec.display_name, rec.role_id, rec.org_id, rec.row_status, now, now),
                )
        return rec

    def get_org(self, org_id: str) -> Optional[OrgRecord]:
        if not self.available() or not org_id:
            return None
        table = settings.table_org(self.config)
        ph = self._ph()
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                f"SELECT org_id, parent_id, org_category, org_name, row_status FROM {table} "
                f"WHERE org_id = {ph}",
                (org_id,),
            )
            row = cur.fetchone()
        if not row:
            return None
        return OrgRecord(
            org_id=row[0],
            parent_id=row[1],
            org_category=row[2],
            org_name=row[3],
            row_status=row[4],
        )

    def get_role(self, role_id: str) -> Optional[RoleRecord]:
        if not self.available() or not role_id:
            return None
        table = settings.table_role(self.config)
        ph = self._ph()
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                f"SELECT role_id, role_name, scenario_list, row_status FROM {table} "
                f"WHERE role_id = {ph}",
                (role_id,),
            )
            row = cur.fetchone()
        if not row:
            return None
        return RoleRecord(
            role_id=row[0],
            role_name=row[1],
            scenario_list=row[2],
            row_status=row[3],
        )

    def list_grants(
        self,
        *,
        resource_kind: Optional[str] = None,
        resource_id: Optional[str] = None,
        scope_kind: Optional[str] = None,
        scope_id: Optional[str] = None,
        active_only: bool = True,
    ) -> List[GrantRecord]:
        if not self.available():
            return []
        table = settings.table_grant(self.config)
        ph = self._ph()
        clauses = ["1=1"]
        params: list = []
        if resource_kind:
            clauses.append(f"resource_kind = {ph}")
            params.append(resource_kind)
        if resource_id:
            clauses.append(f"resource_id = {ph}")
            params.append(resource_id)
        if scope_kind:
            clauses.append(f"scope_kind = {ph}")
            params.append(scope_kind)
        if scope_id:
            clauses.append(f"scope_id = {ph}")
            params.append(scope_id)
        if active_only:
            clauses.append(f"row_status = {ph}")
            params.append(settings.acl_active(self.config))
        sql = (
            f"SELECT grant_id, scope_kind, scope_id, resource_kind, resource_id, "
            f"grant_effect, row_status FROM {table} WHERE {' AND '.join(clauses)}"
        )
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            rows = cur.fetchall()
        return [
            GrantRecord(
                grant_id=r[0],
                scope_kind=r[1],
                scope_id=r[2],
                resource_kind=r[3],
                resource_id=r[4],
                grant_effect=r[5],
                row_status=r[6],
            )
            for r in rows
        ]

    def upsert_grant(self, rec: GrantRecord) -> GrantRecord:
        if not self.available():
            raise RuntimeError("directory tables are not available")
        table = settings.table_grant(self.config)
        ph = self._ph()
        now = _now_sql()
        if not rec.row_status:
            rec.row_status = settings.acl_active(self.config)
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                f"SELECT grant_id FROM {table} WHERE scope_kind = {ph} AND scope_id = {ph} "
                f"AND resource_kind = {ph} AND resource_id = {ph}",
                (rec.scope_kind, rec.scope_id, rec.resource_kind, rec.resource_id),
            )
            row = cur.fetchone()
            if row:
                rec.grant_id = row[0]
                cur.execute(
                    f"UPDATE {table} SET grant_effect = {ph}, row_status = {ph}, "
                    f"updated_at = {ph} WHERE grant_id = {ph}",
                    (rec.grant_effect, rec.row_status, now, rec.grant_id),
                )
            else:
                rec.grant_id = rec.grant_id or grant_id()
                cur.execute(
                    f"INSERT INTO {table} (grant_id, scope_kind, scope_id, resource_kind, "
                    f"resource_id, grant_effect, row_status, created_at, updated_at) "
                    f"VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph})",
                    (
                        rec.grant_id,
                        rec.scope_kind,
                        rec.scope_id,
                        rec.resource_kind,
                        rec.resource_id,
                        rec.grant_effect,
                        rec.row_status,
                        now,
                        now,
                    ),
                )
        return rec

    def get_grant(self, grant_id_value: str) -> Optional[GrantRecord]:
        if not self.available() or not grant_id_value:
            return None
        table = settings.table_grant(self.config)
        ph = self._ph()
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                f"SELECT grant_id, scope_kind, scope_id, resource_kind, resource_id, "
                f"grant_effect, row_status FROM {table} WHERE grant_id = {ph}",
                (grant_id_value,),
            )
            row = cur.fetchone()
        if not row:
            return None
        return GrantRecord(
            grant_id=row[0],
            scope_kind=row[1],
            scope_id=row[2],
            resource_kind=row[3],
            resource_id=row[4],
            grant_effect=row[5],
            row_status=row[6],
        )


_resolved_directories = {}


def directory_for(config) -> Directory:
    injected = getattr(config, "_directory", None)
    if injected is not None:
        return injected
    storage = getattr(config, "storage", None)
    key = (
        getattr(storage, "backend", None),
        getattr(storage, "sqlite_path", None),
        getattr(storage, "mysql_host", None),
        getattr(storage, "mysql_database", None),
        getattr(getattr(config, "acl", None), "table_user", None),
    )
    cached = _resolved_directories.get(key)
    if cached is not None:
        return cached
    directory = SqlDirectory(config)
    resolved: Directory = directory if directory.available() else NullDirectory()
    _resolved_directories[key] = resolved
    try:
        config._directory_resolved = resolved
    except Exception:
        pass
    return resolved
