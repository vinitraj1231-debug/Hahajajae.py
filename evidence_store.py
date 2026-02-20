
"""
ShieldBot — Evidence Store
Immutable media + metadata archival to MinIO/S3.
Every file is sha256-hashed; hash stored in DB for tamper-proofing.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import zipfile
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import aioboto3
from botocore.exceptions import ClientError

from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class EvidenceStore:
    def __init__(self):
        self._session = aioboto3.Session()

    def _client(self):
        return self._session.client(
            "s3",
            endpoint_url=f"{'https' if settings.minio_secure else 'http'}://{settings.minio_endpoint}",
            aws_access_key_id=settings.minio_access_key,
            aws_secret_access_key=settings.minio_secret_key,
        )

    async def ensure_bucket(self) -> None:
        async with self._client() as s3:
            try:
                await s3.head_bucket(Bucket=settings.minio_bucket)
            except ClientError:
                await s3.create_bucket(Bucket=settings.minio_bucket)
                # Set bucket versioning for immutability
                await s3.put_bucket_versioning(
                    Bucket=settings.minio_bucket,
                    VersioningConfiguration={"Status": "Enabled"},
                )
                logger.info(f"Created evidence bucket: {settings.minio_bucket}")

    @staticmethod
    def _sha256(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def _build_key(self, namespace: str, identifier: str, filename: str) -> str:
        today = datetime.now(timezone.utc).strftime("%Y/%m/%d")
        return f"{namespace}/{today}/{identifier}/{filename}"

    async def store_message_evidence(
        self,
        group_id: int,
        incident_id: int,
        message_data: Dict[str, Any],
        media_bytes: Optional[bytes] = None,
        media_filename: Optional[str] = None,
    ) -> Tuple[str, str, Optional[str], Optional[str]]:
        """
        Store message JSON (and optional media) in MinIO.
        Returns (json_key, json_hash, media_key, media_hash).
        """
        namespace = f"groups/{group_id}/incidents/{incident_id}"

        # Store JSON metadata
        json_bytes = json.dumps(message_data, default=str, ensure_ascii=False).encode("utf-8")
        json_hash = self._sha256(json_bytes)
        json_key = self._build_key(namespace, json_hash[:8], "message.json")

        async with self._client() as s3:
            await s3.put_object(
                Bucket=settings.minio_bucket,
                Key=json_key,
                Body=json_bytes,
                ContentType="application/json",
                Metadata={
                    "sha256": json_hash,
                    "group_id": str(group_id),
                    "incident_id": str(incident_id),
                    "archived_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            logger.debug(f"Stored evidence JSON: {json_key}")

            media_key = None
            media_hash = None
            if media_bytes and media_filename:
                media_hash = self._sha256(media_bytes)
                media_key = self._build_key(namespace, media_hash[:8], media_filename)
                await s3.put_object(
                    Bucket=settings.minio_bucket,
                    Key=media_key,
                    Body=media_bytes,
                    Metadata={
                        "sha256": media_hash,
                        "original_filename": media_filename,
                        "group_id": str(group_id),
                        "incident_id": str(incident_id),
                        "archived_at": datetime.now(timezone.utc).isoformat(),
                    },
                )
                logger.debug(f"Stored evidence media: {media_key}")

        return json_key, json_hash, media_key, media_hash

    async def export_incident(
        self,
        incident_id: int,
        group_id: int,
        incident_data: Dict[str, Any],
        evidence_keys: List[str],
        admin_notes: Optional[str] = None,
    ) -> str:
        """
        Create a ZIP archive of all incident evidence.
        Returns the MinIO key for the exported ZIP.
        """
        zip_buffer = io.BytesIO()

        async with self._client() as s3:
            with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
                # Incident metadata
                manifest = {
                    "incident_id": incident_id,
                    "group_id": group_id,
                    "exported_at": datetime.now(timezone.utc).isoformat(),
                    "admin_notes": admin_notes,
                    "incident_data": incident_data,
                    "evidence_keys": evidence_keys,
                }
                zf.writestr("manifest.json", json.dumps(manifest, default=str, indent=2))

                # Download and include each evidence file
                for key in evidence_keys:
                    try:
                        resp = await s3.get_object(Bucket=settings.minio_bucket, Key=key)
                        body = await resp["Body"].read()
                        filename = key.split("/")[-1]
                        zf.writestr(f"evidence/{filename}", body)
                    except Exception as e:
                        logger.error(f"Failed to fetch evidence {key}: {e}")

        zip_bytes = zip_buffer.getvalue()
        zip_hash = self._sha256(zip_bytes)
        export_key = f"exports/incident_{incident_id}_{zip_hash[:8]}.zip"

        async with self._client() as s3:
            await s3.put_object(
                Bucket=settings.minio_bucket,
                Key=export_key,
                Body=zip_bytes,
                ContentType="application/zip",
                Metadata={
                    "sha256": zip_hash,
                    "incident_id": str(incident_id),
                    "exported_at": datetime.now(timezone.utc).isoformat(),
                },
            )

        logger.info(f"Incident {incident_id} exported to {export_key}")
        return export_key

    async def get_presigned_url(self, key: str, expires_in: int = 3600) -> str:
        """Generate a time-limited pre-signed URL for evidence download."""
        async with self._client() as s3:
            url = await s3.generate_presigned_url(
                "get_object",
                Params={"Bucket": settings.minio_bucket, "Key": key},
                ExpiresIn=expires_in,
            )
        return url


_evidence_store: Optional[EvidenceStore] = None


def get_evidence_store() -> EvidenceStore:
    global _evidence_store
    if _evidence_store is None:
        _evidence_store = EvidenceStore()
    return _evidence_store
