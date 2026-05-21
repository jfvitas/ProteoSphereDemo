"""User identity for the v2 GUI chrome.

Replaces the hardcoded "rosa.kw / Anvil Lab" placeholder. Reads from
environment variables; falls back to the OS username.

Env vars (all optional):
    PROTEOSPHERE_USER_NAME       display name, e.g. "Jonathan Vitas"
    PROTEOSPHERE_USER_HANDLE     short handle, e.g. "jvitas"
    PROTEOSPHERE_USER_INITIALS   avatar initials, e.g. "JV"
    PROTEOSPHERE_LAB             lab / workspace, e.g. "ProteoSphere Lab"
    PROTEOSPHERE_USER_EMAIL      contact email
"""

from __future__ import annotations

import getpass
import os


def _osuser() -> str:
    try:
        return getpass.getuser() or ""
    except Exception:
        return os.environ.get("USERNAME") or os.environ.get("USER") or ""


def user_identity() -> dict:
    osuser = _osuser()
    handle = os.environ.get("PROTEOSPHERE_USER_HANDLE") or osuser or "user"
    # Display name defaults to titlecased osuser; allow override
    default_name = (osuser or "User").replace(".", " ").replace("_", " ").title() or "User"
    name = os.environ.get("PROTEOSPHERE_USER_NAME") or default_name
    # Initials: env > derived from name
    if os.environ.get("PROTEOSPHERE_USER_INITIALS"):
        initials = os.environ["PROTEOSPHERE_USER_INITIALS"]
    else:
        parts = [p for p in name.split() if p]
        initials = (
            (parts[0][:1] + parts[-1][:1]) if len(parts) >= 2
            else (parts[0][:2] if parts else "U")
        ).upper()
    lab   = os.environ.get("PROTEOSPHERE_LAB") or "ProteoSphere Lab"
    email = os.environ.get("PROTEOSPHERE_USER_EMAIL") or ""
    return {
        "handle":   handle,
        "name":     name,
        "initials": initials[:3],
        "lab":      lab,
        "email":    email,
        "os_user":  osuser,
    }
