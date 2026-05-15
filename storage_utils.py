#!/usr/bin/env python3
"""
S3-compatible storage helpers for Railway buckets or any S3 provider.
"""
import os
from typing import Optional, Dict, Any


def get_storage_config() -> Optional[Dict[str, Any]]:
    endpoint = os.environ.get("AWS_ENDPOINT_URL")
    bucket = os.environ.get("AWS_S3_BUCKET_NAME")
    access_key = os.environ.get("AWS_ACCESS_KEY_ID")
    secret_key = os.environ.get("AWS_SECRET_ACCESS_KEY")
    region = os.environ.get("AWS_DEFAULT_REGION") or "auto"

    if not all([endpoint, bucket, access_key, secret_key]):
        return None

    import boto3

    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=region
    )
    return {"client": client, "bucket": bucket}


def upload_file(storage: Dict[str, Any], local_path: str, key: str) -> None:
    storage["client"].upload_file(local_path, storage["bucket"], key)


def download_file(storage: Dict[str, Any], key: str, local_path: str) -> None:
    storage["client"].download_file(storage["bucket"], key, local_path)


def delete_file(storage: Dict[str, Any], key: str) -> None:
    storage["client"].delete_object(Bucket=storage["bucket"], Key=key)


def copy_file(storage: Dict[str, Any], source_key: str, dest_key: str) -> None:
    storage["client"].copy_object(
        Bucket=storage["bucket"],
        CopySource={"Bucket": storage["bucket"], "Key": source_key},
        Key=dest_key,
    )


def generate_presigned_url(
    storage: Dict[str, Any],
    key: str,
    expires_seconds: int = 3600
) -> str:
    return storage["client"].generate_presigned_url(
        "get_object",
        Params={"Bucket": storage["bucket"], "Key": key},
        ExpiresIn=expires_seconds
    )
