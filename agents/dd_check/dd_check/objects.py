"""History object store (Tencent COS / S3). Do not import sleuth."""
from __future__ import annotations

from typing import Any, Dict, Optional

from .bizerror import APPError, BizErrorCode
from .config import Settings

WORD_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)


class ObjectStoreError(RuntimeError):
    """COS operation failed before mapping to APPError."""


def history_object_key(prefix: str, report_id: str, check_id: str) -> str:
    base = (prefix or "").strip().strip("/")
    rid = _safe_segment(report_id)
    cid = _safe_segment(check_id)
    tail = f"dd_check/{rid}/{cid}.docx"
    return f"{base}/{tail}" if base else tail


def _safe_segment(value: str) -> str:
    text = (value or "").strip().replace("\\", "/").replace("..", "")
    text = text.replace("/", "_")
    return text or "unknown"


class BotoObjectStore:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = None

    def _boto(self):
        if self._client is not None:
            return self._client
        try:
            import boto3
            from botocore.config import Config as BotoConfig
        except ImportError as exc:
            raise ObjectStoreError("boto3 is required: pip install boto3") from exc
        kwargs: Dict[str, Any] = {
            "aws_access_key_id": self._settings.cos_secret_id,
            "aws_secret_access_key": self._settings.cos_secret_key,
        }
        if self._settings.cos_region:
            kwargs["region_name"] = self._settings.cos_region
        if self._settings.cos_endpoint:
            kwargs["endpoint_url"] = self._settings.cos_endpoint
        kwargs["config"] = BotoConfig(
            signature_version="s3v4",
            s3={"addressing_style": "virtual"},
        )
        self._client = boto3.client("s3", **kwargs)
        return self._client

    def put_bytes(self, *, key: str, data: bytes, content_type: str) -> None:
        extra: Dict[str, Any] = {}
        if content_type:
            extra["ContentType"] = content_type
        try:
            self._boto().put_object(
                Bucket=self._settings.cos_bucket,
                Key=key,
                Body=data,
                **extra,
            )
        except Exception as exc:
            raise APPError.of(BizErrorCode.UPLOAD_S3_FAIL, key) from exc

    def get_bytes(self, key: str, max_bytes: int = 0) -> bytes:
        try:
            resp = self._boto().get_object(
                Bucket=self._settings.cos_bucket,
                Key=key,
            )
        except Exception as exc:
            raise APPError.of(BizErrorCode.DOWNLOAD_S3_FAIL, key) from exc
        body = resp.get("Body")
        if body is None:
            return b""
        try:
            data = body.read()
        finally:
            close = getattr(body, "close", None)
            if callable(close):
                close()
        cap = int(max_bytes or 0)
        if cap and len(data) > cap:
            raise APPError.of(BizErrorCode.DOWNLOAD_FAIL, f">{cap}")
        return data

    def delete_object(self, key: str) -> None:
        try:
            self._boto().delete_object(
                Bucket=self._settings.cos_bucket,
                Key=key,
            )
        except Exception as exc:
            raise APPError.of(BizErrorCode.DELETE_S3_FAIL, key) from exc


def build_object_store(settings: Settings) -> Optional[BotoObjectStore]:
    if not settings.output_enabled:
        return None
    return BotoObjectStore(settings)
