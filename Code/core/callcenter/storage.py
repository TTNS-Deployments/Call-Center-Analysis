"""Blob storage abstraction: local folder or Azure Blob Storage / ADLS Gen2 (and Azurite)."""

from __future__ import annotations

import json
import shutil
import uuid
from abc import ABC, abstractmethod
from pathlib import Path

from .config import Settings


class BlobStore(ABC):
    @abstractmethod
    def put_bytes(self, container: str, name: str, data: bytes, content_type: str | None = None) -> None: ...

    @abstractmethod
    def get_bytes(self, container: str, name: str) -> bytes: ...

    @abstractmethod
    def exists(self, container: str, name: str) -> bool: ...

    @abstractmethod
    def list(self, container: str, prefix: str = "") -> list[str]: ...

    @abstractmethod
    def size(self, container: str, name: str) -> int: ...

    @abstractmethod
    def get_range(self, container: str, name: str, start: int, length: int) -> bytes: ...

    def put_json(self, container: str, name: str, obj) -> None:
        self.put_bytes(container, name, json.dumps(obj, indent=2, default=str).encode(), "application/json")

    def get_json(self, container: str, name: str):
        return json.loads(self.get_bytes(container, name))

    def get_json_or_none(self, container: str, name: str):
        return self.get_json(container, name) if self.exists(container, name) else None


class LocalBlobStore(BlobStore):
    """Containers are folders under ``root``."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, container: str, name: str) -> Path:
        path = (self.root / container / name).resolve()
        base = (self.root / container).resolve()
        if base not in path.parents and path != base:
            raise ValueError(f"Invalid blob name: {name}")
        return path

    def put_bytes(self, container, name, data, content_type=None):
        path = self._path(container, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Unique temp name: two writers of the same blob (e.g. a double retry) must not share it.
        tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex[:8]}.tmp")
        tmp.write_bytes(data)
        tmp.replace(path)

    def get_bytes(self, container, name):
        path = self._path(container, name)
        if not path.is_file():
            raise FileNotFoundError(f"{container}/{name}")
        return path.read_bytes()

    def exists(self, container, name):
        return self._path(container, name).is_file()

    def list(self, container, prefix=""):
        base = self.root / container
        if not base.is_dir():
            return []
        names = [p.relative_to(base).as_posix() for p in base.rglob("*") if p.is_file() and not p.name.endswith(".tmp")]
        return sorted(n for n in names if n.startswith(prefix))

    def size(self, container, name):
        return self._path(container, name).stat().st_size

    def get_range(self, container, name, start, length):
        with self._path(container, name).open("rb") as fh:
            fh.seek(start)
            return fh.read(length)

    def clear(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)
        self.root.mkdir(parents=True, exist_ok=True)


class AzureBlobStore(BlobStore):
    def __init__(self, settings: Settings):
        from azure.storage.blob import BlobServiceClient

        if settings.storage_connection_string:
            self.client = BlobServiceClient.from_connection_string(settings.storage_connection_string)
        elif settings.storage_account_url:
            from .azure_auth import get_credential

            self.client = BlobServiceClient(settings.storage_account_url, credential=get_credential())
        else:
            raise RuntimeError("Set AZURE_STORAGE_CONNECTION_STRING or AZURE_STORAGE_ACCOUNT_URL")
        self._ensured: set[str] = set()

    def _container(self, container: str):
        client = self.client.get_container_client(container)
        if container not in self._ensured:
            from azure.core.exceptions import ResourceExistsError

            try:
                client.create_container()
            except ResourceExistsError:
                pass
            self._ensured.add(container)
        return client

    def put_bytes(self, container, name, data, content_type=None):
        from azure.storage.blob import ContentSettings

        settings = ContentSettings(content_type=content_type) if content_type else None
        self._container(container).upload_blob(name, data, overwrite=True, content_settings=settings)

    def get_bytes(self, container, name):
        from azure.core.exceptions import ResourceNotFoundError

        try:
            return self._container(container).download_blob(name).readall()
        except ResourceNotFoundError as exc:
            raise FileNotFoundError(f"{container}/{name}") from exc

    def exists(self, container, name):
        return self._container(container).get_blob_client(name).exists()

    def list(self, container, prefix=""):
        return sorted(b.name for b in self._container(container).list_blobs(name_starts_with=prefix or None))

    def size(self, container, name):
        return self._container(container).get_blob_client(name).get_blob_properties().size

    def get_range(self, container, name, start, length):
        return self._container(container).download_blob(name, offset=start, length=length).readall()
