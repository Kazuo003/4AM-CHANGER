"""4AM Changer — offline desktop profile studio and direct ADB controller."""
from __future__ import annotations

import logging
import os
import queue
import secrets
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from logging.handlers import RotatingFileHandler

from core import (ASSETS, VERSION, Adb, Catalog, ChangerError, Engine, property_script,
                  read_json, storage_dir, validate_profile, write_json)

BG = "#0F172A"
CARD = "#1B2538"
FG = "#F1F5F9"
MUTED = "#A6B5CC"
ACCENT = "#86EFAC"


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"4AM Changer  ·  {VERSION}")
        self.geometry("1210x860")
        self.minsize(1020, 760)
        self.configure(bg=BG)
        self.folder = storage_dir()
        self.catalog = Catalog()
        self.events = queue.Queue()
        self.busy = False
        self.profile = None
        self.current_props = {}
        self.device_rows = []
        self.filtered = []
        self.action_buttons = []
        self.config_path = self.folder / "settings.json"
        self.logger = logging.getLogger("4am")
        self.logger.setLevel(logging.INFO)
        handler = RotatingFileHandler(self.folder / "activity.log", maxBytes=1_000_000,
                                      backupCount=2, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        self.logger.addHandler(handler)
        default_adb = str(ASSETS / "adb" / "adb.exe")
        try:
            saved = read_json(self.config_path)
            default_adb = saved.get("adb", default_adb) if isinstance(saved, dict) else default_adb
        except (OSError, ValueError, ChangerError):
            pass
        self.adb_path = tk.StringVar(value=default_adb)
        self.status = tk.StringVar(value="Offline workspace  ·  No device selected")
        self.brand = tk.StringVar(value="All brands")
        self.search = tk.StringVar()
        self.model = tk.StringVar()
        self.country = tk.StringVar()
        self.carrier = tk.StringVar(value="Random carrier")
        self.serial_enabled = tk.BooleanVar(value=True)
        self.hardware_enabled = tk.BooleanVar(value=False)
        self.network_enabled = tk.BooleanVar(value=False)
        self.fingerprint = tk.StringVar()
        self.profile_label = tk.StringVar(value="No profile generated")
        self.summary = tk.StringVar(value="Choose a model, then generate a profile. Nothing is applied automatically.")
        self.country_details = tk.StringVar()
        self._style()
        self._layout()
        self.brand.trace_add("write", lambda *_: self.filter_models())
        self.search.trace_add("write", lambda *_: self.filter_models())
        self.country.trace_add("write", lambda *_: self.filter_carriers())
        self.filter_models()
        self.country_map = {}
        for network in self.catalog.networks:
            code = network["name"]
            country = self.catalog.country_by_code.get(code, {})
            self.country_map[f"{code} — {country.get('name', code)}"] = network
        self.country_combo["values"] = sorted(self.country_map)
        self.country.set(next((k for k in self.country_map if k.startswith("VN —")), next(iter(self.country_map), "")))
        self.load_backups()
        self.log(f"Workspace ready: {len(self.catalog.devices):,} models, {len(self.catalog.networks)} carrier regions.")
        self.after(100, self.drain_events)
        self.protocol("WM_DELETE_WINDOW", self.close)

    def _style(self):
        self.option_add("*Font", ("Segoe UI", 10))
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(".", background=BG, foreground=FG, font=("Segoe UI", 10), borderwidth=0)
        style.configure("TFrame", background=BG)
        style.configure("Card.TFrame", background=CARD)
        style.configure("TLabel", background=BG, foreground=FG)
        style.configure("Muted.TLabel", foreground=MUTED)
        style.configure("Card.TLabel", background=CARD)
        style.configure("Header.TLabel", font=("Segoe UI Semibold", 26))
        style.configure("Section.TLabel", font=("Segoe UI Semibold", 13))
        style.configure("TButton", background="#2E3E58", foreground=FG, padding=(13, 9), borderwidth=1)
        style.map("TButton", background=[("active", "#3A5073"), ("disabled", "#202B3F")],
                  foreground=[("disabled", "#7F8DA4")])
        style.configure("Primary.TButton", background=ACCENT, foreground="#10251A", font=("Segoe UI Semibold", 10))
        style.map("Primary.TButton", background=[("active", "#BBF7D0"), ("disabled", "#345F49")],
                  foreground=[("disabled", "#9CAB9F")])
        style.configure("TEntry", fieldbackground="#111C2F", foreground=FG, padding=7, insertcolor=FG)
        style.configure("TCombobox", fieldbackground="#111C2F", foreground=FG, background="#2E3E58", padding=6)
        style.map("TCombobox", fieldbackground=[("readonly", "#111C2F")], foreground=[("readonly", FG)])
        self.option_add("*TCombobox*Listbox.background", "#111C2F")
        self.option_add("*TCombobox*Listbox.foreground", FG)
        self.option_add("*TCombobox*Listbox.selectBackground", "#334D71")
        style.configure("TCheckbutton", background=BG, foreground=FG, padding=(0, 6))
        style.map("TCheckbutton", background=[("active", BG)])
        style.configure("Treeview", background="#111C2F", fieldbackground="#111C2F", foreground=FG,
                        rowheight=29, borderwidth=0)
        style.configure("Treeview.Heading", background="#26344B", foreground=MUTED, padding=7,
                        font=("Segoe UI Semibold", 9))
        style.map("Treeview", background=[("selected", "#304C6A")], foreground=[("selected", "#FFFFFF")])
        style.configure("TNotebook", background=BG, tabmargins=(0, 7, 0, 10))
        style.configure("TNotebook.Tab", background=CARD, foreground=MUTED, padding=(19, 10))
        style.map("TNotebook.Tab", background=[("selected", "#30435D")], foreground=[("selected", FG)])
        style.configure("Horizontal.TProgressbar", background=ACCENT, troughcolor=CARD)

    def button(self, parent, label, command, primary=False, **pack):
        button = ttk.Button(parent, text=label, command=command,
                            style="Primary.TButton" if primary else "TButton")
        button.pack(**pack)
        self.action_buttons.append(button)
        return button

    def table(self, parent, columns, height=8):
        box = ttk.Frame(parent)
        box.pack(fill="both", expand=True)
        tree = ttk.Treeview(box, columns=[c[0] for c in columns], show="headings", height=height,
                            selectmode="browse")
        for key, label, width in columns:
            tree.heading(key, text=label)
            tree.column(key, width=width, minwidth=70)
        scroll = ttk.Scrollbar(box, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        return tree

    def _layout(self):
        shell = ttk.Frame(self, padding=(25, 20))
        shell.pack(fill="both", expand=True)
        header = ttk.Frame(shell)
        header.pack(fill="x")
        ttk.Label(header, text="4AM / CHANGER", style="Header.TLabel").pack(side="left")
        ttk.Label(header, text="INDEPENDENT ENGINE  /  OFFLINE FIRST", style="Muted.TLabel").pack(side="right")
        ttk.Label(shell, text="Gchanger device catalog + Pchanger country metadata. One independent ADB engine.",
                  style="Muted.TLabel").pack(anchor="w", pady=(3, 14))
        conn = ttk.Frame(shell)
        conn.pack(fill="x")
        ttk.Label(conn, text="ADB executable", style="Muted.TLabel").pack(side="left", padx=(0, 10))
        ttk.Entry(conn, textvariable=self.adb_path).pack(side="left", fill="x", expand=True)
        self.button(conn, "Browse", self.browse_adb, side="left", padx=(8, 0))
        self.button(conn, "Refresh devices", self.scan, side="left", padx=(8, 0))
        devicebox = ttk.Frame(shell)
        devicebox.pack(fill="x", pady=(12, 9))
        self.devices_tree = self.table(devicebox, [("serial", "ADB SERIAL", 280), ("model", "REPORTED MODEL", 330),
                                                    ("state", "CONNECTION", 180)], height=3)
        self.devices_tree.bind("<<TreeviewSelect>>", self.select_device)
        bar = ttk.Frame(shell)
        bar.pack(fill="x", pady=(0, 10))
        ttk.Label(bar, textvariable=self.status, style="Muted.TLabel").pack(side="left")
        self.button(bar, "Inspect device", self.inspect, side="right")
        self.button(bar, "Backup properties", self.backup, side="right", padx=(0, 8))
        self.tabs = ttk.Notebook(shell)
        self.tabs.pack(fill="both", expand=True)
        studio = ttk.Frame(self.tabs, padding=(0, 8))
        backups = ttk.Frame(self.tabs, padding=(0, 8))
        live = ttk.Frame(self.tabs, padding=(0, 8))
        activity = ttk.Frame(self.tabs, padding=(0, 8))
        self.tabs.add(studio, text="Profile studio")
        self.tabs.add(backups, text="Backups & recovery")
        self.tabs.add(live, text="Device inspection")
        self.tabs.add(activity, text="Activity")
        studio.columnconfigure(0, weight=0, minsize=342)
        studio.columnconfigure(1, weight=1)
        studio.rowconfigure(0, weight=1)
        left = ttk.Frame(studio, padding=(0, 0, 22, 0))
        left.grid(row=0, column=0, sticky="nsew")
        right = ttk.Frame(studio)
        right.grid(row=0, column=1, sticky="nsew")
        ttk.Label(left, text="01 / Build a profile", style="Section.TLabel").pack(anchor="w", pady=(0, 10))
        ttk.Label(left, text="Brand", style="Muted.TLabel").pack(anchor="w")
        ttk.Combobox(left, textvariable=self.brand, values=["All brands"] + self.catalog.brands,
                     state="readonly").pack(fill="x", pady=(3, 8))
        ttk.Label(left, text="Search model or marketing name", style="Muted.TLabel").pack(anchor="w")
        ttk.Entry(left, textvariable=self.search).pack(fill="x", pady=(3, 8))
        self.model_combo = ttk.Combobox(left, textvariable=self.model, state="readonly", width=38)
        self.model_combo.pack(fill="x", pady=(0, 7))
        ttk.Label(left, text="Carrier region", style="Muted.TLabel").pack(anchor="w")
        self.country_combo = ttk.Combobox(left, textvariable=self.country, state="readonly")
        self.country_combo.pack(fill="x", pady=(3, 7))
        self.carrier_combo = ttk.Combobox(left, textvariable=self.carrier, state="readonly")
        self.carrier_combo.pack(fill="x", pady=(0, 4))
        ttk.Label(left, textvariable=self.country_details, style="Muted.TLabel", wraplength=325).pack(anchor="w", pady=(0, 5))
        ttk.Checkbutton(left, text="Randomize reported serial properties", variable=self.serial_enabled).pack(anchor="w")
        ttk.Checkbutton(left, text="Include carrier properties (not real SIM)", variable=self.network_enabled).pack(anchor="w")
        ttk.Checkbutton(left, text="Include board / platform / SoC properties", variable=self.hardware_enabled).pack(anchor="w")
        ttk.Label(left, text="Fingerprint override · optional", style="Muted.TLabel").pack(anchor="w", pady=(5, 0))
        ttk.Entry(left, textvariable=self.fingerprint).pack(fill="x", pady=(3, 5))
        ttk.Label(left, text="Blank preserves the current fingerprint and OS version.",
                  style="Muted.TLabel", wraplength=320).pack(anchor="w")
        buildbar = ttk.Frame(left)
        buildbar.pack(fill="x", pady=(13, 0))
        self.button(buildbar, "Generate", self.generate, primary=True, side="left", fill="x", expand=True)
        self.button(buildbar, "Random model", self.randomize, side="left", padx=(7, 0))
        ttk.Label(right, text="02 / Review before applying", style="Section.TLabel").pack(anchor="w", pady=(0, 8))
        ttk.Label(right, textvariable=self.profile_label, font=("Segoe UI Semibold", 12)).pack(anchor="w")
        ttk.Label(right, textvariable=self.summary, style="Muted.TLabel", wraplength=670).pack(anchor="w", pady=(4, 11))
        self.props_tree = self.table(right, [("key", "PROPERTY · DOUBLE-CLICK TO EDIT", 265), ("value", "PROFILE VALUE", 310)], 9)
        self.props_tree.bind("<Double-1>", self.edit_property)
        actions = ttk.Frame(right)
        actions.pack(fill="x", pady=(12, 0))
        self.button(actions, "Import", self.import_profile, side="left")
        self.button(actions, "Export", self.export_profile, side="left", padx=(7, 0))
        self.button(actions, "Preview script", self.preview, side="left", padx=(7, 0))
        self.button(actions, "Apply to device", self.apply, primary=True, side="right")
        ttk.Label(right, text="Runtime properties only. Apply creates a backup, verifies read-back, and attempts rollback on failure.\n"
                  "No reboot, app-data wipe, firmware flash, IMEI write, or real SIM/GPS change.",
                  style="Muted.TLabel", wraplength=670).pack(anchor="w", pady=(11, 0))
        backupbar = ttk.Frame(backups)
        backupbar.pack(fill="x", pady=(0, 10))
        ttk.Label(backupbar, text="Property snapshots · not app or firmware backups", style="Section.TLabel").pack(side="left")
        self.button(backupbar, "Reload", self.load_backups, side="right")
        self.backups_tree = self.table(backups, [("time", "CREATED (UTC)", 260), ("serial", "ADB SERIAL", 240),
                                               ("model", "MODEL", 230), ("count", "PROPERTIES", 100)])
        backupactions = ttk.Frame(backups)
        backupactions.pack(fill="x", pady=(12, 0))
        self.button(backupactions, "Restore selected", self.restore_selected, primary=True, side="left")
        self.button(backupactions, "Restore JSON file", self.restore_file, side="left", padx=(8, 0))
        self.button(backupactions, "Open local storage", self.open_storage, side="right")
        ttk.Label(backups, text="Restore requires the same ADB serial. Every restore also saves an undo snapshot.",
                  style="Muted.TLabel").pack(anchor="w", pady=(12, 0))
        ttk.Label(live, text="Last inspected values · click Inspect device to refresh", style="Section.TLabel").pack(anchor="w", pady=(0, 12))
        self.live_tree = self.table(live, [("key", "PROPERTY", 400), ("value", "READ FROM DEVICE", 600)])
        self.activity = tk.Text(activity, bg="#0B1220", fg=FG, insertbackground=FG, font=("Consolas", 10),
                                relief="flat", padx=12, pady=12, wrap="word", state="disabled")
        self.activity.pack(fill="both", expand=True)
        foot = ttk.Frame(shell)
        foot.pack(fill="x", pady=(10, 0))
        self.progress = ttk.Progressbar(foot, mode="indeterminate", length=110)
        self.progress.pack(side="right")
        ttk.Label(foot, text=f"v{VERSION}  /  No original apps or licensing servers required", style="Muted.TLabel").pack(side="left")

    def log(self, message):
        self.logger.info(message)
        self.activity.configure(state="normal")
        self.activity.insert("end", message + "\n")
        if int(self.activity.index("end-1c").split(".")[0]) > 1200:
            self.activity.delete("1.0", "200.0")
        self.activity.see("end")
        self.activity.configure(state="disabled")

    def run_task(self, label, work, done):
        if self.busy:
            return
        self.busy = True
        for button in self.action_buttons:
            button.state(["disabled"])
        self.progress.start(15)
        self.status.set(label + "…")
        self.log(label)

        def worker():
            try:
                self.events.put(("done", done, work()))
            except Exception as exc:
                self.events.put(("error", label, str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def drain_events(self):
        try:
            while True:
                event = self.events.get_nowait()
                if event[0] == "log":
                    self.log(event[1])
                    continue
                self.busy = False
                self.progress.stop()
                for button in self.action_buttons:
                    button.state(["!disabled"])
                self.status.set("Ready · operation completed" if event[0] == "done" else "Operation failed · see Activity")
                if event[0] == "done":
                    try:
                        event[1](event[2])
                    except Exception as exc:
                        self.log(f"Result display failed: {exc}")
                        messagebox.showerror("4AM Changer", str(exc), parent=self)
                else:
                    self.log(f"{event[1]} failed: {event[2]}")
                    messagebox.showerror("4AM Changer", event[2], parent=self)
                self.load_backups()
        except queue.Empty:
            pass
        self.after(100, self.drain_events)

    def adb(self):
        path = self.adb_path.get().strip()
        if not path:
            raise ChangerError("Choose an ADB executable first.")
        return Adb(path)

    def engine(self):
        return Engine(self.adb(), self.folder, lambda msg: self.events.put(("log", msg)))

    def serial(self):
        selected = self.devices_tree.selection()
        if not selected:
            raise ChangerError("Refresh devices and select an authorized device first.")
        row = self.device_rows[int(selected[0])]
        if row["state"] != "device":
            raise ChangerError(f"Device is {row['state']}. Unlock the phone and authorize USB debugging.")
        return row["serial"]

    def select_device(self, _=None):
        if not self.busy:
            try:
                self.status.set("Selected: " + self.serial() + " · root checked only when applying")
            except ChangerError as exc:
                self.status.set(str(exc))

    def browse_adb(self):
        path = filedialog.askopenfilename(parent=self, title="Choose adb.exe", filetypes=[("ADB executable", "adb.exe"), ("All files", "*.*")])
        if path:
            self.adb_path.set(path)
            write_json(self.config_path, {"adb": path})

    def scan(self):
        try:
            adb = self.adb()
        except ChangerError as exc:
            return messagebox.showerror("ADB", str(exc), parent=self)
        previous = None
        try:
            previous = self.serial()
        except ChangerError:
            pass

        def done(rows):
            self.device_rows = rows
            self.devices_tree.delete(*self.devices_tree.get_children())
            for index, row in enumerate(rows):
                self.devices_tree.insert("", "end", iid=str(index), values=(row["serial"], row["model"], row["state"]))
            selected = next((str(i) for i, r in enumerate(rows) if r["serial"] == previous), None)
            if selected is None and len(rows) == 1:
                selected = "0"
            if selected is not None:
                self.devices_tree.selection_set(selected)
            self.status.set(f"{len(rows)} device(s) found" if rows else "No ADB devices · connect USB and enable USB debugging")
            self.log(f"Scan completed: {len(rows)} device(s).")
        self.run_task("Scanning ADB devices", adb.devices, done)

    def inspect(self):
        try:
            serial, adb = self.serial(), self.adb()
        except ChangerError as exc:
            return messagebox.showinfo("Device", str(exc), parent=self)

        def done(props):
            self.current_props = props
            self.live_tree.delete(*self.live_tree.get_children())
            for key, value in sorted(props.items()):
                self.live_tree.insert("", "end", values=(key, value))
            self.tabs.select(2)
            self.status.set(f"{serial} · {props.get('ro.product.model', '?')} · Android {props.get('ro.build.version.release', '?')}")
            self.log(f"Inspected {len(props)} properties on {serial}. No changes made.")
        self.run_task("Reading device properties", lambda: adb.props(serial), done)

    def filter_models(self):
        brand = self.brand.get()
        self.filtered = self.catalog.find("" if brand == "All brands" else brand, self.search.get())
        labels = [f"{d['model']} — {d.get('modelName', '')}" for d in self.filtered]
        self.model_combo["values"] = labels
        self.model_combo.current(0) if labels else self.model.set("")

    def filter_carriers(self):
        network = getattr(self, "country_map", {}).get(self.country.get())
        carriers = [c.strip() for c in network.get("carrier", "").split(",") if c.strip()] if network else []
        self.carrier_combo["values"] = ["Random carrier"] + carriers
        self.carrier.set("Random carrier")
        country = self.catalog.country_by_code.get(network["name"], {}) if network else {}
        self.country_details.set(f"Capital: {country.get('capital') or '—'}  ·  {', '.join(country.get('timezones', [])[:1])}\nCountry metadata only; GPS is not changed.")

    def generate(self):
        try:
            index = self.model_combo.current()
            if index < 0 or index >= len(self.filtered):
                raise ChangerError("No model matches the current search.")
            network = self.country_map.get(self.country.get()) if self.network_enabled.get() else None
            carrier = self.carrier.get()
            profile = self.catalog.make_profile(self.filtered[index], network,
                "" if carrier == "Random carrier" else carrier,
                self.serial_enabled.get(), self.hardware_enabled.get(), self.fingerprint.get())
            self.show_profile(profile)
            self.log("Generated profile: " + profile["label"] + ". Preview only; not applied.")
        except (ChangerError, ValueError) as exc:
            messagebox.showerror("Profile", str(exc), parent=self)

    def randomize(self):
        if not self.filtered:
            return messagebox.showinfo("Models", "No models match the current filter.", parent=self)
        self.model_combo.current(secrets.randbelow(len(self.filtered)))
        self.generate()

    def show_profile(self, profile):
        self.profile = validate_profile(profile)
        self.profile_label.set(self.profile["label"])
        self.props_tree.delete(*self.props_tree.get_children())
        for key, value in self.profile["properties"].items():
            self.props_tree.insert("", "end", iid=key, values=(key, value))
        count = len(self.profile["properties"])
        self.summary.set(f"{count} properties staged locally. Catalog settings take effect only after Generate; apply uses this exact table.")

    def need_profile(self):
        if not self.profile:
            raise ChangerError("Generate or import a profile first.")
        return validate_profile(self.profile)

    def edit_property(self, event):
        if self.busy or not self.profile:
            return
        row = self.props_tree.identify_row(event.y)
        if not row:
            return
        value = simpledialog.askstring("Edit property", row, initialvalue=self.profile["properties"][row], parent=self)
        if value is not None:
            changed = {**self.profile, "properties": {**self.profile["properties"], row: value}}
            try:
                self.show_profile(changed)
            except ChangerError as exc:
                messagebox.showerror("Property", str(exc), parent=self)

    def import_profile(self):
        path = filedialog.askopenfilename(parent=self, title="Import 4AM profile", filetypes=[("JSON profile", "*.json")])
        if path:
            try:
                self.show_profile(read_json(Path(path)))
                self.log("Imported profile: " + self.profile["label"])
            except (OSError, ValueError, ChangerError) as exc:
                messagebox.showerror("Import", str(exc), parent=self)

    def export_profile(self):
        try:
            profile = self.need_profile()
        except ChangerError as exc:
            return messagebox.showinfo("Profile", str(exc), parent=self)
        path = filedialog.asksaveasfilename(parent=self, title="Export profile", defaultextension=".json",
            initialdir=self.folder, initialfile="4am-profile.json", filetypes=[("JSON profile", "*.json")])
        if path:
            try:
                write_json(Path(path), profile)
                self.log("Exported profile: " + path)
            except OSError as exc:
                messagebox.showerror("Export", str(exc), parent=self)

    def preview(self):
        try:
            profile = self.need_profile()
        except ChangerError as exc:
            return messagebox.showinfo("Profile", str(exc), parent=self)
        window = tk.Toplevel(self)
        window.title("Dry-run · script preview only")
        window.geometry("850x510")
        window.configure(bg=BG)
        ttk.Label(window, text="Not executed. Apply wraps this script in a root shell, with backup and read-back verification.",
                  padding=12, wraplength=800).pack(fill="x")
        text = tk.Text(window, bg="#0B1220", fg=FG, font=("Consolas", 10), wrap="word", padx=15, pady=15)
        text.pack(fill="both", expand=True)
        text.insert("end", "# Runtime changes, not a persistent boot script\n" + property_script(profile["properties"]))
        text.configure(state="disabled")
        ttk.Button(window, text="Close", command=window.destroy).pack(pady=10)

    def backup(self):
        try:
            serial, engine = self.serial(), self.engine()
        except ChangerError as exc:
            return messagebox.showinfo("Device", str(exc), parent=self)
        def done(path):
            self.log("Saved property snapshot: " + str(path))
            messagebox.showinfo("Backup saved", str(path), parent=self)
        self.run_task("Backing up supported properties", lambda: engine.backup(serial), done)

    def apply(self):
        try:
            profile, serial, engine = self.need_profile(), self.serial(), self.engine()
        except ChangerError as exc:
            return messagebox.showinfo("Apply", str(exc), parent=self)
        if not messagebox.askyesno("Apply runtime properties?",
            f"Device: {serial}\nProfile: {profile['label']}\nProperties: {len(profile['properties'])}\n\n"
            "Requires root + Magisk resetprop. A local backup is saved first.\n"
            "Existing apps may cache old values. No reboot or data wipe is performed.\n\nApply now?", parent=self):
            return
        self.run_task("Applying and verifying runtime properties", lambda: engine.apply(serial, profile), self.changed)

    def changed(self, result):
        self.current_props = {}
        self.live_tree.delete(*self.live_tree.get_children())
        self.status.set(f"Verified {result['count']} properties on {result['serial']} · runtime only")
        self.log(f"Change verified. Recovery snapshot: {result['backup']}")
        messagebox.showinfo("Read-back verified", f"Verified {result['count']} properties.\n\n"
            f"Backup: {result['backup']}\n\nValues are runtime-only; cached app values may require an app restart.", parent=self)

    def load_backups(self):
        self.backup_paths = {}
        self.backups_tree.delete(*self.backups_tree.get_children())
        for path in sorted((self.folder / "backups").glob("*.json"), reverse=True):
            try:
                data = read_json(path)
                if not isinstance(data, dict) or data.get("schema") != "4am.backup.v1":
                    continue
                key = str(len(self.backup_paths))
                self.backup_paths[key] = path
                self.backups_tree.insert("", "end", iid=key, values=(str(data.get("created_at", ""))[:26],
                    data.get("serial", ""), data.get("device", {}).get("ro.product.model", ""), len(data.get("properties", {}))))
            except (OSError, ValueError, TypeError, AttributeError, ChangerError):
                continue

    def restore_selected(self):
        selected = self.backups_tree.selection()
        if not selected:
            return messagebox.showinfo("Restore", "Select a saved snapshot first.", parent=self)
        self.restore(self.backup_paths[selected[0]])

    def restore_file(self):
        path = filedialog.askopenfilename(parent=self, title="Choose property backup", filetypes=[("4AM backup", "*.json")])
        if path:
            self.restore(Path(path))

    def restore(self, path):
        from core import validate_backup
        try:
            serial, engine = self.serial(), self.engine()
            data = validate_backup(read_json(path), serial)
        except (ChangerError, OSError, ValueError) as exc:
            return messagebox.showerror("Restore", str(exc), parent=self)
        if messagebox.askyesno("Restore snapshot?", f"Restore {len(data['properties'])} properties to {serial}?\n\n"
                "An undo snapshot will be saved before restoring.", parent=self):
            self.run_task("Restoring and verifying properties", lambda: engine.restore(serial, data), self.changed)

    def open_storage(self):
        if os.name == "nt":
            os.startfile(self.folder)

    def close(self):
        if self.busy:
            messagebox.showinfo("Operation running", "Wait for the device operation and recovery checks to finish before closing.", parent=self)
            return
        try:
            write_json(self.config_path, {"adb": self.adb_path.get()})
        except OSError:
            pass
        self.destroy()


def main():
    try:
        app = App()
    except Exception as exc:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("4AM Changer could not start", str(exc), parent=root)
        root.destroy()
        raise SystemExit(1)
    if "--smoke-test" in sys.argv:
        app.update()
        app.generate()
        app.update()
        assert app.profile and len(app.catalog.devices) == 2528
        app.destroy()
        print("GUI smoke test passed: catalog loaded, layout rendered, profile generated.")
        return
    app.mainloop()


if __name__ == "__main__":
    main()
