"""S3-compatible object storage for hosted source media and clip exports."""
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse
from src.core.config import get_settings


def configured() -> bool:
    return bool(get_settings().storage_bucket)


def client():
    import boto3
    cfg = get_settings()
    return boto3.client("s3", endpoint_url=cfg.storage_endpoint_url or None,
                        aws_access_key_id=cfg.storage_access_key or None,
                        aws_secret_access_key=cfg.storage_secret_key or None,
                        region_name=cfg.storage_region or "us-east-1")


def object_key(uri: str) -> str:
    parsed = urlparse(uri)
    if parsed.scheme != "s3" or parsed.netloc != get_settings().storage_bucket:
        raise ValueError("Invalid object storage reference")
    return parsed.path.lstrip("/")


def uri_for(key: str) -> str:
    return f"s3://{get_settings().storage_bucket}/{key.lstrip('/')}"


def presign_upload(key: str, content_type: str, size_bytes: int) -> str:
    return client().generate_presigned_url("put_object", Params={"Bucket": get_settings().storage_bucket,
        "Key": key, "ContentType": content_type, "ContentLength": size_bytes}, ExpiresIn=900)


def download(uri: str, path: Path) -> None:
    client().download_file(get_settings().storage_bucket, object_key(uri), str(path))


def upload_file(path: Path, key: str, content_type: str) -> str:
    client().upload_file(str(path), get_settings().storage_bucket, key, ExtraArgs={"ContentType": content_type})
    return uri_for(key)


def upload_bytes(contents: bytes, key: str, content_type: str) -> str:
    client().put_object(Bucket=get_settings().storage_bucket, Key=key, Body=contents, ContentType=content_type)
    return uri_for(key)


def delete_object(uri: str) -> None:
    client().delete_object(Bucket=get_settings().storage_bucket, Key=object_key(uri))


def presign_download(uri: str, expires: int = 600) -> str:
    return client().generate_presigned_url("get_object", Params={"Bucket": get_settings().storage_bucket,
        "Key": object_key(uri)}, ExpiresIn=expires)


def object_size(key: str) -> int:
    return int(client().head_object(Bucket=get_settings().storage_bucket, Key=key)["ContentLength"])
