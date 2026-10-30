"""Independent, offline Android property-profile engine for 4AM Changer."""
from __future__ import annotations

import json
import os
import re
import secrets
import shlex
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

VERSION = "1.0.0"
ASSETS = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "assets"
PRODUCT_FIELDS = ("brand", "manufacturer", "model", "name", "device")
ALLOWED_PROPERTIES = frozenset(
    {f"ro.product.{field}" for field in PRODUCT_FIELDS}
    | {"ro.serialno", "ro.boot.serialno", "ro.product.board", "ro.board.platform",
       "ro.soc.model", "ro.build.fingerprint", "gsm.operator.alpha",
       "gsm.operator.numeric", "gsm.sim.operator.alpha", "gsm.sim.operator.numeric"}
)


class ChangerError(Exception):
    """An actionable error suitable for display in the desktop app."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def storage_dir() -> Path:
    base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
    for target in (base / "userdata", Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "4AM-Changer"):
        try:
            target.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryFile(dir=target):
                pass
            return target
        except OSError:
            continue
    raise ChangerError("No writable folder available for profiles and backups.")


def read_json(path: Path):
    if path.stat().st_size > 8_000_000:
        raise ChangerError("JSON file exceeds the 8 MB limit.")
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".4am-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def validate_properties(properties: dict, allow_absent: bool = False) -> dict:
    if not isinstance(properties, dict) or not properties or len(properties) > len(ALLOWED_PROPERTIES):
        raise ChangerError("A profile must contain a nonempty supported property map.")
    clean = {}
    for key, value in properties.items():
        if key not in ALLOWED_PROPERTIES:
            raise ChangerError(f"Unsupported property: {key!r}")
        if value is None and allow_absent:
            clean[key] = None
            continue
        if not isinstance(value, str) or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ChangerError(f"{key}: value must be text without control characters.")
        limit = 1024 if key.startswith("ro.") else 91
        if len(value.encode("utf-8")) > limit:
            raise ChangerError(f"{key}: value exceeds {limit} bytes.")
        clean[key] = value
    return clean


def validate_profile(data: dict) -> dict:
    if not isinstance(data, dict) or data.get("schema") != "4am.profile.v1":
        raise ChangerError("Not a 4AM profile (expected schema 4am.profile.v1).")
    label = data.get("label")
    if not isinstance(label, str) or not label.strip() or len(label) > 200:
        raise ChangerError("Profile label must contain 1–200 characters.")
    return {"schema": "4am.profile.v1", "label": label,
            "created_at": str(data.get("created_at", utc_now())),
            "properties": validate_properties(data.get("properties")),
            "metadata": data.get("metadata", {}) if isinstance(data.get("metadata", {}), dict) else {}}


def validate_backup(data: dict, serial: str | None = None) -> dict:
    if not isinstance(data, dict) or data.get("schema") != "4am.backup.v1":
        raise ChangerError("Not a 4AM property backup.")
    if not isinstance(data.get("serial"), str) or not data["serial"].strip():
        raise ChangerError("Backup has no device serial.")
    if serial is not None and data["serial"] != serial:
        raise ChangerError("Backup belongs to a different ADB serial. Restore is blocked.")
    return {**data, "properties": validate_properties(data.get("properties"), allow_absent=True)}


class Catalog:
    def __init__(self, folder: Path = ASSETS):
        self.devices = read_json(folder / "devices.json")
        self.networks = read_json(folder / "networks.json")
        self.countries = read_json(folder / "countries.json")
        self.country_by_code = {row["country_code"]: row for row in self.countries}
        brands = {}
        for row in self.devices:
            brands.setdefault(row["brand"].casefold(), row["brand"])
        self.brands = sorted(brands.values(), key=str.casefold)

    def find(self, brand: str = "", query: str = "") -> list[dict]:
        query = query.casefold().strip()
        return [d for d in self.devices if (not brand or brand.casefold() == d["brand"].casefold())
                and (not query or query in f"{d['model']} {d.get('modelName', '')}".casefold())]

    def make_profile(self, device: dict, network: dict | None = None, carrier: str = "",
                     random_serial: bool = True, hardware: bool = False,
                     fingerprint: str = "") -> dict:
        props = {"ro.product.brand": str(device["brand"]),
                 "ro.product.manufacturer": str(device["brand"]),
                 "ro.product.model": str(device["model"]),
                 "ro.product.name": str(device.get("product") or device["model"]),
                 "ro.product.device": str(device.get("device") or device["model"])}
        if random_serial:
            value = "".join(secrets.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789") for _ in range(16))
            props.update({"ro.serialno": value, "ro.boot.serialno": value})
        if hardware:
            for source, target in (("board", "ro.product.board"), ("platform", "ro.board.platform"), ("soc", "ro.soc.model")):
                if device.get(source):
                    props[target] = str(device[source])
        if fingerprint.strip():
            props["ro.build.fingerprint"] = fingerprint.strip()
        if network is not None:
            candidates = [x.strip() for x in network.get("carrier", "").split(",") if x.strip()]
            chosen = carrier if carrier else secrets.choice(candidates) if candidates else ""
            if chosen not in candidates:
                raise ChangerError("Select a carrier from the selected country's catalog.")
            name, sep, code = chosen.rpartition("-")
            if not sep or not re.fullmatch(r"\d{5,6}", code):
                raise ChangerError("Carrier is missing a valid 5–6 digit MCC/MNC code.")
            props.update({"gsm.operator.alpha": name, "gsm.operator.numeric": code,
                          "gsm.sim.operator.alpha": name, "gsm.sim.operator.numeric": code})
        return validate_profile({"schema": "4am.profile.v1", "created_at": utc_now(),
            "label": f"{device['brand']} {device['model']}", "properties": props,
            "metadata": {"model_name": device.get("modelName", ""),
                "country": network.get("name", "") if network else "",
                "screen": f"{device.get('screenW', '?')} × {device.get('screenH', '?')}",
                "density": device.get("density"), "scope": "runtime-properties-only"}})


def parse_getprop(output: str) -> dict[str, str]:
    result = {}
    for line in output.splitlines():
        match = re.fullmatch(r"\[([^\]]+)\]: \[(.*)\]", line)
        if match:
            result[match[1]] = match[2]
    return result


def property_script(properties: dict, resetprop: str = "resetprop") -> str:
    if resetprop not in ("resetprop", "magisk resetprop"):
        raise ChangerError("Invalid resetprop executable.")
    properties = validate_properties(properties, allow_absent=True)
    lines = ["set -e"]
    for key, value in properties.items():
        if value is None:
            # Only delete a property that exists; absence is already the desired state.
            lines.append(f"if {resetprop} | grep -F -q {shlex.quote('[' + key + ']:')}; then {resetprop} -n --delete {shlex.quote(key)}; fi")
        else:
            lines.append(f"{resetprop} -n {shlex.quote(key)} {shlex.quote(value)}")
    return "\n".join(lines)


def verify_properties(actual: dict, expected: dict) -> None:
    mismatches = [key for key, value in expected.items()
                  if (key in actual if value is None else actual.get(key) != value)]
    if mismatches:
        raise ChangerError("Read-back mismatch: " + ", ".join(mismatches))


class Adb:
    def __init__(self, executable: Path | str, timeout: int = 30):
        self.executable = str(executable)
        self.timeout = timeout

    def run(self, args: list[str], serial: str | None = None) -> str:
        if serial is not None and (not serial.strip() or serial.startswith("-") or any(c.isspace() for c in serial)):
            raise ChangerError("Invalid ADB serial.")
        cmd = [self.executable] + (["-s", serial] if serial is not None else []) + args
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=self.timeout, shell=False,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        except FileNotFoundError as exc:
            raise ChangerError("ADB was not found. Choose adb.exe in the connection panel.") from exc
        except subprocess.TimeoutExpired as exc:
            raise ChangerError("ADB timed out. Check the USB connection and any root permission prompt.") from exc
        if result.returncode:
            raise ChangerError((result.stderr or result.stdout or f"ADB exit {result.returncode}").strip()[:2000])
        return result.stdout.strip()

    def devices(self) -> list[dict]:
        output = self.run(["devices", "-l"])
        rows = []
        for line in output.splitlines():
            parts = line.split()
            if len(parts) < 2 or parts[0] == "List" or line.startswith("*"):
                continue
            info = {"serial": parts[0], "state": parts[1], "model": ""}
            for field in parts[2:]:
                if field.startswith("model:"):
                    info["model"] = field[6:].replace("_", " ")
            rows.append(info)
        return rows

    def props(self, serial: str) -> dict:
        props = parse_getprop(self.run(["shell", "getprop"], serial))
        if not props:
            raise ChangerError("No readable getprop properties returned by this device.")
        return props

    def root_mode(self, serial: str) -> str:
        if self.run(["shell", "id -u"], serial).strip() == "0":
            return "adbd"
        if self.run(["shell", "su -c 'id -u'"], serial).strip() != "0":
            raise ChangerError("Root access is required to apply properties. Inspection and preview work without it.")
        return "su"

    def root(self, serial: str, script: str, mode: str) -> str:
        return self.run(["shell", "su -c " + shlex.quote(script) if mode == "su" else script], serial)

    def resetprop(self, serial: str, mode: str) -> str:
        result = self.root(serial,
            "if command -v resetprop >/dev/null 2>&1; then echo resetprop; "
            "elif command -v magisk >/dev/null 2>&1 && magisk resetprop --help >/dev/null 2>&1; "
            "then echo 'magisk resetprop'; else echo 'No resetprop installed' >&2; exit 7; fi", mode)
        if result not in ("resetprop", "magisk resetprop"):
            raise ChangerError("A working Magisk resetprop is required on the device.")
        return result


class Engine:
    def __init__(self, adb: Adb, folder: Path, log: Callable[[str], None] | None = None):
        self.adb, self.folder, self.log = adb, folder, log or (lambda message: None)

    def snapshot(self, serial: str, keys=None, actual: dict | None = None) -> dict:
        actual = self.adb.props(serial) if actual is None else actual
        keys = ALLOWED_PROPERTIES if keys is None else keys
        return {"schema": "4am.backup.v1", "serial": serial, "created_at": utc_now(),
                "properties": {key: actual.get(key) for key in sorted(keys)},
                "device": {key: actual.get(key, "") for key in
                           ("ro.product.model", "ro.build.version.release", "ro.build.fingerprint")}}

    def save_snapshot(self, snapshot: dict) -> Path:
        validate_backup(snapshot)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        safe_serial = re.sub(r"[^\w.-]", "_", snapshot["serial"])[:80]
        path = self.folder / "backups" / f"{stamp}-{safe_serial}-{secrets.token_hex(3)}.json"
        write_json(path, snapshot)
        return path

    def backup(self, serial: str) -> Path:
        return self.save_snapshot(self.snapshot(serial))

    def apply(self, serial: str, profile: dict) -> dict:
        profile = validate_profile(profile)
        return self._transaction(serial, profile["properties"])

    def restore(self, serial: str, backup: dict) -> dict:
        backup = validate_backup(backup, serial)
        return self._transaction(serial, backup["properties"])

    def _transaction(self, serial: str, properties: dict) -> dict:
        properties = validate_properties(properties, allow_absent=True)
        mode = self.adb.root_mode(serial)
        tool = self.adb.resetprop(serial, mode)
        before = self.snapshot(serial, properties)
        # Backup must reach durable local storage before the first mutation.
        path = self.save_snapshot(before)
        self.log(f"Automatic pre-change backup: {path}")
        try:
            self.adb.root(serial, property_script(properties, tool), mode)
            verify_properties(self.adb.props(serial), properties)
        except Exception as exc:
            self.log("Apply failed; attempting rollback from the pre-change snapshot.")
            try:
                self.adb.root(serial, property_script(before["properties"], tool), mode)
                verify_properties(self.adb.props(serial), before["properties"])
                rollback = "Rollback verified."
            except Exception as rollback_exc:
                rollback = f"Rollback could not be verified: {rollback_exc}"
            raise ChangerError(f"Change failed: {exc}\n{rollback}\nRecovery backup: {path}") from exc
        self.log(f"Read-back verified for {len(properties)} properties.")
        return {"backup": str(path), "count": len(properties), "serial": serial,
                "scope": "runtime only; cached app values may require an app restart"}
