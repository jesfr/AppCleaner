"""
AppCleaner — Gestionnaire d'applications Windows
Interface moderne pour lister, filtrer et désinstaller les apps inutilisées.
"""

import customtkinter as ctk
import tkinter as tk
import tkinter.ttk as ttk
from tkinter import messagebox
import winreg
import os
import subprocess
import threading
import shutil
import sys
import ctypes
from datetime import datetime, timedelta

# ─── Thème ───────────────────────────────────────────────────────────────────
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

ACCENT  = "#3B82F6"
DANGER  = "#EF4444"
SUCCESS = "#22C55E"
WARNING = "#F59E0B"
MUTED   = "#6B7280"

BG_DARK  = "#0F172A"
BG_HDR   = "#111827"
BG_BAR   = "#1F2937"
BG_EVEN  = "#1A2332"
BG_ODD   = "#1E2A3A"

# ─── Éditeurs / apps système à exclure ───────────────────────────────────────
SYSTEM_PUBLISHERS = {
    "microsoft corporation", "microsoft", "windows", "intel corporation",
    "intel", "amd", "nvidia corporation", "nvidia", "realtek semiconductor",
    "realtek", "qualcomm", "broadcom", "marvell", "mediatek",
    "advanced micro devices", "vmware", "oracle corporation",
}
SYSTEM_NAME_PREFIXES = (
    "microsoft visual c++", "microsoft .net", "windows sdk", "directx",
    "microsoft update", "windows update", "microsoft edge",
    "windows desktop runtime", "asp.net", "microsoft office",
    ".net core", ".net framework", "vcredist", "vc_redist",
    "windows app runtime", "microsoft windows desktop",
)


# ─── Utilitaires ─────────────────────────────────────────────────────────────
def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def elevate():
    ctypes.windll.shell32.ShellExecuteW(
        None, "runas", sys.executable, " ".join(f'"{a}"' for a in sys.argv), None, 1
    )
    sys.exit(0)


def fmt_size(n: int) -> str:
    for unit in ("o", "Ko", "Mo", "Go"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "o" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.2f} To"


def folder_size(path: str) -> int:
    total = 0
    try:
        with os.scandir(path) as it:
            for entry in it:
                try:
                    if entry.is_file(follow_symlinks=False):
                        total += entry.stat().st_size
                    elif entry.is_dir(follow_symlinks=False):
                        total += folder_size(entry.path)
                except (PermissionError, OSError):
                    pass
    except (PermissionError, OSError):
        pass
    return total


def last_exe_access(path: str) -> datetime | None:
    latest = None
    try:
        for root, _, files in os.walk(path):
            for f in files:
                if f.lower().endswith(".exe"):
                    try:
                        t = datetime.fromtimestamp(os.path.getatime(os.path.join(root, f)))
                        if latest is None or t > latest:
                            latest = t
                    except (OSError, ValueError):
                        pass
    except (PermissionError, OSError):
        pass
    return latest


def is_system(name: str, publisher: str, sys_component: int, uninstall: str) -> bool:
    if sys_component == 1:
        return True
    name_l = (name or "").lower().strip()
    pub_l  = (publisher or "").lower().strip()
    if pub_l in SYSTEM_PUBLISHERS:
        return True
    if any(name_l.startswith(p) for p in SYSTEM_NAME_PREFIXES):
        return True
    return False


# ─── Lecture registre ─────────────────────────────────────────────────────────
REG_PATHS = [
    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
    (winreg.HKEY_CURRENT_USER,  r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
]


def _reg_val(key, name, default=""):
    try:
        return winreg.QueryValueEx(key, name)[0]
    except Exception:
        return default


def scan_registry() -> list[dict]:
    apps: list[dict] = []
    seen: set[str] = set()

    for hive, path in REG_PATHS:
        try:
            root = winreg.OpenKey(hive, path)
        except Exception:
            continue
        try:
            count = winreg.QueryInfoKey(root)[0]
        except Exception:
            winreg.CloseKey(root)
            continue

        for i in range(count):
            try:
                sub_name = winreg.EnumKey(root, i)
                sub = winreg.OpenKey(root, sub_name)
            except Exception:
                continue
            try:
                name = _reg_val(sub, "DisplayName")
                if not name or name in seen:
                    continue
                publisher = _reg_val(sub, "Publisher")
                location  = _reg_val(sub, "InstallLocation").strip().rstrip("\\")
                uninstall = _reg_val(sub, "UninstallString")
                quiet     = _reg_val(sub, "QuietUninstallString")
                sys_comp  = _reg_val(sub, "SystemComponent", 0)
                version   = _reg_val(sub, "DisplayVersion")

                if is_system(name, publisher, sys_comp, uninstall):
                    continue

                seen.add(name)
                apps.append({
                    "name":      name,
                    "publisher": publisher,
                    "version":   version,
                    "location":  location,
                    "uninstall": uninstall,
                    "quiet":     quiet,
                    "portable":  not bool(uninstall),
                    "size":      0,
                    "last_used": None,
                })
            except Exception:
                pass
            finally:
                try:
                    winreg.CloseKey(sub)
                except Exception:
                    pass
        try:
            winreg.CloseKey(root)
        except Exception:
            pass

    apps.sort(key=lambda a: a["name"].lower())
    return apps


# ─── Désinstallation ──────────────────────────────────────────────────────────
def build_uninstall_cmd(app: dict) -> str | None:
    if app.get("quiet"):
        return app["quiet"]
    cmd = app.get("uninstall", "")
    if not cmd:
        return None
    cl = cmd.lower()
    if "msiexec" in cl:
        cmd = cmd.replace("/I{", "/X{").replace("/i{", "/X{")
        if "/quiet" not in cl and "/qn" not in cl:
            cmd += " /quiet /norestart"
        return cmd
    if ".exe" in cl:
        if not any(f in cl for f in ("/s", "/silent", "/quiet", "/uninstall")):
            cmd += " /S"
        return cmd
    return cmd


def do_uninstall(app: dict) -> tuple[bool, str]:
    if app.get("portable"):
        loc = app.get("location", "")
        if loc and os.path.isdir(loc):
            try:
                shutil.rmtree(loc)
                return True, "Dossier supprimé"
            except Exception as e:
                return False, str(e)
        return False, "Dossier introuvable"

    cmd = build_uninstall_cmd(app)
    if not cmd:
        return False, "Pas de commande de désinstallation"
    try:
        r = subprocess.run(cmd, shell=True, timeout=180, capture_output=True)
        ok = r.returncode in (0, 3010, 1605, 1614)
        return ok, f"Code retour : {r.returncode}"
    except subprocess.TimeoutExpired:
        return False, "Délai dépassé (>3 min)"
    except Exception as e:
        return False, str(e)


# ═══════════════════════════════════════════════════════════════════════════════
# TABLE  (ttk.Treeview stylisé dark — alignement natif garanti)
# ═══════════════════════════════════════════════════════════════════════════════

def _apply_treeview_style():
    style = ttk.Style()
    style.theme_use("clam")

    style.configure("App.Treeview",
        background=BG_EVEN,
        foreground="#E5E7EB",
        fieldbackground=BG_EVEN,
        borderwidth=0,
        rowheight=38,
        font=("Segoe UI", 11),
    )
    style.configure("App.Treeview.Heading",
        background=BG_BAR,
        foreground="#9CA3AF",
        font=("Segoe UI", 11, "bold"),
        relief="flat",
        borderwidth=0,
        padding=(8, 8),
    )
    style.map("App.Treeview",
        background=[("selected", "#1D3461"), ("!selected", BG_EVEN)],
        foreground=[("selected", "#E5E7EB")],
    )
    style.map("App.Treeview.Heading",
        background=[("active", "#374151")],
    )
    style.layout("App.Treeview", [("Treeview.treearea", {"sticky": "nswe"})])

    style.configure("Dark.Vertical.TScrollbar",
        background="#1F2937", troughcolor=BG_DARK,
        arrowcolor="#6B7280", borderwidth=0, arrowsize=13,
    )
    style.map("Dark.Vertical.TScrollbar",
        background=[("active", "#374151"), ("!active", "#1F2937")],
    )


class AppTable(ctk.CTkFrame):
    COLS    = ("sel", "name", "publisher", "size", "last_used", "location")
    HEADERS = ("",    "Application", "Éditeur", "Taille", "Dernière utilisation", "Emplacement")
    WIDTHS  = (36,    280,           165,       100,      155,                    300)

    def __init__(self, parent, on_change, **kw):
        super().__init__(parent, corner_radius=0, fg_color=BG_DARK, **kw)
        self._on_change = on_change
        self._apps: list[dict] = []
        self._checked: set[str] = set()
        self._build()

    def _build(self):
        _apply_treeview_style()

        self.tree = ttk.Treeview(
            self, columns=self.COLS, show="headings",
            style="App.Treeview", selectmode="none",
        )

        for col, hdr, w in zip(self.COLS, self.HEADERS, self.WIDTHS):
            self.tree.heading(col, text=hdr)
            stretch = (col == "location")
            anchor  = "center" if col in ("sel", "size") else "w"
            self.tree.column(col, width=w, minwidth=max(w // 2, 36),
                             anchor=anchor, stretch=stretch)

        vsb = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview,
                            style="Dark.Vertical.TScrollbar")
        self.tree.configure(yscrollcommand=vsb.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self.tree.tag_configure("odd",     background=BG_ODD)
        self.tree.tag_configure("even",    background=BG_EVEN)
        self.tree.tag_configure("checked", foreground="#60A5FA", font=("Segoe UI", 11, "bold"))
        self.tree.tag_configure("success", foreground=SUCCESS)
        self.tree.tag_configure("warning", foreground=WARNING)
        self.tree.tag_configure("danger",  foreground=DANGER)
        self.tree.tag_configure("muted",   foreground=MUTED)

        self.tree.bind("<Button-1>", self._on_click)

    def populate(self, apps: list[dict]):
        self._apps = apps
        self._checked.clear()
        self.tree.delete(*self.tree.get_children())

        for i, app in enumerate(apps):
            last     = app.get("last_used")
            size_txt = fmt_size(app["size"]) if app.get("size") else "—"
            pub      = (app.get("publisher") or "—")[:30]
            name     = app["name"] + ("  [portable]" if app.get("portable") else "")

            if last:
                days   = (datetime.now() - last).days
                lu_txt = "aujourd'hui" if days == 0 else f"il y a {days} j"
                if days < 30:
                    time_tag = "success"
                elif days < 90:
                    time_tag = "warning"
                else:
                    time_tag = "danger"
            else:
                lu_txt   = "inconnu"
                time_tag = "muted"

            loc  = app.get("location") or "—"
            base = "odd" if i % 2 else "even"

            self.tree.insert("", "end", iid=str(i),
                values=("☐", name, pub, size_txt, lu_txt, loc),
                tags=(base, time_tag),
            )

        self._on_change()

    def _on_click(self, event):
        iid = self.tree.identify_row(event.y)
        if not iid or self.tree.identify_region(event.x, event.y) != "cell":
            return
        idx  = int(iid)
        name = self._apps[idx]["name"]

        vals = list(self.tree.item(iid, "values"))
        tags = list(self.tree.item(iid, "tags"))
        base = "odd" if idx % 2 else "even"

        last     = self._apps[idx].get("last_used")
        if last:
            days = (datetime.now() - last).days
            time_tag = "success" if days < 30 else ("warning" if days < 90 else "danger")
        else:
            time_tag = "muted"

        if name in self._checked:
            self._checked.discard(name)
            vals[0] = "☐"
            self.tree.item(iid, values=vals, tags=(base, time_tag))
        else:
            self._checked.add(name)
            vals[0] = "☑"
            self.tree.item(iid, values=vals, tags=(base, time_tag, "checked"))

        self._on_change()

    def get_selected(self) -> list[dict]:
        return [a for a in self._apps if a["name"] in self._checked]

    def select_all(self, value: bool):
        self._checked.clear()
        for i, app in enumerate(self._apps):
            iid  = str(i)
            vals = list(self.tree.item(iid, "values"))
            last = app.get("last_used")
            if last:
                days = (datetime.now() - last).days
                time_tag = "success" if days < 30 else ("warning" if days < 90 else "danger")
            else:
                time_tag = "muted"
            base = "odd" if i % 2 else "even"
            if value:
                self._checked.add(app["name"])
                vals[0] = "☑"
                self.tree.item(iid, values=vals, tags=(base, time_tag, "checked"))
            else:
                vals[0] = "☐"
                self.tree.item(iid, values=vals, tags=(base, time_tag))
        self._on_change()


# ═══════════════════════════════════════════════════════════════════════════════
# DIALOGUES
# ═══════════════════════════════════════════════════════════════════════════════

class UninstallDialog(ctk.CTkToplevel):
    def __init__(self, parent, apps: list[dict]):
        super().__init__(parent)
        self.title("Confirmer la désinstallation")
        self.geometry("520x430")
        self.resizable(False, False)
        self.grab_set()
        self.result = False
        self._build(apps)

    def _build(self, apps):
        total_size = sum(a.get("size", 0) for a in apps)
        count = len(apps)

        ctk.CTkLabel(self, text=f"Désinstaller {count} application(s) ?",
                     font=("Segoe UI", 17, "bold")).pack(pady=(24, 6))
        ctk.CTkLabel(self, text=f"Espace récupéré estimé : {fmt_size(total_size)}",
                     font=("Segoe UI", 13), text_color=SUCCESS).pack(pady=4)

        box = ctk.CTkTextbox(self, height=210, font=("Segoe UI", 11), wrap="word")
        box.pack(fill="x", padx=24, pady=12)
        lines = ["  • " + a["name"] + (" [portable]" if a.get("portable") else "") for a in apps]
        box.insert("end", "\n".join(lines))
        box.configure(state="disabled")

        if any(a.get("portable") for a in apps):
            ctk.CTkLabel(self,
                text="⚠  Les apps portables seront supprimées définitivement (dossier entier).",
                font=("Segoe UI", 10), text_color=WARNING, wraplength=480).pack(pady=(0, 8))

        frm = ctk.CTkFrame(self, fg_color="transparent")
        frm.pack(pady=12)
        ctk.CTkButton(frm, text="Annuler", width=110,
            fg_color="#374151", hover_color="#4B5563",
            command=self.destroy).pack(side="left", padx=10)
        ctk.CTkButton(frm, text="Désinstaller", width=140,
            fg_color=DANGER, hover_color="#DC2626",
            command=self._confirm).pack(side="left", padx=10)

    def _confirm(self):
        self.result = True
        self.destroy()


class ProgressDialog(ctk.CTkToplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("Désinstallation en cours…")
        self.geometry("520x270")
        self.resizable(False, False)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", lambda: None)

        self.lbl = ctk.CTkLabel(self, text="", font=("Segoe UI", 12))
        self.lbl.pack(pady=(24, 4))

        self.bar = ctk.CTkProgressBar(self, width=460)
        self.bar.pack(pady=8)
        self.bar.set(0)

        self.lbl_n = ctk.CTkLabel(self, text="0 / 0", font=("Segoe UI", 11), text_color=MUTED)
        self.lbl_n.pack()

        self.log = ctk.CTkTextbox(self, height=100, font=("Consolas", 10))
        self.log.pack(fill="x", padx=20, pady=12)

    def update(self, done: int, total: int, name: str):
        self.bar.set(done / total if total else 0)
        self.lbl.configure(text=f"Désinstallation de {name}…")
        self.lbl_n.configure(text=f"{done} / {total}")

    def log_line(self, line: str):
        self.log.insert("end", line + "\n")
        self.log.see("end")


class ResultDialog(ctk.CTkToplevel):
    def __init__(self, parent, ok: int, fail: int, freed: int, on_rescan):
        super().__init__(parent)
        self.title("Résultat")
        self.geometry("420x320")
        self.resizable(False, False)
        self.grab_set()

        icon = "✅" if fail == 0 else ("⚠️" if ok > 0 else "❌")
        ctk.CTkLabel(self, text=f"{icon}  Désinstallation terminée",
                     font=("Segoe UI", 17, "bold")).pack(pady=(28, 8))
        ctk.CTkLabel(self, text=f"✓  {ok} application(s) désinstallée(s)",
                     font=("Segoe UI", 13), text_color=SUCCESS).pack(pady=4)
        if fail:
            ctk.CTkLabel(self, text=f"✗  {fail} échec(s)",
                         font=("Segoe UI", 13), text_color=DANGER).pack(pady=4)
        ctk.CTkLabel(self, text=f"💾  Espace libéré : {fmt_size(freed)}",
                     font=("Segoe UI", 15, "bold"), text_color=ACCENT).pack(pady=12)
        if fail:
            ctk.CTkLabel(self,
                text="Certains désinstalleurs peuvent s'être lancés en arrière-plan.",
                font=("Segoe UI", 10), text_color=MUTED, justify="center").pack(pady=4)

        frm = ctk.CTkFrame(self, fg_color="transparent")
        frm.pack(pady=12)
        ctk.CTkButton(frm, text="Rescanner", width=120,
            command=lambda: (self.destroy(), on_rescan())).pack(side="left", padx=8)
        ctk.CTkButton(frm, text="Fermer", width=100,
            fg_color="#374151", hover_color="#4B5563",
            command=self.destroy).pack(side="left", padx=8)


class UpdateDialog(ctk.CTkToplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("Mise à jour des applications")
        self.geometry("680x520")
        self.resizable(True, True)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", lambda: None)
        self._proc = None
        self._build()
        threading.Thread(target=self._run, daemon=True).start()

    def _build(self):
        ctk.CTkLabel(self, text="⬆  Mise à jour en cours…",
                     font=("Segoe UI", 16, "bold")).pack(pady=(22, 2))
        ctk.CTkLabel(self, text="winget upgrade --all --include-unknown",
                     font=("Consolas", 10), text_color=MUTED).pack(pady=(0, 10))

        self._bar = ctk.CTkProgressBar(self, width=580, mode="indeterminate")
        self._bar.pack(pady=(0, 10))
        self._bar.start()

        self._log = ctk.CTkTextbox(self, font=("Consolas", 10), wrap="word")
        self._log.pack(fill="both", expand=True, padx=20, pady=(0, 8))

        self._btn = ctk.CTkButton(self, text="Fermer", width=110, state="disabled",
            fg_color="#374151", hover_color="#4B5563", command=self.destroy)
        self._btn.pack(pady=(0, 16))

    def _run(self):
        try:
            self._proc = subprocess.Popen(
                ["winget", "upgrade", "--all", "--include-unknown"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            updated = 0
            for raw in self._proc.stdout:
                line = raw.rstrip()
                if not line:
                    continue
                self.after(0, lambda l=line: (
                    self._log.insert("end", l + "\n"),
                    self._log.see("end"),
                ))
                if any(k in line.lower() for k in (
                    "successfully installed", "successfully upgraded",
                    "mise à jour", "installé avec succès",
                )):
                    updated += 1
            self._proc.wait()
            self.after(0, lambda: self._done(updated, self._proc.returncode))
        except FileNotFoundError:
            self.after(0, lambda: self._error(
                "winget introuvable.\nInstallez « App Installer » depuis le Microsoft Store."))
        except Exception as e:
            self.after(0, lambda: self._error(str(e)))

    def _done(self, updated: int, rc: int):
        self._bar.stop()
        self._bar.configure(mode="determinate")
        self._bar.set(1)
        sep = "\n" + "─" * 55 + "\n"
        msg = f"{sep}✅  Terminé — {updated} mise(s) à jour effectuée(s)\n" \
              if rc in (0, -1073741510) else f"{sep}⚠️  Terminé (code {rc})\n"
        self._log.insert("end", msg)
        self._log.see("end")
        self._btn.configure(state="normal")
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.title("Mise à jour terminée")

    def _error(self, msg: str):
        self._bar.stop()
        self._log.insert("end", f"\n❌  Erreur : {msg}\n")
        self._log.see("end")
        self._btn.configure(state="normal")
        self.protocol("WM_DELETE_WINDOW", self.destroy)


# ═══════════════════════════════════════════════════════════════════════════════
# FENÊTRE PRINCIPALE
# ═══════════════════════════════════════════════════════════════════════════════

class AppCleaner(ctk.CTk):
    DAYS_MAP = {
        "Toutes":   0,
        "30 jours": 30,
        "60 jours": 60,
        "90 jours": 90,
        "6 mois":   180,
        "1 an":     365,
        "2 ans":    730,
    }

    def __init__(self):
        super().__init__()
        self.title("AppCleaner")
        self.geometry("1300x820")
        self.minsize(960, 600)

        self._all_apps:      list[dict] = []
        self._filtered_apps: list[dict] = []

        self._build_ui()
        self._start_scan()

    # ── UI ────────────────────────────────────────────────────────────────────
    def _build_ui(self):
        self.grid_rowconfigure(2, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # En-tête
        hdr = ctk.CTkFrame(self, height=64, corner_radius=0, fg_color=BG_HDR)
        hdr.grid(row=0, column=0, sticky="ew")
        hdr.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(hdr, text="🗑  AppCleaner",
                     font=("Segoe UI", 20, "bold")).grid(row=0, column=0, padx=20, pady=14)
        ctk.CTkLabel(hdr, text="Gérez et nettoyez vos applications Windows",
                     font=("Segoe UI", 11), text_color=MUTED
                     ).grid(row=0, column=1, padx=8, sticky="w")

        self._btn_update = ctk.CTkButton(
            hdr, text="⬆  Mettre à jour tout",
            width=180, height=36, font=("Segoe UI", 12, "bold"),
            fg_color="#059669", hover_color="#047857",
            command=self._launch_update)
        self._btn_update.grid(row=0, column=2, padx=12, pady=14)

        self._lbl_status = ctk.CTkLabel(hdr, text="",
                                        font=("Segoe UI", 11), text_color=MUTED)
        self._lbl_status.grid(row=0, column=3, padx=20)

        # Barre de filtres
        bar = ctk.CTkFrame(self, height=56, corner_radius=0, fg_color=BG_BAR)
        bar.grid(row=1, column=0, sticky="ew")
        bar.grid_columnconfigure(99, weight=1)

        col = 0
        ctk.CTkLabel(bar, text="Recherche :", font=("Segoe UI", 12)
                     ).grid(row=0, column=col, padx=(16, 4), pady=12)
        col += 1
        self._search_var = tk.StringVar()
        self._search_var.trace_add("write", lambda *_: self._apply_filters())
        ctk.CTkEntry(bar, textvariable=self._search_var, width=200,
                     placeholder_text="Nom d'application…"
                     ).grid(row=0, column=col, padx=(0, 16), pady=12)

        col += 1
        ctk.CTkLabel(bar, text="Non utilisée depuis :", font=("Segoe UI", 12)
                     ).grid(row=0, column=col, padx=(0, 4), pady=12)
        col += 1
        self._days_combo = ctk.CTkComboBox(
            bar, values=list(self.DAYS_MAP.keys()), width=130,
            command=lambda _: self._apply_filters())
        self._days_combo.set("Toutes")
        self._days_combo.grid(row=0, column=col, padx=(0, 16), pady=12)

        col += 1
        ctk.CTkLabel(bar, text="Trier par :", font=("Segoe UI", 12)
                     ).grid(row=0, column=col, padx=(0, 4), pady=12)
        col += 1
        self._sort_combo = ctk.CTkComboBox(
            bar, values=["Nom A→Z", "Taille ↓", "Taille ↑", "Dernière utilisation"],
            width=180, command=lambda _: self._apply_filters())
        self._sort_combo.set("Nom A→Z")
        self._sort_combo.grid(row=0, column=col, padx=(0, 16), pady=12)

        col += 1
        self._show_portable_var = tk.BooleanVar(value=True)
        ctk.CTkCheckBox(bar, text="Afficher portables",
                        variable=self._show_portable_var,
                        command=self._apply_filters
                        ).grid(row=0, column=col, padx=(0, 16), pady=12)

        col += 99
        self._select_all_var = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(bar, text="Tout sélectionner",
                        variable=self._select_all_var,
                        command=self._toggle_all
                        ).grid(row=0, column=col, padx=16, pady=12)

        # Table
        self._table = AppTable(self, on_change=self._update_bottom)
        self._table.grid(row=2, column=0, sticky="nsew", padx=0, pady=0)

        # Barre d'actions
        bot = ctk.CTkFrame(self, height=64, corner_radius=0, fg_color=BG_HDR)
        bot.grid(row=3, column=0, sticky="ew")
        bot.grid_columnconfigure(1, weight=1)

        self._lbl_count = ctk.CTkLabel(bot, text="", font=("Segoe UI", 12))
        self._lbl_count.grid(row=0, column=0, padx=20, pady=16)

        self._lbl_selected = ctk.CTkLabel(bot, text="",
                                          font=("Segoe UI", 11), text_color=MUTED)
        self._lbl_selected.grid(row=0, column=1, padx=8, sticky="w")

        self._btn_uninstall = ctk.CTkButton(
            bot, text="Désinstaller la sélection",
            width=220, height=42, font=("Segoe UI", 13, "bold"),
            fg_color=DANGER, hover_color="#DC2626",
            state="disabled", command=self._ask_uninstall)
        self._btn_uninstall.grid(row=0, column=2, padx=20, pady=10)

    # ── Scan ──────────────────────────────────────────────────────────────────
    def _start_scan(self):
        self._lbl_status.configure(text="Lecture du registre…")
        threading.Thread(target=self._scan_worker, daemon=True).start()

    def _scan_worker(self):
        apps = scan_registry()
        total = len(apps)
        for i, app in enumerate(apps):
            self.after(0, lambda i=i, t=total:
                       self._lbl_status.configure(text=f"Analyse {i+1}/{t}…"))
            loc = app.get("location", "")
            if loc and os.path.isdir(loc):
                app["size"]      = folder_size(loc)
                app["last_used"] = last_exe_access(loc)
        self._all_apps = apps
        self.after(0, self._on_scan_done)

    def _on_scan_done(self):
        n  = len(self._all_apps)
        sz = sum(a.get("size", 0) for a in self._all_apps)
        self._lbl_status.configure(text=f"{n} applications — {fmt_size(sz)} au total")
        self._apply_filters()

    # ── Filtres ───────────────────────────────────────────────────────────────
    def _apply_filters(self, *_):
        search   = self._search_var.get().lower()
        days     = self.DAYS_MAP.get(self._days_combo.get(), 0)
        sort_key = self._sort_combo.get()
        portals  = self._show_portable_var.get()

        filtered = self._all_apps.copy()

        if search:
            filtered = [a for a in filtered if
                        search in a["name"].lower() or
                        search in (a.get("publisher") or "").lower()]

        if not portals:
            filtered = [a for a in filtered if not a.get("portable")]

        if days > 0:
            cutoff = datetime.now() - timedelta(days=days)
            filtered = [a for a in filtered
                        if a.get("last_used") is None or a["last_used"] < cutoff]

        if sort_key == "Taille ↓":
            filtered.sort(key=lambda a: a.get("size", 0), reverse=True)
        elif sort_key == "Taille ↑":
            filtered.sort(key=lambda a: a.get("size", 0))
        elif sort_key == "Dernière utilisation":
            filtered.sort(key=lambda a: a.get("last_used") or datetime.min)
        else:
            filtered.sort(key=lambda a: a["name"].lower())

        self._filtered_apps = filtered
        self._table.populate(filtered)
        n  = len(filtered)
        sz = sum(a.get("size", 0) for a in filtered)
        self._lbl_count.configure(text=f"{n} application(s) — {fmt_size(sz)}")

    def _toggle_all(self):
        self._table.select_all(self._select_all_var.get())

    def _update_bottom(self):
        sel = self._table.get_selected()
        if sel:
            sz = sum(a.get("size", 0) for a in sel)
            self._lbl_selected.configure(
                text=f"{len(sel)} sélectionnée(s) · {fmt_size(sz)} à libérer")
            self._btn_uninstall.configure(state="normal")
        else:
            self._lbl_selected.configure(text="")
            self._btn_uninstall.configure(state="disabled")

    # ── Désinstallation ───────────────────────────────────────────────────────
    def _ask_uninstall(self):
        sel = self._table.get_selected()
        if not sel:
            return
        dlg = UninstallDialog(self, sel)
        self.wait_window(dlg)
        if dlg.result:
            self._run_uninstall(sel)

    def _run_uninstall(self, apps: list[dict]):
        self._btn_uninstall.configure(state="disabled")
        prog = ProgressDialog(self)

        def worker():
            ok, fail, freed = 0, 0, 0
            total = len(apps)
            for i, app in enumerate(apps):
                prog.after(0, lambda n=app["name"], d=i, t=total: prog.update(d, t, n))
                size_before = app.get("size", 0)
                success, msg = do_uninstall(app)
                if success:
                    ok += 1; freed += size_before
                    prog.after(0, lambda n=app["name"]: prog.log_line(f"✓  {n}"))
                else:
                    fail += 1
                    prog.after(0, lambda n=app["name"], m=msg: prog.log_line(f"✗  {n}  ({m})"))
            prog.after(0, lambda: self._show_result(prog, ok, fail, freed))

        threading.Thread(target=worker, daemon=True).start()

    def _show_result(self, prog, ok, fail, freed):
        prog.grab_release()
        prog.destroy()
        ResultDialog(self, ok, fail, freed, self._rescan)

    def _launch_update(self):
        UpdateDialog(self)

    def _rescan(self):
        self._all_apps.clear()
        self._filtered_apps.clear()
        self._table.populate([])
        self._start_scan()


# ─── Point d'entrée ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    if not is_admin():
        answer = messagebox.askyesno(
            "Droits administrateur",
            "AppCleaner fonctionne mieux avec les droits administrateur "
            "(certaines désinstallations peuvent échouer sans).\n\n"
            "Relancer en tant qu'administrateur ?"
        )
        if answer:
            elevate()

    app = AppCleaner()
    app.mainloop()
