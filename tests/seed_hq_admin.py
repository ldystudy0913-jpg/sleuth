"""One-shot insert: HQ org, hq_admin role, wildcard grants, three superusers.

Fill MYSQL below, then from the repo root:

    python tests/seed_hq_admin.py

Needs PyMySQL. Re-run is safe: existing org/role/grant/user rows are skipped.
This is not part of the product; unittest discovery ignores this filename.
"""
from __future__ import annotations

import sys

# --- fill these in, then run once ---
MYSQL = {
    "host": "",
    "port": 3306,
    "user": "",
    "password": "",
    "database": "",
}

ORG_ID = "HQ"
ROLE_ID = "hq_admin"
USER_IDS = ("80362611", "80362612", "80451232")


def _require_config() -> None:
    missing = [k for k in ("host", "user", "database") if not str(MYSQL.get(k) or "").strip()]
    if missing:
        sys.exit("Fill MYSQL in this file first (empty: %s)." % ", ".join(missing))


def _skip_or_insert(cur, sql: str, params: tuple, *, exists_sql: str, exists_params: tuple) -> str:
    cur.execute(exists_sql, exists_params)
    if cur.fetchone():
        return "skip"
    cur.execute(sql, params)
    return "insert"


def main() -> None:
    _require_config()
    try:
        import pymysql
    except ImportError:
        sys.exit("Install PyMySQL first: python -m pip install PyMySQL")

    conn = pymysql.connect(
        host=MYSQL["host"].strip(),
        port=int(MYSQL["port"] or 3306),
        user=MYSQL["user"].strip(),
        password=str(MYSQL.get("password") or ""),
        database=MYSQL["database"].strip(),
        charset="utf8mb4",
        autocommit=False,
    )
    results = []
    try:
        with conn.cursor() as cur:
            results.append(
                (
                    "org " + ORG_ID,
                    _skip_or_insert(
                        cur,
                        "INSERT INTO mem_org "
                        "(org_id, parent_id, org_category, org_name, row_status, created_at, updated_at) "
                        "VALUES (%s, NULL, 'head', '总行', 'active', NOW(), NOW())",
                        (ORG_ID,),
                        exists_sql="SELECT 1 FROM mem_org WHERE org_id = %s",
                        exists_params=(ORG_ID,),
                    ),
                )
            )
            results.append(
                (
                    "role " + ROLE_ID,
                    _skip_or_insert(
                        cur,
                        "INSERT INTO mem_role "
                        "(role_id, role_name, scenario_list, row_status, created_at, updated_at) "
                        "VALUES (%s, '总行平台管理员', NULL, 'active', NOW(), NOW())",
                        (ROLE_ID,),
                        exists_sql="SELECT 1 FROM mem_role WHERE role_id = %s",
                        exists_params=(ROLE_ID,),
                    ),
                )
            )
            for grant_id, kind in (
                ("grant_hq_admin_agent_all", "agent"),
                ("grant_hq_admin_skill_all", "skill"),
            ):
                results.append(
                    (
                        "grant %s %s=*" % (grant_id, kind),
                        _skip_or_insert(
                            cur,
                            "INSERT INTO mem_grant "
                            "(grant_id, scope_kind, scope_id, resource_kind, resource_id, "
                            "grant_effect, row_status, created_at, updated_at) "
                            "VALUES (%s, 'role', %s, %s, '*', 'allow', 'active', NOW(), NOW())",
                            (grant_id, ROLE_ID, kind),
                            exists_sql=(
                                "SELECT 1 FROM mem_grant WHERE grant_id = %s OR "
                                "(scope_kind = 'role' AND scope_id = %s AND "
                                "resource_kind = %s AND resource_id = '*')"
                            ),
                            exists_params=(grant_id, ROLE_ID, kind),
                        ),
                    )
                )
            for user_id in USER_IDS:
                results.append(
                    (
                        "user " + user_id,
                        _skip_or_insert(
                            cur,
                            "INSERT INTO mem_user "
                            "(user_id, display_name, role_id, org_id, row_status, created_at, updated_at) "
                            "VALUES (%s, NULL, %s, %s, 'active', NOW(), NOW())",
                            (user_id, ROLE_ID, ORG_ID),
                            exists_sql="SELECT 1 FROM mem_user WHERE user_id = %s",
                            exists_params=(user_id,),
                        ),
                    )
                )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    for label, action in results:
        print("%s\t%s" % (action, label))


if __name__ == "__main__":
    main()
