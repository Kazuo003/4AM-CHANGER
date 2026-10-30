"""Copy only reference catalogs and platform tools, never patched app engines."""
from pathlib import Path
import hashlib
import shutil

from core import write_json

HERE = Path(__file__).resolve().parent
WORKSPACE = HERE.parent
G = WORKSPACE / "Gchanger_v623" / "Gchanger_v623"
P = WORKSPACE / "[12]-Pchanger-S9-4.4" / "[12]-Pchanger-S9-4.4"


def main():
    sources = {
        "devices.json": G / "getdata_gdevices.dec.json",
        "networks.json": G / "getdata_networks.dec.json",
        "countries.json": P / "data" / "info" / "locationCountry.json",
    }
    target = HERE / "assets"
    target.mkdir(parents=True, exist_ok=True)
    manifest = {"engine": "independent implementation; no vendor app binaries required", "files": {}}
    for name, source in sources.items():
        shutil.copy2(source, target / name)
        manifest["files"][name] = {"source": str(source.relative_to(WORKSPACE)),
                                    "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}
    adb_dir = target / "adb"
    adb_dir.mkdir(exist_ok=True)
    for name in ("adb.exe", "AdbWinApi.dll", "AdbWinUsbApi.dll", "libwinpthread-1.dll", "NOTICE.txt", "source.properties"):
        source = G / "data" / "adb" / name
        shutil.copy2(source, adb_dir / name)
        manifest["files"]["adb/" + name] = {"source": str(source.relative_to(WORKSPACE)),
                                              "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}
    write_json(target / "provenance.json", manifest)
    print("Imported device, network, country catalogs and standalone ADB with notices.")


if __name__ == "__main__":
    main()
