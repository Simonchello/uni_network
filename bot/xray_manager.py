"""Xray config management: add/remove clients, restart service."""
import json
import uuid
import fcntl
import subprocess
from contextlib import contextmanager
from typing import List, Tuple

from config import XRAY_CONFIG_PATH


@contextmanager
def _locked_config():
    """Exclusive lock on config file for concurrent safety."""
    with open(XRAY_CONFIG_PATH, "r+") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            yield f
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def _read() -> dict:
    with open(XRAY_CONFIG_PATH, "r") as f:
        return json.load(f)


def _write(config: dict):
    with open(XRAY_CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


def add_client(email: str) -> str:
    """Add client with given email to both inbounds. Returns new UUID."""
    new_uuid = str(uuid.uuid4())

    with _locked_config():
        config = _read()
        for inbound in config["inbounds"]:
            tag = inbound.get("tag", "")
            if tag == "vless-reality-vision":
                inbound["settings"]["clients"].append({
                    "id": new_uuid,
                    "email": email,
                    "flow": "xtls-rprx-vision",
                })
            elif tag == "vless-reality-xhttp":
                inbound["settings"]["clients"].append({
                    "id": new_uuid,
                    "email": email,
                })
        _write(config)

    restart_xray()
    return new_uuid


def remove_client(email: str):
    """Remove client by email from all inbounds."""
    with _locked_config():
        config = _read()
        for inbound in config["inbounds"]:
            if "settings" in inbound and "clients" in inbound["settings"]:
                inbound["settings"]["clients"] = [
                    c for c in inbound["settings"]["clients"]
                    if c.get("email") != email
                ]
        _write(config)

    restart_xray()


def list_clients() -> List[Tuple[str, str]]:
    """Return [(email, uuid)] from vision inbound (source of truth)."""
    config = _read()
    for inbound in config["inbounds"]:
        if inbound.get("tag") == "vless-reality-vision":
            return [
                (c["email"], c["id"])
                for c in inbound["settings"]["clients"]
                if "email" in c
            ]
    return []


def restart_xray():
    subprocess.run(
        ["systemctl", "restart", "xray"],
        check=True,
        capture_output=True,
        timeout=30,
    )


def validate_config() -> bool:
    result = subprocess.run(
        ["xray", "run", "-test", "-config", XRAY_CONFIG_PATH],
        capture_output=True,
        timeout=10,
    )
    return result.returncode == 0
