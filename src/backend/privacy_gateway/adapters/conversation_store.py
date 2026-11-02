from __future__ import annotations

import base64
import ctypes
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import struct
import sys
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


class ConversationStoreError(RuntimeError):
    pass


class KeyUnavailableError(ConversationStoreError):
    pass


class CorruptStoreError(ConversationStoreError):
    pass


class RecordNotFoundError(ConversationStoreError):
    pass


class VersionConflictError(ConversationStoreError):
    pass


class StoreBusyError(ConversationStoreError):
    pass


type JSONValue = None | bool | int | float | str | list["JSONValue"] | dict[str, "JSONValue"]
type JSONRecord = dict[str, JSONValue]


class KeyProvider(Protocol):
    def load_or_create(self, key_path: Path, *, create: bool) -> bytes:
        pass


class WindowsDPAPIKeyProvider:
    _MAGIC = b"CENSITIVE-DPAPI-V1\x00"
    _CRYPTPROTECT_UI_FORBIDDEN = 0x1

    def load_or_create(self, key_path: Path, *, create: bool) -> bytes:
        if sys.platform != "win32":
            raise KeyUnavailableError("KEY_PROTECTION_UNAVAILABLE")
        try:
            if key_path.exists():
                protected = key_path.read_bytes()
                if not protected.startswith(self._MAGIC):
                    raise KeyUnavailableError("KEY_FILE_INVALID")
                return self._unprotect(protected[len(self._MAGIC) :])
            if not create:
                raise KeyUnavailableError("KEY_MISSING")
            key = secrets.token_bytes(32)
            protected = self._protect(key)
            key_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                with key_path.open("xb") as handle:
                    handle.write(self._MAGIC + protected)
                    handle.flush()
                    os.fsync(handle.fileno())
            except FileExistsError:
                return self.load_or_create(key_path, create=False)
            return key
        except KeyUnavailableError:
            raise
        except (OSError, ValueError, TypeError):
            raise KeyUnavailableError("KEY_UNAVAILABLE") from None

    @classmethod
    def _protect(cls, value: bytes) -> bytes:
        return _dpapi_call(value, protect=True)

    @classmethod
    def _unprotect(cls, value: bytes) -> bytes:
        return _dpapi_call(value, protect=False)


def _dpapi_call(value: bytes, *, protect: bool) -> bytes:
    if sys.platform != "win32":
        raise KeyUnavailableError("KEY_PROTECTION_UNAVAILABLE")
    class DataBlob(ctypes.Structure):
        _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]

    source = (ctypes.c_ubyte * len(value)).from_buffer_copy(value)
    input_blob = DataBlob(len(value), source)
    output_blob = DataBlob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    function_name = "CryptProtectData" if protect else "CryptUnprotectData"
    function = getattr(crypt32, function_name)
    if protect:
        function.argtypes = [
            ctypes.POINTER(DataBlob),
            ctypes.c_wchar_p,
            ctypes.POINTER(DataBlob),
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.POINTER(DataBlob),
        ]
        description: object = "Censitive conversation store key"
    else:
        function.argtypes = [
            ctypes.POINTER(DataBlob),
            ctypes.POINTER(ctypes.c_wchar_p),
            ctypes.POINTER(DataBlob),
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.POINTER(DataBlob),
        ]
        description = None
    function.restype = ctypes.c_int
    if not function(
        ctypes.byref(input_blob),
        description,
        None,
        None,
        None,
        WindowsDPAPIKeyProvider._CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(output_blob),
    ):
        raise KeyUnavailableError("KEY_UNAVAILABLE")
    try:
        if not output_blob.pbData or output_blob.cbData <= 0:
            raise KeyUnavailableError("KEY_UNAVAILABLE")
        result = ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        if output_blob.pbData:
            kernel32.LocalFree.argtypes = [ctypes.c_void_p]
            kernel32.LocalFree.restype = ctypes.c_void_p
            kernel32.LocalFree(ctypes.cast(output_blob.pbData, ctypes.c_void_p))
    if not protect and len(result) != 32:
        raise KeyUnavailableError("KEY_LENGTH_INVALID")
    return result


@dataclass(frozen=True)
class StoredRecord:
    namespace: str
    record_id: str
    version: int
    data: JSONRecord


class EncryptedConversationStore:
    _FORMAT = 1
    _KEY_SIZE = 32
    _MAX_RECORD_BYTES = 4 * 1024 * 1024
    _MAX_LOCATOR_BYTES = 1024
    _STORE_AAD = b"censitive/encrypted-conversation-store/state/v1"
    _RECORD_AAD_PREFIX = b"censitive/encrypted-conversation-store/record/v1\x00"
    _INDEX_INFO = b"censitive/encrypted-conversation-store/index/v1"
    _AEAD_INFO = b"censitive/encrypted-conversation-store/aead/v1"

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        key: bytes | bytearray | memoryview | None = None,
        key_provider: KeyProvider | None = None,
        key_path: str | os.PathLike[str] | None = None,
    ) -> None:
        if key is not None and key_provider is not None:
            raise ValueError("KEY_SOURCE_CONFLICT")
        self.path = Path(path)
        self.key_path = Path(key_path) if key_path is not None else Path(f"{self.path}.key")
        self._lock = threading.RLock()
        self._closed = False
        self._process_lock_handle: Any = None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._acquire_process_lock()
        try:
            existed = self.path.exists()
            raw_key = bytes(key) if key is not None else (
                key_provider or WindowsDPAPIKeyProvider()
            ).load_or_create(self.key_path, create=not existed)
            self._validate_key(raw_key)
            self._aead_key, self._index_key = self._derive_keys(raw_key)
            del raw_key
            self._connection = sqlite3.connect(
                self.path,
                timeout=5.0,
                isolation_level=None,
                check_same_thread=False,
            )
            self._connection.execute("PRAGMA foreign_keys=ON")
            self._connection.execute("PRAGMA journal_mode=DELETE")
            self._connection.execute("PRAGMA synchronous=FULL")
            self._connection.execute("PRAGMA secure_delete=ON")
            self._connection.execute("PRAGMA temp_store=MEMORY")
            self._create_schema()
            if existed:
                self._verify_store_state()
            else:
                self._initialize_store_state()
        except ConversationStoreError:
            self._close_after_init_failure()
            raise
        except (OSError, sqlite3.Error):
            self._close_after_init_failure()
            raise CorruptStoreError("STORE_UNAVAILABLE") from None

    def _acquire_process_lock(self) -> None:
        lock_path = Path(f"{self.path}.lock")
        try:
            handle = lock_path.open("a+b")
            handle.seek(0)
            if handle.read(1) == b"":
                handle.seek(0)
                handle.write(b"\x00")
                handle.flush()
                os.fsync(handle.fileno())
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)  # type: ignore[attr-defined]
            self._process_lock_handle = handle
        except (OSError, ImportError):
            try:
                handle.close()
            except (UnboundLocalError, OSError):
                pass
            raise StoreBusyError("STORE_BUSY") from None

    def _release_process_lock(self) -> None:
        handle = self._process_lock_handle
        if handle is None:
            return
        self._process_lock_handle = None
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)  # type: ignore[attr-defined]
        except (OSError, ImportError):
            pass
        finally:
            try:
                handle.close()
            except OSError:
                pass

    @staticmethod
    def _validate_key(key: bytes) -> None:
        if len(key) != EncryptedConversationStore._KEY_SIZE:
            raise KeyUnavailableError("KEY_LENGTH_INVALID")

    @classmethod
    def _derive_keys(cls, root_key: bytes) -> tuple[bytes, bytes]:
        material = HKDF(
            algorithm=SHA256(),
            length=64,
            salt=None,
            info=cls._AEAD_INFO + b"\x00" + cls._INDEX_INFO,
        ).derive(root_key)
        return material[:32], material[32:]

    def _close_after_init_failure(self) -> None:
        connection = getattr(self, "_connection", None)
        if connection is not None:
            try:
                connection.close()
            except sqlite3.Error:
                pass
            self._closed = True
        self._release_process_lock()

    def _create_schema(self) -> None:
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS store_state (
                state_id INTEGER PRIMARY KEY CHECK (state_id = 1),
                nonce BLOB NOT NULL,
                ciphertext BLOB NOT NULL
            );
            CREATE TABLE IF NOT EXISTS records (
                record_key BLOB PRIMARY KEY,
                version INTEGER NOT NULL CHECK (version > 0),
                nonce BLOB NOT NULL,
                ciphertext BLOB NOT NULL
            );
            """
        )

    @staticmethod
    def _json_bytes(value: JSONValue | Mapping[str, JSONValue]) -> bytes:
        _validate_json(value)
        try:
            encoded = json.dumps(
                value,
                allow_nan=False,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError, UnicodeError, OverflowError, RecursionError):
            raise CorruptStoreError("JSON_INVALID") from None
        if len(encoded) > EncryptedConversationStore._MAX_RECORD_BYTES:
            raise CorruptStoreError("RECORD_TOO_LARGE")
        return encoded

    @classmethod
    def _decode_json(cls, encoded: bytes) -> Any:
        if len(encoded) > cls._MAX_RECORD_BYTES:
            raise CorruptStoreError("RECORD_TOO_LARGE")
        try:
            def reject_constant(_: str) -> None:
                raise ValueError("non-finite")

            def unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
                result: dict[str, Any] = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError("duplicate")
                    result[key] = value
                return result

            decoded = json.loads(
                encoded.decode("utf-8"),
                object_pairs_hook=unique_pairs,
                parse_constant=reject_constant,
            )
            _validate_json(decoded)
            if cls._json_bytes(decoded) != encoded:
                raise ValueError("noncanonical")
            return decoded
        except CorruptStoreError:
            raise
        except (TypeError, ValueError, UnicodeError, RecursionError, OverflowError):
            raise CorruptStoreError("JSON_INVALID") from None

    @classmethod
    def _locator_bytes(cls, value: str) -> bytes:
        if not isinstance(value, str) or not value:
            raise ValueError("locator")
        try:
            encoded = value.encode("utf-8")
        except UnicodeError:
            raise ValueError("locator") from None
        if len(encoded) > cls._MAX_LOCATOR_BYTES:
            raise ValueError("locator")
        return encoded

    def _record_key(self, namespace: str, record_id: str) -> bytes:
        try:
            namespace_bytes = self._locator_bytes(namespace)
            record_bytes = self._locator_bytes(record_id)
        except ValueError:
            raise ConversationStoreError("LOCATOR_INVALID") from None
        material = (
            struct.pack("!I", len(namespace_bytes))
            + namespace_bytes
            + struct.pack("!I", len(record_bytes))
            + record_bytes
        )
        return hmac.new(self._index_key, material, hashlib.sha256).digest()

    def _record_aad(
        self, namespace: str, record_id: str, version: int, record_key: bytes
    ) -> bytes:
        try:
            namespace_bytes = self._locator_bytes(namespace)
            record_bytes = self._locator_bytes(record_id)
        except ValueError:
            raise ConversationStoreError("LOCATOR_INVALID") from None
        return (
            self._RECORD_AAD_PREFIX
            + record_key
            + struct.pack("!I", version)
            + struct.pack("!I", len(namespace_bytes))
            + namespace_bytes
            + struct.pack("!I", len(record_bytes))
            + record_bytes
        )

    def _encrypt(self, plaintext: bytes, aad: bytes) -> tuple[bytes, bytes]:
        nonce = secrets.token_bytes(12)
        return nonce, AESGCM(self._aead_key).encrypt(nonce, plaintext, aad)

    def _decrypt(self, nonce: bytes, ciphertext: bytes, aad: bytes) -> bytes:
        if len(nonce) != 12 or not ciphertext:
            raise CorruptStoreError("RECORD_CORRUPT")
        try:
            return AESGCM(self._aead_key).decrypt(nonce, ciphertext, aad)
        except (InvalidTag, ValueError, TypeError):
            raise CorruptStoreError("RECORD_CORRUPT") from None

    def _initialize_store_state(self) -> None:
        marker = self._json_bytes({"format": self._FORMAT, "marker": secrets.token_hex(16)})
        nonce, ciphertext = self._encrypt(marker, self._STORE_AAD)
        try:
            self._connection.execute(
                "INSERT INTO store_state(state_id, nonce, ciphertext) VALUES (1, ?, ?)",
                (sqlite3.Binary(nonce), sqlite3.Binary(ciphertext)),
            )
        except sqlite3.Error:
            raise CorruptStoreError("STORE_UNAVAILABLE") from None

    def _verify_store_state(self) -> None:
        try:
            row = self._connection.execute(
                "SELECT nonce, ciphertext FROM store_state WHERE state_id = 1"
            ).fetchone()
        except sqlite3.Error:
            raise CorruptStoreError("STORE_CORRUPT") from None
        if row is None:
            raise CorruptStoreError("STORE_CORRUPT")
        try:
            nonce, ciphertext = bytes(row[0]), bytes(row[1])
        except (TypeError, ValueError):
            raise CorruptStoreError("STORE_CORRUPT") from None
        decoded = self._decode_json(self._decrypt(nonce, ciphertext, self._STORE_AAD))
        if (
            not isinstance(decoded, dict)
            or set(decoded) != {"format", "marker"}
            or decoded.get("format") != self._FORMAT
            or not isinstance(decoded.get("marker"), str)
            or len(decoded["marker"]) != 32
        ):
            raise CorruptStoreError("STORE_CORRUPT")

    def _read_row(self, row: tuple[object, ...]) -> StoredRecord:
        try:
            if len(row) != 4:
                raise ValueError("row")
            raw_record_key: Any = row[0]
            raw_version: Any = row[1]
            raw_nonce: Any = row[2]
            raw_ciphertext: Any = row[3]
            record_key, version, nonce, ciphertext = (
                bytes(raw_record_key),
                int(raw_version),
                bytes(raw_nonce),
                bytes(raw_ciphertext),
            )
        except (TypeError, ValueError, OverflowError):
            raise CorruptStoreError("RECORD_CORRUPT") from None
        if len(record_key) != hashlib.sha256().digest_size or version <= 0:
            raise CorruptStoreError("RECORD_CORRUPT")
        decoded = self._decode_json(
            self._decrypt(nonce, ciphertext, self._outer_aad(record_key, version))
        )
        if not isinstance(decoded, dict) or set(decoded) != {
            "format",
            "namespace",
            "record_id",
            "version",
            "record_nonce",
            "record_ciphertext",
        }:
            raise CorruptStoreError("RECORD_CORRUPT")
        namespace = decoded.get("namespace")
        record_id = decoded.get("record_id")
        envelope_version = decoded.get("version")
        inner_nonce = decoded.get("record_nonce")
        inner_ciphertext = decoded.get("record_ciphertext")
        if (
            decoded.get("format") != self._FORMAT
            or not isinstance(namespace, str)
            or not isinstance(record_id, str)
            or type(envelope_version) is not int
            or envelope_version != version
            or not isinstance(inner_nonce, str)
            or not isinstance(inner_ciphertext, str)
        ):
            raise CorruptStoreError("RECORD_CORRUPT")
        if self._record_key(namespace, record_id) != record_key:
            raise CorruptStoreError("RECORD_CORRUPT")
        try:
            decoded_nonce = base64.b64decode(inner_nonce, validate=True)
            decoded_ciphertext = base64.b64decode(inner_ciphertext, validate=True)
        except (ValueError, TypeError):
            raise CorruptStoreError("RECORD_CORRUPT") from None
        record = self._decode_json(
            self._decrypt(
                decoded_nonce,
                decoded_ciphertext,
                self._record_aad(namespace, record_id, version, record_key),
            )
        )
        if not isinstance(record, dict):
            raise CorruptStoreError("RECORD_CORRUPT")
        return StoredRecord(namespace, record_id, version, dict(record))

    def _outer_aad(self, record_key: bytes, version: int) -> bytes:
        return (
            self._RECORD_AAD_PREFIX
            + record_key
            + struct.pack("!I", version)
        )

    def _fetch(self, namespace: str, record_id: str) -> StoredRecord | None:
        record_key = self._record_key(namespace, record_id)
        try:
            row = self._connection.execute(
                "SELECT record_key, version, nonce, ciphertext FROM records WHERE record_key = ?",
                (sqlite3.Binary(record_key),),
            ).fetchone()
        except sqlite3.Error:
            raise CorruptStoreError("STORE_UNAVAILABLE") from None
        if row is None:
            return None
        result = self._read_row(tuple(row))
        if result.namespace != namespace or result.record_id != record_id:
            raise CorruptStoreError("RECORD_CORRUPT")
        return result

    def get(self, namespace: str, record_id: str) -> StoredRecord | None:
        with self._guard():
            return self._fetch(namespace, record_id)

    def list(self, namespace: str) -> list[StoredRecord]:
        try:
            self._locator_bytes(namespace)
        except ValueError:
            raise ConversationStoreError("LOCATOR_INVALID") from None
        with self._guard():
            try:
                rows = self._connection.execute(
                    "SELECT record_key, version, nonce, ciphertext FROM records ORDER BY record_key"
                ).fetchall()
            except sqlite3.Error:
                raise CorruptStoreError("STORE_UNAVAILABLE") from None
            records = [self._read_row(tuple(row)) for row in rows]
            return [record for record in records if record.namespace == namespace]

    def put(self, namespace: str, record_id: str, data: Mapping[str, JSONValue]) -> StoredRecord:
        if not isinstance(data, Mapping):
            raise ConversationStoreError("RECORD_INVALID")
        normalized = dict(data)
        self._json_bytes(normalized)
        with self._guard():
            record_key = self._record_key(namespace, record_id)
            existing = self._fetch(namespace, record_id)
            version = 1 if existing is None else existing.version + 1
            record_plaintext = self._json_bytes(normalized)
            record_nonce, record_ciphertext = self._encrypt(
                record_plaintext,
                self._record_aad(namespace, record_id, version, record_key),
            )
            envelope = self._json_bytes(
                {
                    "format": self._FORMAT,
                    "namespace": namespace,
                    "record_id": record_id,
                    "version": version,
                    "record_nonce": base64.b64encode(record_nonce).decode("ascii"),
                    "record_ciphertext": base64.b64encode(record_ciphertext).decode("ascii"),
                }
            )
            nonce, ciphertext = self._encrypt(
                envelope,
                self._outer_aad(record_key, version),
            )
            try:
                self._connection.execute(
                    "INSERT INTO records(record_key, version, nonce, ciphertext) "
                    "VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(record_key) DO UPDATE SET version=excluded.version, "
                    "nonce=excluded.nonce, ciphertext=excluded.ciphertext",
                    (
                        sqlite3.Binary(record_key),
                        version,
                        sqlite3.Binary(nonce),
                        sqlite3.Binary(ciphertext),
                    ),
                )
            except sqlite3.Error:
                raise CorruptStoreError("STORE_UNAVAILABLE") from None
            return StoredRecord(namespace, record_id, version, normalized)

    def update(
        self,
        namespace: str,
        record_id: str,
        data: Mapping[str, JSONValue],
        *,
        expected_version: int | None = None,
    ) -> StoredRecord:
        with self._guard():
            current = self._fetch(namespace, record_id)
            if current is None:
                raise RecordNotFoundError("RECORD_NOT_FOUND")
            if expected_version is not None and current.version != expected_version:
                raise VersionConflictError("VERSION_CONFLICT")
            return self.put(namespace, record_id, data)

    def delete(self, namespace: str, record_id: str) -> bool:
        with self._guard():
            record_key = self._record_key(namespace, record_id)
            try:
                cursor = self._connection.execute(
                    "DELETE FROM records WHERE record_key = ?", (sqlite3.Binary(record_key),)
                )
            except sqlite3.Error:
                raise CorruptStoreError("STORE_UNAVAILABLE") from None
            return cursor.rowcount == 1

    def append_message(
        self,
        namespace: str,
        record_id: str,
        message: Mapping[str, JSONValue],
        *,
        expected_version: int | None = None,
    ) -> StoredRecord:
        if not isinstance(message, Mapping):
            raise ConversationStoreError("MESSAGE_INVALID")
        with self._guard():
            current = self._fetch(namespace, record_id)
            if current is None:
                raise RecordNotFoundError("RECORD_NOT_FOUND")
            if expected_version is not None and current.version != expected_version:
                raise VersionConflictError("VERSION_CONFLICT")
            messages = current.data.get("messages")
            if not isinstance(messages, list):
                raise ConversationStoreError("MESSAGES_INVALID")
            updated = dict(current.data)
            updated["messages"] = [*messages, dict(message)]
            return self.put(namespace, record_id, updated)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            try:
                self._connection.commit()
                self._connection.close()
            except sqlite3.Error:
                raise CorruptStoreError("STORE_CLOSE_FAILED") from None
            finally:
                self._closed = True
                self._release_process_lock()

    def __enter__(self) -> EncryptedConversationStore:
        self._ensure_open()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _ensure_open(self) -> None:
        if self._closed:
            raise ConversationStoreError("STORE_CLOSED")

    class _Guard:
        def __init__(self, store: EncryptedConversationStore) -> None:
            self.store = store

        def __enter__(self) -> None:
            self.store._lock.acquire()
            try:
                self.store._ensure_open()
            except Exception:
                self.store._lock.release()
                raise

        def __exit__(self, *_: object) -> None:
            self.store._lock.release()

    def _guard(self) -> _Guard:
        return self._Guard(self)


def _validate_json(value: Any, *, _depth: int = 0) -> None:
    if _depth > 100:
        raise CorruptStoreError("JSON_TOO_DEEP")
    if value is None or type(value) is bool or type(value) is int:
        return
    if type(value) is float:
        if not (value == value and abs(value) != float("inf")):
            raise CorruptStoreError("JSON_INVALID")
        return
    if isinstance(value, str):
        try:
            value.encode("utf-8")
        except UnicodeError:
            raise CorruptStoreError("JSON_INVALID") from None
        return
    if isinstance(value, list):
        for item in value:
            _validate_json(item, _depth=_depth + 1)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise CorruptStoreError("JSON_INVALID")
            _validate_json(key, _depth=_depth + 1)
            _validate_json(item, _depth=_depth + 1)
        return
    raise CorruptStoreError("JSON_INVALID")
