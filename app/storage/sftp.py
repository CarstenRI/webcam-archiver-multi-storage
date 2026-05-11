"""SFTP/SSH-Backend (paramiko).

Unterstützt Passwort- und Key-basierte Auth. Pfad-Template steuert die
Struktur unter dem Basis-Pfad. Verzeichnisse werden bei Bedarf rekursiv angelegt.
"""
from __future__ import annotations

import logging
import posixpath
import stat
from datetime import datetime
from pathlib import Path
from typing import Optional

from .base import (
    BackendError, ConfigField, StorageBackend, UploadResult, render_template,
)

log = logging.getLogger(__name__)


class SftpBackend(StorageBackend):
    type_id = "sftp"
    display_name = "SFTP / SSH"
    description = (
        "Lädt Bilder via SSH zu einem entfernten Server. Authentifizierung über "
        "Passwort oder SSH-Key-Datei. Verzeichnisse werden bei Bedarf rekursiv angelegt."
    )

    @classmethod
    def config_fields(cls) -> list[ConfigField]:
        return [
            ConfigField(
                key="host", label="Host", required=True,
                placeholder="nas.local oder 192.168.1.20",
            ),
            ConfigField(
                key="port", label="Port", kind="number", default="22",
            ),
            ConfigField(
                key="username", label="Benutzer", required=True,
            ),
            ConfigField(
                key="auth_method", label="Auth-Methode", kind="select",
                default="password",
                options=[("password", "Passwort"), ("key", "SSH-Key-Datei")],
            ),
            ConfigField(
                key="password", label="Passwort", kind="password",
                help="Nur bei Auth-Methode 'Passwort'.",
            ),
            ConfigField(
                key="key_path", label="Pfad zum Private Key", kind="text",
                placeholder="/etc/webcam-uploader/keys/id_ed25519",
                help="Nur bei Auth-Methode 'SSH-Key-Datei'. Muss vom Service-User lesbar sein.",
            ),
            ConfigField(
                key="key_passphrase", label="Key-Passphrase (optional)", kind="password",
            ),
            ConfigField(
                key="base_path", label="Basis-Pfad auf dem Server", required=True,
                placeholder="/volume1/webcams",
            ),
            ConfigField(
                key="known_hosts_strict", label="Known-Hosts-Prüfung", kind="checkbox",
                default="",
                help="Wenn aktiv: Host-Key muss in ~/.ssh/known_hosts bekannt sein.",
            ),
        ]

    # -------- intern --------
    def _connect(self):
        try:
            import paramiko
        except ImportError as e:
            raise BackendError(f"paramiko nicht installiert: {e}")

        host = (self.config.get("host") or "").strip()
        if not host:
            raise BackendError("Host fehlt.")
        try:
            port = int(self.config.get("port") or 22)
        except (TypeError, ValueError):
            port = 22
        username = (self.config.get("username") or "").strip()
        if not username:
            raise BackendError("Benutzer fehlt.")
        auth_method = (self.config.get("auth_method") or "password").strip()
        strict = bool(self.config.get("known_hosts_strict"))

        client = paramiko.SSHClient()
        if strict:
            client.load_system_host_keys()
            client.set_missing_host_key_policy(paramiko.RejectPolicy())
        else:
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        connect_kw = {
            "hostname": host,
            "port": port,
            "username": username,
            "timeout": 15,
            "allow_agent": False,
            "look_for_keys": False,
        }

        if auth_method == "key":
            key_path = (self.config.get("key_path") or "").strip()
            if not key_path:
                raise BackendError("Key-Pfad fehlt.")
            passphrase = self.config.get("key_passphrase") or None
            # Versuche verschiedene Key-Typen
            pkey = None
            errors = []
            for cls in (paramiko.Ed25519Key, paramiko.ECDSAKey, paramiko.RSAKey, paramiko.DSSKey):
                try:
                    pkey = cls.from_private_key_file(key_path, password=passphrase)
                    break
                except Exception as e:  # noqa: BLE001
                    errors.append(f"{cls.__name__}: {e}")
            if pkey is None:
                raise BackendError(
                    "Key konnte nicht geladen werden. Versucht: " + " | ".join(errors)
                )
            connect_kw["pkey"] = pkey
        else:
            password = self.config.get("password") or ""
            if not password:
                raise BackendError("Passwort fehlt.")
            connect_kw["password"] = password

        try:
            client.connect(**connect_kw)
        except Exception as e:  # noqa: BLE001
            raise BackendError(f"SSH-Connect fehlgeschlagen: {e}")
        return client

    def _mkdirs(self, sftp, remote_dir: str) -> None:
        """Legt `remote_dir` rekursiv an (POSIX-Pfad)."""
        if not remote_dir or remote_dir == "/":
            return
        parts = [p for p in remote_dir.split("/") if p]
        cur = "" if remote_dir.startswith("/") else "."
        # Wenn absolut, starte mit "/"
        if remote_dir.startswith("/"):
            cur = ""
        for p in parts:
            cur = cur + "/" + p if cur or remote_dir.startswith("/") else p
            try:
                sftp.stat(cur)
            except IOError:
                try:
                    sftp.mkdir(cur)
                except IOError as e:
                    raise BackendError(f"mkdir {cur} fehlgeschlagen: {e}")

    # -------- API --------
    def upload(
        self,
        path: Path,
        image_bytes: bytes,
        cam_id: int,
        cam_name: str,
        album_name: Optional[str] = None,
        album_db_id: Optional[int] = None,
        taken_at: Optional[datetime] = None,
    ) -> UploadResult:
        client = None
        try:
            base = (self.config.get("base_path") or "").strip()
            if not base:
                return UploadResult(status="error", message="Basis-Pfad fehlt.")
            rel = render_template(
                self.path_template, cam_id, cam_name, album_name, image_bytes, taken_at,
            )
            remote_path = posixpath.join(base, rel)
            remote_dir = posixpath.dirname(remote_path)

            client = self._connect()
            sftp = client.open_sftp()
            try:
                self._mkdirs(sftp, remote_dir)
                sftp.put(str(path), remote_path)
            finally:
                sftp.close()
            return UploadResult(
                status="success",
                message="",
                remote_ref=remote_path,
                bytes=len(image_bytes),
            )
        except BackendError as e:
            return UploadResult(status="error", message=str(e)[:500])
        except Exception as e:  # noqa: BLE001
            return UploadResult(status="error", message=f"SFTP: {e}"[:500])
        finally:
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass


    def delete(self, remote_ref: str) -> tuple[bool, str]:
        if not remote_ref:
            return False, "kein Pfad"
        client = None
        try:
            client = self._connect()
            sftp = client.open_sftp()
            try:
                try:
                    sftp.remove(remote_ref)
                except IOError:
                    # bereits weg - kein Fehler
                    pass
                # leere Eltern-Verzeichnisse bis zum base_path raeumen
                import posixpath
                base = (self.config.get("base_path") or "").strip().rstrip("/")
                cur = posixpath.dirname(remote_ref)
                while cur and cur != base and cur != "/":
                    try:
                        sftp.rmdir(cur)
                    except IOError:
                        break
                    cur = posixpath.dirname(cur)
            finally:
                sftp.close()
            return True, remote_ref
        except BackendError as e:
            return False, str(e)
        except Exception as e:  # noqa: BLE001
            return False, f"sftp.delete: {e}"
        finally:
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass

    def health_check(self) -> tuple[bool, str]:
        # Statischer Check: alle Felder vorhanden?
        host = (self.config.get("host") or "").strip()
        user = (self.config.get("username") or "").strip()
        base = (self.config.get("base_path") or "").strip()
        if not (host and user and base):
            return False, "Pflichtfelder fehlen."
        return True, "Konfiguriert (Test-Verbindung beim Speichern)"

    def test_connection(self) -> tuple[bool, str]:
        client = None
        try:
            client = self._connect()
            sftp = client.open_sftp()
            try:
                base = (self.config.get("base_path") or "").strip()
                # Versuche base_path zu erreichen / anzulegen
                try:
                    sftp.stat(base)
                except IOError:
                    self._mkdirs(sftp, base)
                # Schreibtest
                test_path = posixpath.join(base, ".webcam-uploader-write-test")
                with sftp.file(test_path, "w") as f:
                    f.write("ok")
                try:
                    sftp.remove(test_path)
                except IOError:
                    pass
            finally:
                sftp.close()
            return True, f"SFTP OK → {self.config.get('host')}:{self.config.get('base_path')}"
        except BackendError as e:
            return False, str(e)
        except Exception as e:  # noqa: BLE001
            return False, f"SFTP-Test: {e}"
        finally:
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass
