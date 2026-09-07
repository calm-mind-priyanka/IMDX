"""Automatic Telegram inline-button colour support.

Telegram now supports three button background styles for inline keyboards:
primary (blue), success (green), and danger (red).

The project uses Pyrofork, whose pinned version predates this MTProto field.
This module keeps the existing Pyrogram API intact and adds the style field at
serialization time, so existing button/callback code does not need to change.
"""

from __future__ import annotations

import struct
from typing import Optional

_STYLE_PRIMARY = "primary"
_STYLE_SUCCESS = "success"
_STYLE_DANGER = "danger"

# Current Telegram MTProto constructors.
_KB_STYLE = 0x4FDD3430
_KB_CALLBACK = 0xE62BC960
_KB_URL = 0xD80C25EC
_KB_SWITCH_INLINE = 0x991399FC
_KB_WEBVIEW = 0xE846B1A0
_KB_COPY = 0xBCC4AF10
_KB_USER_PROFILE = 0xC0FD5D09
_KB_GAME = 0x89C590F9


def _i(value: int) -> bytes:
    return struct.pack("<I", value & 0xFFFFFFFF)


def _q(value: int) -> bytes:
    return struct.pack("<q", int(value))


def _tl_bytes(value: bytes) -> bytes:
    n = len(value)
    if n < 254:
        out = bytes([n]) + value
    else:
        out = b"\xfe" + n.to_bytes(3, "little") + value
    return out + b"\0" * ((-len(out)) % 4)


def _tl_string(value: str) -> bytes:
    return _tl_bytes(str(value).encode("utf-8"))


def _style_bytes(style: Optional[str]) -> bytes:
    if style == _STYLE_DANGER:
        flags = 1 << 1
    elif style == _STYLE_SUCCESS:
        flags = 1 << 2
    else:
        flags = 1 << 0
    return _i(_KB_STYLE) + _i(flags)


def _normalise_text(text: str) -> str:
    return " ".join(str(text).replace("\n", " ").split()).lower()


def choose_button_style(text: str, callback_data=None) -> str:
    """Choose a colour from the semantic purpose of a button."""
    t = _normalise_text(text)
    c = _normalise_text(callback_data or "")
    s = f"{t} {c}"

    # Destructive / irreversible actions -> red.
    danger = (
        "delete", "remove", "reject", "cancel", "close", "ban", "unban",
        "disable", "clear", "stop", "terminate", "reset", "drop", "deny",
        "decline", "logout", "kick", "purge", "unsubscribe",
    )
    if any(word in s for word in danger):
        return _STYLE_DANGER

    # Positive / primary completion actions -> green.
    success = (
        "approve", "approved", "verify", "verified", "activate", "activated",
        "purchase", "buy", "subscribe", "confirm", "confirmed", "yes",
        "allow", "allowed", "enable", "enabled", "send all", "send_all",
        "get file", "get_file", "download", "continue", "proceed", "start",
        "unlock", "premium", "retry", "try again", "accept", "grant",
    )
    if any(word in s for word in success):
        return _STYLE_SUCCESS

    # Everything else is neutral/navigation -> blue.
    return _STYLE_PRIMARY


class _StyledRawButton:
    """Tiny TL object used only for serialising the new style field."""
    __slots__ = ("payload",)

    def __init__(self, payload: bytes):
        self.payload = payload

    def write(self, *args, **kwargs):
        return self.payload


def _build_button(button):
    style = getattr(button, "style", None)
    if not style:
        # Preserve Pyrofork's normal output for callers that explicitly opt out.
        return None

    text = button.text
    style_blob = _style_bytes(style)

    if button.callback_data is not None:
        data = button.callback_data
        if isinstance(data, str):
            data = data.encode("utf-8")
        flags = (1 if getattr(button, "requires_password", False) else 0) | (1 << 10)
        payload = _i(_KB_CALLBACK) + _i(flags) + style_blob + _tl_string(text) + _tl_bytes(data)
        return _StyledRawButton(payload)

    if button.url is not None:
        flags = 1 << 10
        payload = _i(_KB_URL) + _i(flags) + style_blob + _tl_string(text) + _tl_string(button.url)
        return _StyledRawButton(payload)

    if button.switch_inline_query is not None or button.switch_inline_query_current_chat is not None:
        flags = (1 if button.switch_inline_query_current_chat is not None else 0) | (1 << 10)
        query = (button.switch_inline_query_current_chat
                 if button.switch_inline_query_current_chat is not None
                 else button.switch_inline_query)
        payload = _i(_KB_SWITCH_INLINE) + _i(flags) + style_blob + _tl_string(text) + _tl_string(query or "")
        return _StyledRawButton(payload)

    if button.web_app is not None:
        flags = 1 << 10
        payload = _i(_KB_WEBVIEW) + _i(flags) + style_blob + _tl_string(text) + _tl_string(button.web_app.url)
        return _StyledRawButton(payload)

    if getattr(button, "copy_text", None) is not None:
        flags = 1 << 10
        payload = _i(_KB_COPY) + _i(flags) + style_blob + _tl_string(text) + _tl_string(button.copy_text)
        return _StyledRawButton(payload)

    if button.user_id is not None:
        flags = 1 << 10
        payload = _i(_KB_USER_PROFILE) + _i(flags) + style_blob + _tl_string(text) + _q(button.user_id)
        return _StyledRawButton(payload)

    if button.callback_game is not None:
        flags = 1 << 10
        payload = _i(_KB_GAME) + _i(flags) + style_blob + _tl_string(text)
        return _StyledRawButton(payload)

    return None


def install():
    """Patch Pyrofork's InlineKeyboardButton once, globally."""
    try:
        from pyrogram.types import InlineKeyboardButton
    except Exception:
        return False

    if getattr(InlineKeyboardButton, "_imdx_colours_installed", False):
        return True

    original_init = InlineKeyboardButton.__init__
    original_write = InlineKeyboardButton.write

    # Pyrofork versions differ in which newer InlineKeyboardButton fields
    # they accept. Build kwargs from the installed constructor so this
    # compatibility layer never passes an unknown argument (e.g. copy_text
    # on Pyrofork 2.3.45).
    try:
        import inspect
        _init_params = set(inspect.signature(original_init).parameters)
    except Exception:
        _init_params = set()

    def init(self, text, callback_data=None, url=None, web_app=None, login_url=None,
             user_id=None, switch_inline_query=None, switch_inline_query_current_chat=None,
             callback_game=None, requires_password=None, copy_text=None, style=None, **kwargs):
        values = {
            "callback_data": callback_data,
            "url": url,
            "web_app": web_app,
            "login_url": login_url,
            "user_id": user_id,
            "switch_inline_query": switch_inline_query,
            "switch_inline_query_current_chat": switch_inline_query_current_chat,
            "callback_game": callback_game,
            "requires_password": requires_password,
            "copy_text": copy_text,
        }
        supported = {k: v for k, v in values.items() if k in _init_params}
        supported.update({k: v for k, v in kwargs.items() if k in _init_params})
        original_init(self, text, **supported)
        # Style is handled by our serializer when the installed Pyrofork
        # schema does not expose it natively.
        self.style = style or choose_button_style(text, callback_data)
        # Preserve newer fields for the serializer when the installed class
        # does not define them natively.
        if copy_text is not None and not hasattr(self, "copy_text"):
            self.copy_text = copy_text

    async def write(self, client):
        styled = _build_button(self)
        if styled is not None:
            return styled
        return await original_write(self, client)

    InlineKeyboardButton.__init__ = init
    InlineKeyboardButton.write = write
    InlineKeyboardButton._imdx_colours_installed = True
    return True


install()
