"""Optional COS/S3-compatible object stream reader."""
from __future__ import annotations

from typing import Iterable

from ..config import Settings


class CosObjectStore:
    def __init__(self, settings: Settings):
        self.settings = settings

    def configured(self) -> bool:
        s = self.settings
        return bool(s.cos_secret_id and s.cos_secret_key and s.cos_bucket and s.cos_region)

    def open_stream(self, location_path: str) -> Iterable[bytes]:
        if not self.configured():
            raise RuntimeError("COS not configured")
        try:
            import boto3
        except ImportError as exc:
            raise RuntimeError("boto3 required: pip install dd-check[cos]") from exc

        s = self.settings
        key = location_path
        prefix = s.cos_path_prefix or ""
        if prefix and not key.startswith(prefix):
            # if path is absolute URL-like, keep as-is; else join prefix
            if "://" not in key:
                key = prefix.rstrip("/") + "/" + key.lstrip("/")

        kwargs = {
            "aws_access_key_id": s.cos_secret_id,
            "aws_secret_access_key": s.cos_secret_key,
            "region_name": s.cos_region,
        }
        if s.cos_endpoint:
            kwargs["endpoint_url"] = s.cos_endpoint
        client = boto3.client("s3", **kwargs)
        resp = client.get_object(Bucket=s.cos_bucket, Key=key)
        body = resp["Body"]
        while True:
            chunk = body.read(1024 * 256)
            if not chunk:
                break
            yield chunk
