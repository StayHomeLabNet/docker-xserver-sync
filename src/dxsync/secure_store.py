from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


SERVICE_NAME = "Docker XServer Sync"
ACCOUNT_PREFIX = "host-key:"
PASSPHRASE_PREFIX = "ssh-passphrase:"
XSERVER_API_KEY_PREFIX = "xserver-api-key:"


class SecureStoreError(RuntimeError):
    pass


class HostKeySecureStore(Protocol):
    @property
    def backend_name(self) -> str: ...

    def store(self, project_id: str, fingerprint: str) -> str: ...
    def retrieve(self, reference: str) -> str | None: ...
    def delete(self, reference: str) -> None: ...


def host_key_reference(project_id: str) -> str:
    return f"keyring://{ACCOUNT_PREFIX}{project_id}"


def ssh_passphrase_reference(project_id: str) -> str:
    return f"keyring://{PASSPHRASE_PREFIX}{project_id}"


def xserver_api_key_reference(project_id: str) -> str:
    return f"keyring://{XSERVER_API_KEY_PREFIX}{project_id}"


def _account_from_reference(reference: str) -> str:
    prefix = "keyring://"
    if not reference.startswith(prefix):
        raise SecureStoreError(f"Unsupported secure-store reference: {reference!r}")
    account = reference[len(prefix) :]
    if not account.startswith(ACCOUNT_PREFIX):
        raise SecureStoreError("Secure-store reference is not a host-key entry")
    return account


def _passphrase_account_from_reference(reference: str) -> str:
    prefix = "keyring://"
    if not reference.startswith(prefix):
        raise SecureStoreError(f"Unsupported secure-store reference: {reference!r}")
    account = reference[len(prefix) :]
    if not account.startswith(PASSPHRASE_PREFIX):
        raise SecureStoreError("Secure-store reference is not an SSH passphrase entry")
    return account


def _api_key_account_from_reference(reference: str) -> str:
    prefix = "keyring://"
    if not reference.startswith(prefix):
        raise SecureStoreError(f"Unsupported secure-store reference: {reference!r}")
    account = reference[len(prefix) :]
    if not account.startswith(XSERVER_API_KEY_PREFIX):
        raise SecureStoreError("Secure-store reference is not an XServer API key entry")
    return account


class KeyringHostKeyStore:
    def __init__(self) -> None:
        try:
            import keyring
        except ImportError as exc:
            raise SecureStoreError("The OS credential-store component is not installed") from exc
        self._keyring = keyring

    @property
    def backend_name(self) -> str:
        try:
            backend = self._keyring.get_keyring()
            return f"{backend.__class__.__module__}.{backend.__class__.__name__}"
        except Exception as exc:
            return f"unavailable: {exc}"

    def store(self, project_id: str, fingerprint: str) -> str:
        reference = host_key_reference(project_id)
        account = _account_from_reference(reference)
        try:
            self._keyring.set_password(SERVICE_NAME, account, fingerprint)
            verified = self._keyring.get_password(SERVICE_NAME, account)
        except Exception as exc:
            raise SecureStoreError(f"Cannot write to the OS credential store: {exc}") from exc
        if verified != fingerprint:
            raise SecureStoreError("OS credential-store verification failed")
        return reference

    def retrieve(self, reference: str) -> str | None:
        account = _account_from_reference(reference)
        try:
            return self._keyring.get_password(SERVICE_NAME, account)
        except Exception as exc:
            raise SecureStoreError(f"Cannot read from the OS credential store: {exc}") from exc

    def delete(self, reference: str) -> None:
        account = _account_from_reference(reference)
        try:
            current = self._keyring.get_password(SERVICE_NAME, account)
            if current is not None:
                self._keyring.delete_password(SERVICE_NAME, account)
        except Exception as exc:
            raise SecureStoreError(f"Cannot delete from the OS credential store: {exc}") from exc

    def store_passphrase(self, project_id: str, passphrase: str) -> str:
        reference = ssh_passphrase_reference(project_id)
        account = _passphrase_account_from_reference(reference)
        try:
            self._keyring.set_password(SERVICE_NAME, account, passphrase)
            verified = self._keyring.get_password(SERVICE_NAME, account)
        except Exception as exc:
            raise SecureStoreError(f"Cannot write SSH passphrase to the OS credential store: {exc}") from exc
        if verified != passphrase:
            raise SecureStoreError("OS credential-store verification failed")
        return reference

    def retrieve_passphrase(self, reference: str) -> str | None:
        account = _passphrase_account_from_reference(reference)
        try:
            return self._keyring.get_password(SERVICE_NAME, account)
        except Exception as exc:
            raise SecureStoreError(f"Cannot read SSH passphrase from the OS credential store: {exc}") from exc

    def delete_passphrase(self, reference: str) -> None:
        account = _passphrase_account_from_reference(reference)
        try:
            current = self._keyring.get_password(SERVICE_NAME, account)
            if current is not None:
                self._keyring.delete_password(SERVICE_NAME, account)
        except Exception as exc:
            raise SecureStoreError(f"Cannot delete SSH passphrase from the OS credential store: {exc}") from exc

    def store_api_key(self, project_id: str, api_key: str) -> str:
        reference = xserver_api_key_reference(project_id)
        account = _api_key_account_from_reference(reference)
        try:
            self._keyring.set_password(SERVICE_NAME, account, api_key)
            verified = self._keyring.get_password(SERVICE_NAME, account)
        except Exception as exc:
            raise SecureStoreError(f"Cannot write XServer API key to the OS credential store: {exc}") from exc
        if verified != api_key:
            raise SecureStoreError("OS credential-store verification failed")
        return reference

    def retrieve_api_key(self, reference: str) -> str | None:
        account = _api_key_account_from_reference(reference)
        try:
            return self._keyring.get_password(SERVICE_NAME, account)
        except Exception as exc:
            raise SecureStoreError(f"Cannot read XServer API key from the OS credential store: {exc}") from exc

    def delete_api_key(self, reference: str) -> None:
        account = _api_key_account_from_reference(reference)
        try:
            current = self._keyring.get_password(SERVICE_NAME, account)
            if current is not None:
                self._keyring.delete_password(SERVICE_NAME, account)
        except Exception as exc:
            raise SecureStoreError(f"Cannot delete XServer API key from the OS credential store: {exc}") from exc


@dataclass
class MemoryHostKeyStore:
    values: dict[str, str] = field(default_factory=dict)
    fail_writes: bool = False
    fail_reads: bool = False

    @property
    def backend_name(self) -> str:
        return "memory-test-store"

    def store(self, project_id: str, fingerprint: str) -> str:
        if self.fail_writes:
            raise SecureStoreError("Injected secure-store write failure")
        reference = host_key_reference(project_id)
        self.values[reference] = fingerprint
        return reference

    def retrieve(self, reference: str) -> str | None:
        if self.fail_reads:
            raise SecureStoreError("Injected secure-store read failure")
        return self.values.get(reference)

    def delete(self, reference: str) -> None:
        self.values.pop(reference, None)

    def store_passphrase(self, project_id: str, passphrase: str) -> str:
        if self.fail_writes:
            raise SecureStoreError("Injected secure-store write failure")
        reference = ssh_passphrase_reference(project_id)
        self.values[reference] = passphrase
        return reference

    def retrieve_passphrase(self, reference: str) -> str | None:
        if self.fail_reads:
            raise SecureStoreError("Injected secure-store read failure")
        _passphrase_account_from_reference(reference)
        return self.values.get(reference)

    def delete_passphrase(self, reference: str) -> None:
        _passphrase_account_from_reference(reference)
        self.values.pop(reference, None)
