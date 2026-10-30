import json
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import (ALLOWED_PROPERTIES, ASSETS, Adb, Catalog, ChangerError, Engine,
                  parse_getprop, property_script, read_json, validate_backup,
                  validate_profile, validate_properties, verify_properties, write_json)


def profile(properties=None):
    return {"schema": "4am.profile.v1", "label": "Test profile",
            "properties": properties or {"ro.product.model": "QA Model"}}


class FakeAdb:
    def __init__(self, props=None):
        self.values = {"ro.product.model": "Original", "ro.product.brand": "OriginalBrand"} if props is None else dict(props)
        self.executed = []
        self.fail_once = False
        self.fail_always = False
        self.ignore_first = False
        self.folder = None

    def props(self, serial):
        return dict(self.values)

    def root_mode(self, serial):
        return "su"

    def resetprop(self, serial, mode):
        return "resetprop"

    def root(self, serial, script, mode):
        # Verify the pre-change file is already durable at first write.
        if self.folder is not None:
            assert list((self.folder / "backups").glob("*.json")), "mutation occurred before backup"
        self.executed.append(script)
        if self.fail_always:
            raise ChangerError("disconnected")
        if self.fail_once:
            self.fail_once = False
            self.values["ro.product.model"] = "partially written"
            raise ChangerError("simulated mid-script failure")
        if self.ignore_first:
            self.ignore_first = False
            return ""
        for line in script.splitlines()[1:]:
            tokens = shlex.split(line)
            if tokens[0] == "if":
                index = tokens.index("--delete")
                self.values.pop(tokens[index + 1].rstrip(";"), None)
            else:
                self.values[tokens[2]] = tokens[3]
        return ""


class ValidationTests(unittest.TestCase):
    def test_profile_roundtrip(self):
        clean = validate_profile(profile())
        self.assertEqual(clean["properties"]["ro.product.model"], "QA Model")

    def test_reject_bad_profiles(self):
        for value in ([], {}, {"schema": "wrong"}, {**profile(), "label": ""},
                      {**profile(), "properties": []}, profile({"persist.sys.timezone": "UTC"}),
                      profile({"ro.product.model": "bad\nline"}), profile({"ro.product.model": None}),
                      profile({"ro.product.model": "x" * 1025}),
                      profile({"gsm.operator.alpha": "x" * 92})):
            with self.subTest(value=str(value)[:80]), self.assertRaises(ChangerError):
                validate_profile(value)

    def test_empty_and_nonstring_rejected(self):
        for properties in ({}, [], {"ro.product.model": 123}, {"ro.product.model": "a\x00b"}):
            with self.assertRaises(ChangerError):
                validate_properties(properties)

    def test_absent_only_valid_for_backup(self):
        self.assertEqual(validate_properties({"ro.product.model": None}, True), {"ro.product.model": None})
        with self.assertRaises(ChangerError):
            validate_properties({"ro.product.model": None})

    def test_same_device_restore_only(self):
        backup = {"schema": "4am.backup.v1", "serial": "A", "properties": {"ro.serialno": None}}
        self.assertEqual(validate_backup(backup, "A")["serial"], "A")
        with self.assertRaises(ChangerError):
            validate_backup(backup, "B")

    def test_shell_quoting(self):
        value = "Jack's model; $(touch /tmp/nope) `id` \"quoted\" & more"
        script = property_script({"ro.product.model": value})
        self.assertEqual(shlex.split(script.splitlines()[1]), ["resetprop", "-n", "ro.product.model", value])

    def test_delete_script(self):
        script = property_script({"ro.product.model": None})
        self.assertIn("--delete ro.product.model", script)
        self.assertIn("grep -F -q", script)

    def test_tool_not_injectable(self):
        with self.assertRaises(ChangerError):
            property_script({"ro.product.model": "a"}, "resetprop; bad")

    def test_parse_and_verify(self):
        props = parse_getprop("noise\n[ro.product.model]: [Hello [world]]\n[empty]: []\n")
        self.assertEqual(props, {"ro.product.model": "Hello [world]", "empty": ""})
        verify_properties(props, {"ro.product.model": "Hello [world]", "missing": None})
        with self.assertRaises(ChangerError):
            verify_properties(props, {"empty": None})
        with self.assertRaises(ChangerError):
            verify_properties(props, {"ro.product.model": "Wrong"})

    def test_atomic_json_unicode(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "profile.json"
            data = {"value": "Việt Nam"}
            write_json(path, data)
            self.assertEqual(read_json(path), data)
            self.assertEqual(len(list(Path(folder).iterdir())), 1)


class CatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = Catalog()

    def test_catalogs_loaded(self):
        self.assertEqual(len(self.catalog.devices), 2528)
        self.assertEqual(len(self.catalog.networks), 64)
        self.assertGreater(len(self.catalog.countries), 200)
        self.assertEqual(self.catalog.country_by_code["VN"]["name"], "Vietnam")

    def test_filter(self):
        rows = self.catalog.find("Samsung", "SM-G")
        self.assertTrue(rows)
        self.assertTrue(all(d["brand"].casefold() == "samsung" and "sm-g" in d["model"].casefold() for d in rows))
        self.assertEqual(len(self.catalog.brands), len({b.casefold() for b in self.catalog.brands}))
        self.assertEqual(self.catalog.find("does-not-exist"), [])

    def test_every_model_generates_valid_profile(self):
        for device in self.catalog.devices:
            result = self.catalog.make_profile(device, hardware=True)
            self.assertTrue(result["properties"]["ro.product.model"])
            self.assertNotIn("ro.build.version.release", result["properties"])
            self.assertNotIn("ro.build.version.sdk", result["properties"])
            self.assertNotIn("ro.build.fingerprint", result["properties"])

    def test_country_network(self):
        vn = next(n for n in self.catalog.networks if n["name"] == "VN")
        result = self.catalog.make_profile(self.catalog.devices[0], vn, "Viettel-45204")
        self.assertEqual(result["properties"]["gsm.operator.numeric"], "45204")
        self.assertEqual(result["properties"]["gsm.sim.operator.alpha"], "Viettel")
        with self.assertRaises(ChangerError):
            self.catalog.make_profile(self.catalog.devices[0], vn, "madeup-12345")

    def test_base_profile_is_conservative(self):
        result = self.catalog.make_profile(self.catalog.devices[0], random_serial=False)
        self.assertEqual(len(result["properties"]), 5)
        self.assertNotIn("ro.product.board", result["properties"])
        self.assertNotIn("gsm.operator.alpha", result["properties"])

    def test_serial_randomization(self):
        a = self.catalog.make_profile(self.catalog.devices[0])
        b = self.catalog.make_profile(self.catalog.devices[0])
        self.assertNotEqual(a["properties"]["ro.serialno"], b["properties"]["ro.serialno"])
        self.assertEqual(a["properties"]["ro.serialno"], a["properties"]["ro.boot.serialno"])


class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.adb = FakeAdb()
        self.adb.folder = self.folder
        self.engine = Engine(self.adb, self.folder)

    def test_success_saves_backup_before_mutation(self):
        result = self.engine.apply("SERIAL", profile())
        self.assertEqual(self.adb.values["ro.product.model"], "QA Model")
        saved = read_json(Path(result["backup"]))
        self.assertEqual(saved["properties"], {"ro.product.model": "Original"})

    def test_failure_rolls_back(self):
        self.adb.fail_once = True
        with self.assertRaisesRegex(ChangerError, "Rollback verified"):
            self.engine.apply("SERIAL", profile())
        self.assertEqual(self.adb.values["ro.product.model"], "Original")
        self.assertEqual(len(self.adb.executed), 2)

    def test_readback_mismatch_rolls_back(self):
        self.adb.ignore_first = True
        with self.assertRaisesRegex(ChangerError, "Read-back mismatch"):
            self.engine.apply("SERIAL", profile())
        self.assertEqual(self.adb.values["ro.product.model"], "Original")

    def test_failed_recovery_reports_backup(self):
        self.adb.fail_always = True
        with self.assertRaisesRegex(ChangerError, "Recovery backup") as cm:
            self.engine.apply("SERIAL", profile())
        self.assertIn("Rollback could not be verified", str(cm.exception))
        self.assertTrue(list((self.folder / "backups").glob("*.json")))

    def test_backup_disk_failure_prevents_mutation(self):
        with patch("core.write_json", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.engine.apply("SERIAL", profile())
        self.assertEqual(self.adb.executed, [])

    def test_invalid_profile_prevents_adb(self):
        with self.assertRaises(ChangerError):
            self.engine.apply("SERIAL", profile({"ro.evil": "bad"}))
        self.assertEqual(self.adb.executed, [])

    def test_restore_and_restore_of_undo(self):
        initial = dict(self.adb.values)
        result = self.engine.apply("SERIAL", profile({"ro.product.model": "QA", "ro.serialno": "NEW"}))
        restored = self.engine.restore("SERIAL", read_json(Path(result["backup"])))
        self.assertEqual(self.adb.values, initial)
        self.engine.restore("SERIAL", read_json(Path(restored["backup"])))
        self.assertEqual(self.adb.values["ro.product.model"], "QA")
        self.assertEqual(self.adb.values["ro.serialno"], "NEW")

    def test_cross_device_restore_never_executes(self):
        backup = {"schema": "4am.backup.v1", "serial": "OTHER", "properties": {"ro.product.model": "Old"}}
        with self.assertRaises(ChangerError):
            self.engine.restore("SERIAL", backup)
        self.assertEqual(self.adb.executed, [])

    def test_manual_backup_is_read_only(self):
        saved = read_json(self.engine.backup("SERIAL"))
        self.assertEqual(set(saved["properties"]), ALLOWED_PROPERTIES)
        self.assertEqual(self.adb.executed, [])


class AdbTests(unittest.TestCase):
    @patch("core.subprocess.run")
    def test_serial_is_explicit_and_shell_false(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, "okay", "")
        self.assertEqual(Adb("adb.exe").run(["shell", "getprop"], "SERIAL"), "okay")
        self.assertEqual(run.call_args.args[0], ["adb.exe", "-s", "SERIAL", "shell", "getprop"])
        self.assertFalse(run.call_args.kwargs["shell"])

    def test_invalid_serial_rejected(self):
        for serial in ("", "-d", "a b", "a\nb"):
            with self.assertRaises(ChangerError):
                Adb("adb.exe").run(["shell", "getprop"], serial)

    @patch("core.subprocess.run", side_effect=FileNotFoundError)
    def test_missing_adb(self, _):
        with self.assertRaisesRegex(ChangerError, "not found"):
            Adb("missing").devices()

    @patch("core.subprocess.run", side_effect=subprocess.TimeoutExpired("adb", 30))
    def test_timeout(self, _):
        with self.assertRaisesRegex(ChangerError, "timed out"):
            Adb("adb").devices()

    @patch("core.subprocess.run")
    def test_nonzero_exit(self, run):
        run.return_value = subprocess.CompletedProcess([], 1, "", "device unauthorized")
        with self.assertRaisesRegex(ChangerError, "unauthorized"):
            Adb("adb").devices()

    @patch.object(Adb, "run")
    def test_devices_parser(self, run):
        run.return_value = "List of devices attached\nA device product:a model:Pixel_6 transport_id:1\nB unauthorized usb:2\nC offline\n"
        self.assertEqual(Adb("adb").devices(), [
            {"serial": "A", "state": "device", "model": "Pixel 6"},
            {"serial": "B", "state": "unauthorized", "model": ""},
            {"serial": "C", "state": "offline", "model": ""}])

    @patch.object(Adb, "run")
    def test_su_script_quoting(self, run):
        script = property_script({"ro.product.model": "A's $(id)"})
        Adb("adb").root("SERIAL", script, "su")
        args, serial = run.call_args.args
        self.assertEqual(serial, "SERIAL")
        self.assertEqual(shlex.split(args[1]), ["su", "-c", script])


if __name__ == "__main__":
    unittest.main()
