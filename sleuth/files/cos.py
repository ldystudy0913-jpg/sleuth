"""Tencent COS / S3-compatible object store for the session file mailbox.

Credentials and endpoint come from ``Config.cos`` (``SLEUTH_COS_*``). Nothing
COS-specific is hardcoded here besides the boto3 S3 API.
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Protocol, runtime_checkable

from ..config import Config, CosConfig


class CosNotConfigured(RuntimeError):
    """Mailbox COS settings are missing."""


class CosError(RuntimeError):
    """Object-store operation failed."""


@runtime_checkable
class ObjectStore(Protocol):
    def presign_put(self, *, key: str, mime: str, expires: int) -> str: ...
    def presign_get(self, *, key: str, mime: str = "", expires: int) -> str: ...
    def head(self, key: str) -> Optional[Dict[str, Any]]: ...
    def put_bytes(self, *, key: str, data: bytes, mime: str) -> None: ...
    def get_bytes(self, key: str, max_bytes: int = 0) -> bytes: ...


class BotoCosStore:
    """S3-compatible client (Tencent COS via boto3)."""

    def __init__(self, cos: CosConfig):
        self._cos = cos
        self._client = None

    def _boto(self):
        if self._client is not None:
            return self._client
        try:
            import boto3
            from botocore.config import Config as BotoConfig
        except ImportError as exc:
            raise CosError(
                "boto3 is required for COS: pip install 'sleuth[s3]'"
            ) from exc
        kwargs: Dict[str, Any] = {
            "aws_access_key_id": self._cos.secret_id,
            "aws_secret_access_key": self._cos.secret_key,
        }
        if self._cos.region:
            kwargs["region_name"] = self._cos.region
        if self._cos.endpoint:
            kwargs["endpoint_url"] = self._cos.endpoint
        sig = (self._cos.signature_version or "").strip() or "s3v4"
        addressing = (self._cos.addressing_style or "").strip() or "virtual"
        kwargs["config"] = BotoConfig(
            signature_version=sig,
            s3={"addressing_style": addressing},
        )
        self._client = boto3.client("s3", **kwargs)
        return self._client

    def presign_put(self, *, key: str, mime: str, expires: int) -> str:
        params: Dict[str, Any] = {
            "Bucket": self._cos.bucket,
            "Key": key,
        }
        if mime:
            params["ContentType"] = mime
        try:
            return self._boto().generate_presigned_url(
                "put_object",
                Params=params,
                ExpiresIn=int(expires),
                HttpMethod="PUT",
            )
        except Exception as exc:
            raise CosError(f"presign PUT failed: {exc}") from exc

    def presign_get(self, *, key: str, mime: str = "", expires: int) -> str:
        params: Dict[str, Any] = {
            "Bucket": self._cos.bucket,
            "Key": key,
        }
        try:
            return self._boto().generate_presigned_url(
                "get_object",
                Params=params,
                ExpiresIn=int(expires),
                HttpMethod="GET",
            )
        except Exception as exc:
            raise CosError(f"presign GET failed: {exc}") from exc

    def head(self, key: str) -> Optional[Dict[str, Any]]:
        try:
            resp = self._boto().head_object(Bucket=self._cos.bucket, Key=key)
        except Exception:
            return None
        size = resp.get("ContentLength")
        try:
            size_i = int(size) if size is not None else 0
        except (TypeError, ValueError):
            size_i = 0
        return {
            "size": size_i,
            "mime": str(resp.get("ContentType") or ""),
        }

    def put_bytes(self, *, key: str, data: bytes, mime: str) -> None:
        extra: Dict[str, Any] = {}
        if mime:
            extra["ContentType"] = mime
        try:
            self._boto().put_object(
                Bucket=self._cos.bucket,
                Key=key,
                Body=data,
                **extra,
            )
        except Exception as exc:
            raise CosError(f"put_object failed: {exc}") from exc

    def get_bytes(self, key: str, max_bytes: int = 0) -> bytes:
        try:
            resp = self._boto().get_object(Bucket=self._cos.bucket, Key=key)
        except Exception as exc:
            raise CosError(f"get_object failed: {exc}") from exc
        body = resp.get("Body")
        if body is None:
            return b""
        cap = int(max_bytes or 0)
        chunks = bytearray()
        try:
            while True:
                chunk = body.read(256 * 1024)
                if not chunk:
                    break
                chunks.extend(chunk)
                if cap and len(chunks) > cap:
                    raise CosError(f"object too large: > {cap}")
        finally:
            close = getattr(body, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
        return bytes(chunks)


class MemoryObjectStore:
    """In-process store for tests. Not a production backend."""

    def __init__(self) -> None:
        self.objects: Dict[str, Dict[str, Any]] = {}

    def presign_put(self, *, key: str, mime: str, expires: int) -> str:
        return f"https://memory.invalid/put/{key}?expires={int(expires)}"

    def presign_get(self, *, key: str, mime: str = "", expires: int) -> str:
        return f"https://memory.invalid/get/{key}?expires={int(expires)}"

    def head(self, key: str) -> Optional[Dict[str, Any]]:
        obj = self.objects.get(key)
        if obj is None:
            return None
        data = obj.get("data") or b""
        return {"size": len(data), "mime": str(obj.get("mime") or "")}

    def put_bytes(self, *, key: str, data: bytes, mime: str) -> None:
        self.objects[key] = {"data": bytes(data), "mime": mime or ""}

    def get_bytes(self, key: str, max_bytes: int = 0) -> bytes:
        obj = self.objects.get(key)
        if obj is None:
            raise CosError(f"object not found: {key}")
        data = bytes(obj.get("data") or b"")
        cap = int(max_bytes or 0)
        if cap and len(data) > cap:
            raise CosError(f"object too large: {len(data)} > {cap}")
        return data


def object_store_from_config(config: Config) -> ObjectStore:
    cos = getattr(config, "cos", None)
    if cos is None or not cos.configured():
        raise CosNotConfigured(
            "object store is not configured; set AWS_ACCESS_KEY_ID / "
            "AWS_SECRET_ACCESS_KEY, SLEUTH_S3_ENDPOINT (or AWS_DEFAULT_REGION), "
            "and SLEUTH_SKILLS_S3 (bucket in the s3:// URI)"
        )
    return BotoCosStore(cos)
