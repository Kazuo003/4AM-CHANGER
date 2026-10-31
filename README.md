# 4AM Changer 1.0

Independent Windows desktop runtime-property changer. It does **not** launch,
inject into, or require Pchanger or Gchanger, their servers, or their license patches.
The original folders are left untouched.

## Run

### Full Windows package

Download [4AM-Changer-1.0.0-Windows-x64-full.zip](packages/4AM-Changer-1.0.0-Windows-x64-full.zip),
extract the entire ZIP, then open `4AM-Changer/4AM-Changer.exe`.
The full package contains the Windows executable, Python/Tk runtime, ADB and its
DLLs/notices, all reference catalogs, and a `source` folder with the complete source.
It does not require either original changer application or a Python installation.
Do not run the executable from inside the ZIP or separate it from `_internal`.

The ZIP's SHA-256 is published in `packages/SHA256SUMS.txt`; the packaged-file
inventory and hashes are in `packages/release-manifest.json`.

Double-click `run.bat`, or open `dist/4AM-Changer/4AM-Changer.exe` after building.
Keep the entire `dist/4AM-Changer` folder together: the executable uses its bundled
Python/Tk runtime, reference catalogs, and Android platform tools.
No Python installation is needed for the packaged build.

For source execution: Python 3.11+ with Tk, then `python app.py`. No third-party
Python dependencies are required to run from source.

### GitHub source checkout

The repository includes source, reference JSON catalogs, and Android platform-tool
executables/DLLs/notices in `assets/adb`. Generated build folders, local settings,
logs, and device backups are excluded; the explicit full distribution ZIP is in
`packages`. Profile generation and tests do not require a connected Android device.
Use **Browse** in the UI if you prefer an independently installed `adb.exe`.

The optional pinned packaging dependency is in `requirements-build.txt`.

## What is combined

- **Gchanger reference data:** 2,528 device presets and 64 carrier-region entries.
- **Pchanger reference data:** country names, capitals, and timezone metadata.
- **New implementation:** desktop interface, searchable presets, serial-property
  randomization, JSON profile import/export, editable preview, direct ADB operations,
  local property snapshots, restore, read-back verification, and rollback attempts.
- `assets/provenance.json` records local input paths and SHA-256 checksums.
  `import_assets.py` can reproduce the imports while the original folders remain present.
- No cracked executable, JAR, license service, JavaFX, .NET app, cookies, or
  authorization data is bundled. Android platform-tool notices are retained.

This is a standalone **v1 implementation**, not complete feature parity with either
original application or a port of their proprietary phone-side firmware/modules.

## Use

1. Connect an Android device with USB debugging enabled and authorize the computer.
2. Click **Refresh devices** and select its ADB serial. Offline/unauthorized devices
   remain visible but cannot be targeted for operations.
3. Use **Inspect device** to read current properties. **Backup properties** saves a
   snapshot of supported properties without requiring root or changing the phone.
4. Select a brand/model or search by marketing name. **Random model** stays within
   the current brand/search filter. Carrier region and carrier are optional.
5. Click **Generate**. The table is the exact staged profile. Double-click a row to
   edit it. Changing catalog selections alone does not alter an already staged profile.
6. **Preview script** is a dry run: no ADB command is executed. Profiles can be
   exported/imported as `4am.profile.v1` JSON.
7. **Apply to device** requires confirmation, root access via `su` or root adbd,
   and an installed working Magisk `resetprop` on the phone. Root authorization may
   prompt on the phone. The app never installs root or flashes firmware.
8. Before any write, the engine saves a durable local snapshot. After applying,
   it reads back and compares every targeted property. On failure, it attempts and
   verifies rollback. If disconnected, recovery cannot be guaranteed; the error
   includes the snapshot path. Reconnect the same device and restore that snapshot.
9. **Backups & recovery** restores only snapshots belonging to the selected ADB
   serial. Restores also save a pre-restore undo snapshot.

## Exact scope

- Changes are runtime Android **property values**, implemented with `resetprop -n`.
  The property-service trigger bypass is documented by the
  [Magisk resetprop reference](https://topjohnwu.github.io/Magisk/tools.html#resetprop).
  No system/vendor files, persistent boot module, or persistent settings are written.
- Cached `Build` values in already-running apps/framework processes may not update.
  A successful getprop read-back does not prove every app sees the new value.
  Other ROM services may overwrite runtime values. Reboot normally reloads the
  device's original property configuration; no reboot is initiated by the app.
- Serial randomization changes `ro.serialno` and `ro.boot.serialno` **properties**,
  not the physical serial, modem identifiers, or USB serial hardware.
- Carrier options change `gsm.*` **properties**, not the real SIM, modem subscription,
  phone number, or network service. Telephony services may replace these values.
- Board/platform/SoC properties are optional and off by default. They do not change
  the hardware and can make reported values inconsistent with the actual device.
- Every imported device has an empty fingerprint field. The app therefore preserves
  the current fingerprint unless the operator explicitly supplies an override.
  It does not fabricate an OS version, SDK level, or fingerprint from a model name.
- Imported presets are unverified reference data, not certified hardware matches.
  Some source entries contain inconsistent brands/models. Brand filtering ignores
  case, but the original property values are preserved for review rather than
  silently corrected. Inspect and edit the staged profile before applying it.
- Country/timezone records are reference metadata only. No GPS/location change,
  timezone change, IMEI/baseband write, Android ID change, app-data wiping, app backup,
  filesystem backup, device attestation bypass, or `/proc` emulation is implemented.

## Local files

`userdata` next to the running executable (or next to the source app) contains
settings, rotating activity logs, and JSON property backups. If that directory is
not writable, storage falls back to `%LOCALAPPDATA%/4AM-Changer`.
Backups are plain JSON and can contain device-identifying property values.
**Open local storage** opens the folder actually in use.

The UI has no automatic scans, outbound network client, online licensing, background
device writes, app installation, administrator requirement, or automatic phone reboot.
ADB manages its normal local transport daemon when a device action is requested.

## Build and test

The build requires Python, Tk, and an already installed PyInstaller:

```powershell
python -m unittest discover -s tests -v
python app.py --smoke-test
python -m PyInstaller --noconfirm --clean --onedir --windowed --name 4AM-Changer --add-data 'assets;assets' app.py
```

Run `python package_release.py` after building to create the complete ZIP and
checksum manifests, or run `build.ps1` to test, build, and package in one step.
The packager explicitly selects source/reference files and excludes local runtime
data, settings, logs, and caches. Every ZIP member is checked against its SHA-256.

Tests use a fake device, exercising validation,
all device presets, profile generation, shell quoting, ADB error handling,
pre-write backup ordering, failed writes, failed verification, rollback,
rollback failure, deletion of originally absent properties, and undo restore.
No connected phone is written to by tests or the GUI smoke test.

Real-phone apply/restore compatibility must still be tested on the target ROM.
