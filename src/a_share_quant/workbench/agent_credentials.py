"""Local API credentials: Windows user protection, never part of public config."""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes

from dotenv import dotenv_values

from a_share_quant.storage.atomic_json import atomic_write_bytes
from a_share_quant.storage.project_storage import ProjectStoragePolicy


def validate_key(value: str) -> str:
    if (not isinstance(value, str) or not 16 <= len(value) <= 512
        or any(ord(char) < 33 or ord(char) > 126 for char in value)):
        raise ValueError("API 密钥格式无效，请粘贴完整密钥，不要包含空格或换行。")
    return value


def _dpapi(raw: bytes, *, decrypt: bool = False) -> bytes:
    if os.name != "nt":
        raise OSError("本机密钥保存需要 Windows；其他系统可使用环境变量。")

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(wintypes.BYTE))]

    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob),
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
                         ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    buffer = ctypes.create_string_buffer(raw)
    incoming = Blob(len(raw), ctypes.cast(buffer, ctypes.POINTER(wintypes.BYTE)))
    outgoing = Blob()
    try:
        # Current Windows user, no machine-wide scope or interactive OS prompt.
        if not function(ctypes.byref(incoming), None, None, None, None, 1,
                        ctypes.byref(outgoing)):
            raise OSError("本机密钥保护操作失败，请在当前 Windows 账户重新保存。")
        if not 0 < outgoing.size <= 16384:
            raise OSError("本机密钥文件无法核验，请重新保存。")
        return ctypes.string_at(outgoing.data, outgoing.size)
    finally:
        ctypes.memset(buffer, 0, len(raw))
        if outgoing.data:
            ctypes.memset(outgoing.data, 0, outgoing.size)
            kernel.LocalFree(outgoing.data)


class AgentCredentials:
    def __init__(self, policy: ProjectStoragePolicy | None) -> None:
        self.policy = policy
        self.path = None
        self.saved = None
        self.legacy = None
        self.error = False
        try:
            if policy:
                self.path = policy.authorize(".runtime/agent/cloud.key")
                if self.path.exists():
                    policy.revalidate(self.path)
                    if self.path.stat().st_size > 16384:
                        raise ValueError("credential size")
                    self.saved = validate_key(_dpapi(self.path.read_bytes(), decrypt=True).decode())
                else:
                    legacy = policy.authorize(".runtime/agent/cloud.env")
                    if legacy.exists():
                        policy.revalidate(legacy)
                        if legacy.stat().st_size > 4096:
                            raise ValueError("credential size")
                        self.legacy = dotenv_values(legacy, interpolate=False).get(
                            "DEEPSEEK_API_KEY")
        except (OSError, ValueError, UnicodeError):
            # A broken explicit override must not silently switch credentials.
            self.error = True

    def get(self) -> str | None:
        if self.error:
            return None
        return self.saved or os.environ.get("DEEPSEEK_API_KEY") or self.legacy

    def status(self) -> dict:
        source = ("SAVED" if self.saved else "ENVIRONMENT" if os.environ.get("DEEPSEEK_API_KEY")
                  else "LEGACY_FILE" if self.legacy else "NONE")
        return {"configured": bool(self.get()), "source": source,
                "saved": bool(self.saved), "can_save": os.name == "nt" and self.path is not None,
                "error": self.error}

    def save(self, value: str) -> None:
        value = validate_key(value)
        if self.policy is None or self.path is None:
            raise OSError("本机凭据存储不可用。")
        encrypted = _dpapi(value.encode())
        self.policy.revalidate(self.path)
        atomic_write_bytes(self.path, encrypted)
        self.saved = value
        self.error = False

    def remove(self) -> None:
        if self.policy is None or self.path is None:
            raise OSError("本机凭据存储不可用。")
        self.policy.revalidate(self.path)
        self.path.unlink(missing_ok=True)
        # Keep existing environment/legacy configuration usable after an override is removed.
        self.__init__(self.policy)
