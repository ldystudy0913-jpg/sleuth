"""HTTP /v1/checks on the dd_check MCP process."""
from __future__ import annotations
import base64
from typing import Any, Dict
from .bizerror import APPError, BizErrorCode
from .config import Settings
from .envelope import json_app, json_ok, raise_code
from .history import HistoryStore, build_history_store
from .objects import WORD_CONTENT_TYPE, BotoObjectStore, build_object_store
from .scenarios import public_catalog
def _store(settings: Settings) -> HistoryStore:
    store = build_history_store(settings)
    if store is None:
        raise_code(BizErrorCode.ABNORMAL_OPERATION, "history MySQL is not configured")
    return store
def _objects(settings: Settings) -> BotoObjectStore:
    obj = build_object_store(settings)
    if obj is None:
        raise_code(BizErrorCode.ABNORMAL_OPERATION, "history COS is not configured")
    return obj
def _dt(value: Any) -> str:
    if value is None:
        return ""
    text = str(value)
    return text.replace("T", " ")
def _file_entry(row: Dict[str, Any], raw: bytes) -> Dict[str, Any]:
    return {
        "filename": str(row.get("filename") or "check.docx"),
        "content_type": str(row.get("content_type") or WORD_CONTENT_TYPE),
        "content_base64": base64.b64encode(raw).decode("ascii"),
    }
def _meta(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "check_id": str(row.get("check_id") or ""),
        "report_id": str(row.get("report_id") or ""),
        "scenario": str(row.get("scenario") or ""),
        "scenario_title": str(row.get("scenario_title") or ""),
        "filename": str(row.get("filename") or ""),
        "created_at": _dt(row.get("created_at")),
        "updated_at": _dt(row.get("updated_at")),
    }
def list_checks_payload(
    settings: Settings,
    report_id: str,
    *,
    include_files: bool = True,
) -> Dict[str, Any]:
    rid = (report_id or "").strip()
    if not rid:
        raise_code(BizErrorCode.REQUEST_VALIDATION_FAILED, "report_id")
    store = _store(settings)
    rows = store.list_by_report(rid)
    checks = []
    obj = _objects(settings) if include_files else None
    for row in rows:
        item = _meta(row)
        if include_files and obj is not None:
            key = str(row.get("object_key") or "")
            raw = obj.get_bytes(key) if key else b""
            item["files"] = [_file_entry(row, raw)] if raw else []
        checks.append(item)
    return {"checks": checks}
def get_check_file_payload(settings: Settings, check_id: str) -> Dict[str, Any]:
    cid = (check_id or "").strip()
    if not cid:
        raise_code(BizErrorCode.REQUEST_VALIDATION_FAILED, "check_id")
    row = _store(settings).get(cid)
    if row is None:
        raise_code(BizErrorCode.ABNORMAL_OPERATION, f"check {cid} not found")
    key = str(row.get("object_key") or "")
    raw = _objects(settings).get_bytes(key) if key else b""
    item = _meta(row)
    item["files"] = [_file_entry(row, raw)] if raw else []
    return item
def delete_check_payload(settings: Settings, check_id: str) -> Dict[str, Any]:
    cid = (check_id or "").strip()
    if not cid:
        raise_code(BizErrorCode.REQUEST_VALIDATION_FAILED, "check_id")
    row = _store(settings).delete(cid)
    if row is None:
        raise_code(BizErrorCode.ABNORMAL_OPERATION, f"check {cid} not found")
    key = str(row.get("object_key") or "").strip()
    if key:
        try:
            _objects(settings).delete_object(key)
        except APPError:
            pass
    return {"deleted": True, "check_id": cid}
def scenarios_payload(settings: Settings) -> Dict[str, Any]:
    return {"scenarios": public_catalog(settings.config_dir)}
def _query(request: Any, name: str) -> str:
    params = getattr(request, "query_params", None) or {}
    try:
        return str(params.get(name) or "").strip()
    except Exception:
        return ""
def _path_id(request: Any, name: str) -> str:
    params = getattr(request, "path_params", None) or {}
    try:
        return str(params.get(name) or "").strip()
    except Exception:
        return ""
def _wrap(fn):
    def handler(request: Any):
        try:
            return json_ok(fn(request))
        except APPError as exc:
            return json_app(exc)
    async def async_handler(request: Any):
        return handler(request)
    return async_handler
def register_check_routes(server: Any, settings: Settings) -> None:
    register = getattr(server, "custom_route", None)
    if not callable(register):
        return
    @register("/v1/checks/scenarios", methods=["GET"])
    @_wrap
    def http_scenarios(_request: Any) -> Dict[str, Any]:
        return scenarios_payload(settings)
    @register("/v1/checks", methods=["GET"])
    @_wrap
    def http_list(request: Any) -> Dict[str, Any]:
        return list_checks_payload(settings, _query(request, "report_id"), include_files=True)
    @register("/v1/checks/{check_id}/file", methods=["GET"])
    @_wrap
    def http_file(request: Any) -> Dict[str, Any]:
        return get_check_file_payload(settings, _path_id(request, "check_id"))
    @register("/v1/checks/{check_id}", methods=["DELETE"])
    @_wrap
    def http_delete(request: Any) -> Dict[str, Any]:
        return delete_check_payload(settings, _path_id(request, "check_id"))
