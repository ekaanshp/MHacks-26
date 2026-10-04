"""Private atomic storage with optional authenticated encryption at rest.

LASTLY_DATA_KEY is an externally managed, URL-safe base64 encoded 32-byte key.
No key is generated, persisted, or logged here. Plaintext compatibility exists for
synthetic fixtures and explicit migration; private/production policy is enforced
before the application starts and before the Takeout importer writes data.
"""
from __future__ import annotations

import base64
import binascii
import json
import os
import re
import secrets
import stat
from contextlib import contextmanager
from pathlib import Path
from typing import Any

_MARKER = "__lastly_encrypted__"
_VERSION = 1
_LIMIT = 64 * 1024 * 1024
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_CLOEXEC = getattr(os, "O_CLOEXEC", 0)


class StorageError(ValueError):
    """A private file, encryption key, or authenticated envelope is unsafe."""


def data_key() -> bytes | None:
    """Validate at each use so changes never leave a stale encryption key cached."""
    value = os.environ.get("LASTLY_DATA_KEY", "")
    if not value:
        return None
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}=?", value):
        raise StorageError("LASTLY_DATA_KEY must be a URL-safe base64 encoded 32-byte key.")
    try:
        key = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    except (ValueError, binascii.Error) as exc:
        raise StorageError("LASTLY_DATA_KEY must be a URL-safe base64 encoded 32-byte key.") from exc
    if len(key) != 32:
        raise StorageError("LASTLY_DATA_KEY must contain exactly 32 random bytes.")
    return key


def encryption_enabled() -> bool:
    return data_key() is not None


def _absolute(path: Path | str) -> Path:
    return Path(os.path.abspath(os.path.expanduser(os.fspath(path))))


def _require_posix() -> None:
    # Fail closed rather than pretend Windows mode bits enforce owner-only ACLs.
    if os.name != "posix" or not _NOFOLLOW or not hasattr(os, "O_DIRECTORY"):
        raise StorageError("Private storage requires a POSIX filesystem with no-follow support.")


@contextmanager
def _directory(path: Path | str, *, create: bool = False, private: bool = False):
    """Walk directory descriptors so symlink swaps cannot redirect file access."""
    _require_posix()
    absolute = _absolute(path)
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY | _NOFOLLOW | _CLOEXEC)
    try:
        for component in absolute.parts[1:]:
            if create:
                try:
                    os.mkdir(component, mode=0o700, dir_fd=descriptor)
                except FileExistsError:
                    pass
            try:
                child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | _NOFOLLOW | _CLOEXEC, dir_fd=descriptor)
            except OSError as exc:
                if isinstance(exc, FileNotFoundError):
                    raise
                raise StorageError("Private storage cannot use symbolic links or unsafe directories.") from exc
            os.close(descriptor)
            descriptor = child
        info = os.fstat(descriptor)
        if info.st_uid != os.geteuid():
            raise StorageError("The private data directory must be owned by the application user.")
        if private:
            os.fchmod(descriptor, 0o700)
        yield descriptor
    finally:
        os.close(descriptor)


def ensure_private_directory(path: Path | str) -> Path:
    with _directory(path, create=True, private=True):
        pass
    return _absolute(path)


def _check_file(info: os.stat_result) -> None:
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1:
        raise StorageError("Private storage requires an owned regular file without hard links.")


def _check_target(directory: int, name: str) -> None:
    try:
        info = os.stat(name, dir_fd=directory, follow_symlinks=False)
    except FileNotFoundError:
        return
    _check_file(info)


def _read_bytes(path: Path | str, *, max_bytes: int) -> bytes:
    absolute = _absolute(path)
    with _directory(absolute.parent) as directory:
        try:
            descriptor = os.open(absolute.name, os.O_RDONLY | _NOFOLLOW | _CLOEXEC | os.O_NONBLOCK, dir_fd=directory)
        except OSError as exc:
            if isinstance(exc, FileNotFoundError):
                raise
            raise StorageError("Private storage cannot read a symbolic link or unsafe file.") from exc
        with os.fdopen(descriptor, "rb") as stream:
            _check_file(os.fstat(stream.fileno()))
            # Encryption envelopes carry base64; bound the raw read as well as plaintext.
            raw_limit = max_bytes * 4 // 3 + 4096
            if os.fstat(stream.fileno()).st_size > raw_limit:
                raise StorageError("The private data file exceeds its allowed size.")
            payload = stream.read(raw_limit + 1)
            if len(payload) > raw_limit:
                raise StorageError("The private data file exceeds its allowed size.")
            return payload


def _aad(path: Path | str) -> bytes:
    return f"Lastly/storage/v{_VERSION}/{_absolute(path).name}".encode()


def _encode(path: Path | str, content: str) -> bytes:
    payload = content.encode("utf-8")
    if len(payload) > _LIMIT:
        raise StorageError("The private data file exceeds its allowed size.")
    key = data_key()
    if key is None:
        return payload
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    nonce = secrets.token_bytes(12)
    ciphertext = AESGCM(key).encrypt(nonce, payload, _aad(path))
    return (json.dumps({
        _MARKER: _VERSION,
        "algorithm": "AES-256-GCM",
        "nonce": base64.urlsafe_b64encode(nonce).decode("ascii"),
        "ciphertext": base64.urlsafe_b64encode(ciphertext).decode("ascii"),
    }, separators=(",", ":")) + "\n").encode("utf-8")


def _envelope(payload: bytes) -> dict[str, Any] | None:
    try:
        value = json.loads(payload)
    except (ValueError, UnicodeError):
        return None
    if isinstance(value, dict) and _MARKER in value:
        return value
    return None


def is_encrypted(path: Path | str) -> bool:
    return _envelope(_read_bytes(path, max_bytes=_LIMIT)) is not None


def read_text(path: Path | str, *, max_bytes: int = _LIMIT) -> str:
    if max_bytes < 1:
        raise ValueError("The private file size limit must be positive.")
    payload = _read_bytes(path, max_bytes=max_bytes)
    envelope = _envelope(payload)
    if envelope is not None:
        if set(envelope) != {_MARKER, "algorithm", "nonce", "ciphertext"} or type(envelope[_MARKER]) is not int or envelope[_MARKER] != _VERSION or envelope["algorithm"] != "AES-256-GCM":
            raise StorageError("The encrypted private data format is unsupported or corrupt.")
        key = data_key()
        if key is None:
            raise StorageError("LASTLY_DATA_KEY is required to read encrypted private data.")
        from cryptography.exceptions import InvalidTag
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        try:
            nonce = base64.b64decode(envelope["nonce"], altchars=b"-_", validate=True)
            ciphertext = base64.b64decode(envelope["ciphertext"], altchars=b"-_", validate=True)
            if len(nonce) != 12 or len(ciphertext) < 16:
                raise ValueError("Invalid encrypted data lengths.")
            payload = AESGCM(key).decrypt(nonce, ciphertext, _aad(path))
        except (ValueError, TypeError, binascii.Error, InvalidTag) as exc:
            raise StorageError("Encrypted private data could not be authenticated. Check the key and file integrity.") from exc
    if len(payload) > max_bytes:
        raise StorageError("The private data file exceeds its allowed size.")
    try:
        return payload.decode("utf-8")
    except UnicodeError as exc:
        raise StorageError("The private data file is not valid UTF-8.") from exc


def read_json(path: Path | str, *, max_bytes: int = _LIMIT) -> Any:
    return json.loads(read_text(path, max_bytes=max_bytes))


def write_text(path: Path | str, content: str, *, overwrite: bool = True) -> None:
    """Publish a fully fsynced owner-only file atomically without following links."""
    absolute = _absolute(path)
    payload = _encode(absolute, content)
    with _directory(absolute.parent, create=True, private=True) as directory:
        if overwrite:
            _check_target(directory, absolute.name)
        else:
            try:
                os.stat(absolute.name, dir_fd=directory, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise FileExistsError("The private data output already exists.")
        temporary = f".{absolute.name}.{secrets.token_hex(12)}.tmp"
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW | _CLOEXEC, 0o600, dir_fd=directory)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            if overwrite:
                _check_target(directory, absolute.name)
                os.replace(temporary, absolute.name, src_dir_fd=directory, dst_dir_fd=directory)
            else:
                # Unlike an exists() check, this refuses a concurrent writer too.
                os.link(temporary, absolute.name, src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)
                os.unlink(temporary, dir_fd=directory)
            os.fsync(directory)
        finally:
            try:
                os.unlink(temporary, dir_fd=directory)
            except FileNotFoundError:
                pass


def write_json(path: Path | str, value: Any, *, overwrite: bool = True) -> None:
    write_text(path, json.dumps(value, indent=2, ensure_ascii=False) + "\n", overwrite=overwrite)


@contextmanager
def private_file_lock(path: Path | str):
    """Cross-process lock with the same ownership and link protections as data."""
    import fcntl

    absolute = _absolute(path)
    with _directory(absolute.parent, create=True, private=True) as directory:
        _check_target(directory, absolute.name)
        descriptor = os.open(absolute.name, os.O_RDWR | os.O_CREAT | _NOFOLLOW | _CLOEXEC | os.O_NONBLOCK, 0o600, dir_fd=directory)
        with os.fdopen(descriptor, "a+") as stream:
            _check_file(os.fstat(stream.fileno()))
            os.fchmod(stream.fileno(), 0o600)
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
