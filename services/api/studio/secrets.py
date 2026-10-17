"""Backend-only provider secrets stored in the OS credential vault.

Keys never pass through an HTTP request body, manifest or database column.
"""

from __future__ import annotations

import keyring
import ctypes
import sys
import threading
from ctypes import wintypes


SERVICE = "AIAnimationStudio"
_prompt_lock = threading.Lock()


def _windows_vault() -> None:
    backend = keyring.get_keyring()
    if "Windows" not in type(backend).__module__:
        raise RuntimeError("Windows Credential Manager backend is unavailable")


def set_provider_key(provider: str, value: str) -> None:
    _windows_vault()
    if not provider or not value:
        raise ValueError("provider and key are required")
    keyring.set_password(SERVICE, provider, value)


def get_provider_key(provider: str) -> str | None:
    _windows_vault()
    if not provider:
        raise ValueError("provider is required")
    return keyring.get_password(SERVICE, provider)


def delete_provider_key(provider: str) -> None:
    _windows_vault()
    keyring.delete_password(SERVICE, provider)


def prompt_provider_key(provider: str) -> None:
    """Ask for a key in a native Windows dialog, outside the browser DOM."""
    if sys.platform != "win32":
        raise RuntimeError("Native Windows credential dialog is unavailable")
    label = {"OPENAI": "OpenAI", "SEEDANCE_ARK": "Seedance · Volcengine", "SEEDANCE_BYTEPLUS": "Seedance · BytePlus", "ELEVENLABS": "ElevenLabs", "MINIMAX": "MiniMax", "ANTHROPIC": "Claude · Anthropic"}.get(provider)
    if not label:
        raise RuntimeError("Nhà cung cấp khóa chưa được hỗ trợ.")
    _windows_vault()
    if not _prompt_lock.acquire(blocking=False):
        raise RuntimeError("Đang mở cửa sổ nhập khóa khác; hãy hoàn tất cửa sổ đó trước.")

    class CREDUI_INFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("hwndParent", wintypes.HWND),
                    ("pszMessageText", wintypes.LPCWSTR), ("pszCaptionText", wintypes.LPCWSTR),
                    ("hbmBanner", wintypes.HANDLE)]

    owner = None
    password = ctypes.create_unicode_buffer(256)
    try:
        try:
            import tkinter as tk
            owner = tk.Tk()
            owner.title(f"AI Animation Studio · Nhập khóa {label}")
            owner.resizable(False, False)
            owner.geometry("390x100")
            owner.attributes("-topmost", True)
            tk.Label(owner, text=f"Đang mở cửa sổ bảo mật Windows để nhập khóa {label}…",
                     padx=16, pady=26).pack()
            owner.update_idletasks()
            owner.geometry(f"+{max(0, (owner.winfo_screenwidth()-390)//2)}+{max(0, (owner.winfo_screenheight()-100)//2)}")
            owner.lift()
            owner.focus_force()
            owner.update()
        except Exception as exc:
            raise RuntimeError("Không mở được cửa sổ nhập khóa trên phiên Windows này.") from exc
        info = CREDUI_INFO(ctypes.sizeof(CREDUI_INFO), owner.winfo_id(),
                           f"Nhập API key {label}. Khóa được lưu trong Windows Credential Manager và không chuyển qua trình duyệt.",
                           f"AI Animation Studio · Khóa {label}", None)
        username = ctypes.create_unicode_buffer(513)
        username.value = provider
        save = wintypes.BOOL(False)
        flags = 0x40000 | 0x2 | 0x80 | 0x200  # GENERIC, DO_NOT_PERSIST, ALWAYS_SHOW, PASSWORD_ONLY
        result = ctypes.windll.credui.CredUIPromptForCredentialsW(
            ctypes.byref(info), f"{SERVICE}:{provider}", None, 0,
            username, len(username), password, len(password), ctypes.byref(save), flags)
        if result == 1223:  # ERROR_CANCELLED
            raise RuntimeError(f"Đã hủy nhập khóa {label}.")
        if result:
            raise RuntimeError(f"Hộp thoại khóa Windows lỗi mã {result}.")
        if not password.value.strip():
            raise RuntimeError(f"Khóa {label} không được để trống.")
        set_provider_key(provider, password.value.strip())
    finally:
        ctypes.memset(ctypes.addressof(password), 0, ctypes.sizeof(password))
        try:
            if owner is not None:
                owner.destroy()
        finally:
            _prompt_lock.release()
