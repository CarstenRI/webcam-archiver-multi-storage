"""Passwort-Hashing direkt via bcrypt – ohne passlib-Wrapper.

Hintergrund: passlib hat eine bekannte Inkompatibilitaet mit bcrypt>=4.0
(macht beim Self-Test einen Probe-Hash mit zu langem Passwort, der dort
einen ValueError statt eines Truncates ausloest). Direktes bcrypt ist
robuster und reicht fuer unseren Use-Case.

bcrypt unterstuetzt nur die ersten 72 Bytes des Passworts – wir truncaten
proaktiv, damit nichts crasht.
"""
from __future__ import annotations

import bcrypt


def hash_password(password: str) -> str:
    """Liefert einen bcrypt-Hash als ASCII-String."""
    if not isinstance(password, str):
        password = str(password)
    pw_bytes = password.encode("utf-8")[:72]
    return bcrypt.hashpw(pw_bytes, bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, hashed: str) -> bool:
    """Vergleicht Klartext-Passwort gegen bcrypt-Hash. Returns True/False."""
    if not password or not hashed:
        return False
    try:
        pw_bytes = password.encode("utf-8")[:72]
        hash_bytes = hashed.encode("ascii") if isinstance(hashed, str) else hashed
        return bcrypt.checkpw(pw_bytes, hash_bytes)
    except (ValueError, TypeError):
        return False
