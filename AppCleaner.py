"""AppCleaner v2.0 — Gestionnaire d'applications Windows"""

import customtkinter as ctk
import tkinter as tk
import tkinter.ttk as ttk
from tkinter import messagebox
import winreg, os, subprocess, threading, shutil, sys, ctypes
import json, struct, codecs, hashlib, string
from datetime import datetime, timedelta

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

ACCENT  = "#3B82F6"; DANGER  = "#EF4444"; SUCCESS = "#22C55E"
WARNING = "#F59E0B"; MUTED   = "#6B7280"
BG_DARK = "#0F172A"; BG_HDR  = "#111827"; BG_BAR  = "#1F2937"
BG_EVEN = "#1A2332"; BG_ODD  = "#1E2A3A"

PALETTE = [
    "#1D4ED8","#7C3AED","#BE185D","#B45309","#065F46","#0E7490",
    "#991B1B","#4338CA","#047857","#92400E","#1E40AF","#5B21B6",
    "#831843","#78350F","#064E3B","#155E75","#7F1D1D","#166534",
]

SYSTEM_PUBLISHERS = {
    "microsoft corporation","microsoft","windows","intel corporation","intel",
    "amd","nvidia corporation","nvidia","realtek semiconductor","realtek",
    "qualcomm","broadcom","marvell","mediatek","advanced micro devices",
    "vmware","oracle corporation",
}
SYSTEM_PREFIXES = (
    "microsoft visual c++","microsoft .net","windows sdk","directx",
    "microsoft update","windows update","microsoft edge",
    "windows desktop runtime","asp.net","microsoft office",
    ".net core",".net framework","vcredist","vc_redist",
    "windows app runtime","microsoft windows desktop",
)

# ── Utilitaires ───────────────────────────────────────────────────────────────
def is_admin():
    try: return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except: return False

def elevate():
    ctypes.windll.shell32.ShellExecuteW(
        None,"runas",sys.executable," ".join(f'"{a}"' for a in sys.argv),None,1)
    sys.exit(0)

def fmt_size(n):
    for u in ("o","Ko","Mo","Go"):
        if n < 1024: return f"{n:.0f} {u}" if u=="o" else f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.2f} To"

def folder_size(path):
    total = 0
    try:
        with os.scandir(path) as it:
            for e in it:
                try:
                    if e.is_file(follow_symlinks=False): total += e.stat().st_size
                    elif e.is_dir(follow_symlinks=False): total += folder_size(e.path)
                except: pass
    except: pass
    return total

def pub_color(pub):
    h = int(hashlib.md5((pub or "?").encode()).hexdigest(), 16)
    return PALETTE[h % len(PALETTE)]

def _days_ago(dt):
    if not dt: return "inconnu"
    d = (datetime.now() - dt).days
    if d == 0: return "aujourd'hui"
    return f"il y a {d} j  ({dt.strftime('%d/%m/%Y')})"

def _type_badges(app):
    parts = []
    if app.get("store"):    parts.append("Microsoft Store")
    if app.get("portable"): parts.append("Portable")
    if not parts:           parts.append("Classique")
    return "  ·  ".join(parts)

def is_system(name, publisher, sys_comp, uninstall):
    if sys_comp == 1: return True
    if (publisher or "").lower().strip() in SYSTEM_PUBLISHERS: return True
    if any((name or "").lower().strip().startswith(p) for p in SYSTEM_PREFIXES): return True
    return False

# ── UserAssist ────────────────────────────────────────────────────────────────
UA_GUIDS = [
    "{CEBFF5CD-ACE2-4F4F-9178-9926F41749EA}",
    "{F4E57C4B-2036-45F0-A9AB-443BCFE33D9F}",
]
FT_EPOCH = 116_444_736_000_000_000

def get_userassist() -> dict:
    result = {}
    for guid in UA_GUIDS:
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                f"SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Explorer\\UserAssist\\{guid}\\Count")
            for i in range(winreg.QueryInfoKey(key)[1]):
                try:
                    name, data, _ = winreg.EnumValue(key, i)
                    decoded = codecs.decode(name, "rot_13")
                    if len(data) < 72: continue
                    ft = (struct.unpack_from("<I",data,64)[0] << 32) | struct.unpack_from("<I",data,60)[0]
                    if ft < FT_EPOCH: continue
                    dt = datetime.fromtimestamp((ft - FT_EPOCH) / 10_000_000)
                    exe = os.path.basename(decoded).lower()
                    if exe and (exe not in result or dt > result[exe]):
                        result[exe] = dt
                except: pass
            winreg.CloseKey(key)
        except: pass
    return result

def best_last_used(app, ua):
    loc = app.get("location","")
    best = None
    # Try UserAssist first
    if loc and os.path.isdir(loc):
        try:
            for root, _, files in os.walk(loc):
                for f in files:
                    if f.lower().endswith(".exe"):
                        dt = ua.get(f.lower())
                        if dt and (best is None or dt > best): best = dt
        except: pass
    if best: return best
    # Fallback: file access time
    if loc and os.path.isdir(loc):
        try:
            for root, _, files in os.walk(loc):
                for f in files:
                    if f.lower().endswith(".exe"):
                        try:
                            t = datetime.fromtimestamp(os.path.getatime(os.path.join(root, f)))
                            if best is None or t > best: best = t
                        except: pass
        except: pass
    return best

# ── Registre ──────────────────────────────────────────────────────────────────
REG_PATHS = [
    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
    (winreg.HKEY_CURRENT_USER,  r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
]

def _rv(key, name, default=""):
    try: return winreg.QueryValueEx(key, name)[0]
    except: return default

def scan_registry():
    apps, seen = [], set()
    for hive, path in REG_PATHS:
        try: root = winreg.OpenKey(hive, path)
        except: continue
        try: count = winreg.QueryInfoKey(root)[0]
        except: winreg.CloseKey(root); continue
        for i in range(count):
            try: sub = winreg.OpenKey(root, winreg.EnumKey(root, i))
            except: continue
            try:
                name = _rv(sub, "DisplayName")
                if not name or name in seen: continue
                pub  = _rv(sub, "Publisher")
                loc  = _rv(sub, "InstallLocation").strip().rstrip("\\")
                unin = _rv(sub, "UninstallString")
                if is_system(name, pub, _rv(sub,"SystemComponent",0), unin): continue
                seen.add(name)
                apps.append({"name":name,"publisher":pub,"version":_rv(sub,"DisplayVersion"),
                    "location":loc,"uninstall":unin,"quiet":_rv(sub,"QuietUninstallString"),
                    "portable":not bool(unin),"store":False,"size":0,"last_used":None})
            except: pass
            finally:
                try: winreg.CloseKey(sub)
                except: pass
        try: winreg.CloseKey(root)
        except: pass
    return apps

# ── Microsoft Store ───────────────────────────────────────────────────────────
def scan_store():
    try:
        ps = ("Get-AppxPackage | Where-Object {"
              "  $_.Publisher -notlike '*Microsoft*' -and"
              "  $_.Publisher -notlike '*Windows*' -and"
              "  $_.SignatureKind -ne 'System'"
              "} | Select-Object Name,Publisher,InstallLocation,PackageFullName,Version"
              " | ConvertTo-Json -Compress")
        r = subprocess.run(["powershell","-NoProfile","-NonInteractive","-Command",ps],
            capture_output=True, text=True, timeout=30, encoding="utf-8", errors="replace")
        if not r.stdout.strip(): return []
        data = json.loads(r.stdout)
        if isinstance(data, dict): data = [data]
        apps = []
        for pkg in data:
            name = pkg.get("Name","")
            if not name: continue
            pub = (pkg.get("Publisher") or "").split("=")[-1].strip()
            apps.append({"name":name.replace("."," ").strip(),"publisher":pub,
                "version":pkg.get("Version",""),"location":(pkg.get("InstallLocation") or "").strip(),
                "uninstall":"","quiet":"","portable":False,"store":True,
                "package_full_name":pkg.get("PackageFullName",""),"size":0,"last_used":None})
        return apps
    except: return []

# ── Désinstallation ───────────────────────────────────────────────────────────
def do_uninstall(app):
    if app.get("store"):
        pkg = app.get("package_full_name","")
        if not pkg: return False, "Package introuvable"
        try:
            r = subprocess.run(["powershell","-NoProfile","-Command",
                f"Remove-AppxPackage -Package '{pkg}'"],
                capture_output=True, timeout=60)
            return r.returncode == 0, f"Code {r.returncode}"
        except Exception as e: return False, str(e)

    if app.get("portable"):
        loc = app.get("location","")
        if loc and os.path.isdir(loc):
            try: shutil.rmtree(loc); return True, "Dossier supprimé"
            except Exception as e: return False, str(e)
        return False, "Dossier introuvable"

    cmd = app.get("quiet") or app.get("uninstall","")
    if not cmd: return False, "Pas de commande"
    if not app.get("quiet"):
        cl = cmd.lower()
        if "msiexec" in cl:
            cmd = cmd.replace("/I{","/X{").replace("/i{","/X{")
            if "/quiet" not in cl: cmd += " /quiet /norestart"
        elif ".exe" in cl and not any(f in cl for f in ("/s","/silent","/quiet")):
            cmd += " /S"
    try:
        r = subprocess.run(cmd, shell=True, timeout=180, capture_output=True)
        return r.returncode in (0,3010,1605,1614), f"Code {r.returncode}"
    except subprocess.TimeoutExpired: return False, "Délai dépassé"
    except Exception as e: return False, str(e)

# ═══════════════════════════════════════════════════════════════════════════════
# TREEMAP
# ═══════════════════════════════════════════════════════════════════════════════
def _split(items, x, y, w, h):
    if not items: return []
    if len(items) == 1: return [(x, y, w, h, items[0])]
    total = sum(a["size"] for a in items) or 1
    mid = len(items) // 2
    ls  = sum(a["size"] for a in items[:mid]) or 1
    rs  = sum(a["size"] for a in items[mid:]) or 1
    G   = 2
    if w >= h:
        lw = max(1, w * ls / total - G)
        rw = max(1, w - lw - G)
        return _split(items[:mid],x,y,lw,h) + _split(items[mid:],x+lw+G,y,rw,h)
    else:
        th = max(1, h * ls / total - G)
        bh = max(1, h - th - G)
        return _split(items[:mid],x,y,w,th) + _split(items[mid:],x,y+th+G,w,bh)

class TreemapView(ctk.CTkFrame):
    def __init__(self, parent, on_uninstall=None, **kw):
        super().__init__(parent, corner_radius=0, fg_color=BG_DARK, **kw)
        self._apps = []; self._rects = []; self._tip = None
        self._on_uninstall = on_uninstall
        self._build()

    def _build(self):
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        top = ctk.CTkFrame(self, height=52, corner_radius=0, fg_color=BG_HDR)
        top.grid(row=0, column=0, sticky="ew")
        top.grid_columnconfigure(3, weight=1)

        ctk.CTkLabel(top, text="Disque :", font=("Segoe UI",12)).grid(row=0,column=0,padx=(16,4),pady=12)
        drives = [f"{d}:\\" for d in string.ascii_uppercase if os.path.exists(f"{d}:\\")]
        self._drive_var = tk.StringVar(value=drives[0] if drives else "C:\\")
        ctk.CTkComboBox(top, values=drives, width=100, variable=self._drive_var,
                        command=lambda _: self._redraw()).grid(row=0,column=1,padx=(0,16),pady=12)
        self._lbl_disk = ctk.CTkLabel(top, text="", font=("Segoe UI",11), text_color=MUTED)
        self._lbl_disk.grid(row=0, column=2, padx=8)
        self._bar_c = tk.Canvas(top, height=14, bg=BG_HDR, highlightthickness=0)
        self._bar_c.grid(row=0, column=3, sticky="ew", padx=(0,16), pady=19)
        ctk.CTkLabel(top, text="Survolez pour les détails · les apps sans taille connue sont masquées",
                     font=("Segoe UI",10), text_color=MUTED).grid(row=0,column=4,padx=(0,16))

        self._c = tk.Canvas(self, bg=BG_DARK, highlightthickness=0)
        self._c.grid(row=1, column=0, sticky="nsew", padx=4, pady=4)
        self._c.bind("<Configure>", lambda e: self._redraw())
        self._c.bind("<Motion>", self._hover)
        self._c.bind("<Leave>",  lambda e: self._hide_tip())
        self._c.bind("<Button-1>", self._on_click)

    def update_apps(self, apps):
        self._apps = sorted([a for a in apps if a.get("size",0)>0],
                            key=lambda a: a["size"], reverse=True)
        self._redraw()

    def _draw_disk_bar(self):
        drive = self._drive_var.get()
        try:
            u = shutil.disk_usage(drive)
            pct = u.used / u.total * 100
            self._lbl_disk.configure(
                text=f"Utilisé : {fmt_size(u.used)}  /  Libre : {fmt_size(u.free)}  /  Total : {fmt_size(u.total)}")
            w = self._bar_c.winfo_width()
            if w < 10: return
            self._bar_c.delete("all")
            self._bar_c.create_rectangle(0,0,w,14,fill="#374151",outline="")
            uw = int(w * pct / 100)
            color = SUCCESS if pct<70 else (WARNING if pct<90 else DANGER)
            self._bar_c.create_rectangle(0,0,uw,14,fill=color,outline="")
        except: pass

    def _redraw(self):
        c = self._c; c.delete("all"); self._rects = []
        self._draw_disk_bar()
        w, h = c.winfo_width(), c.winfo_height()
        if w < 10 or h < 10 or not self._apps:
            c.create_text(w//2, h//2,
                text="Aucune donnée.\nLancez d'abord un scan complet.",
                fill=MUTED, font=("Segoe UI",13), justify="center")
            return
        for rx,ry,rw,rh,app in _split(self._apps, 2, 2, w-4, h-4):
            col = pub_color(app.get("publisher") or app["name"])
            rid = c.create_rectangle(rx,ry,rx+rw,ry+rh, fill=col, outline=BG_DARK, width=1)
            tid = None
            if rw > 50 and rh > 24:
                nm = app["name"][:18] + ("…" if len(app["name"])>18 else "")
                tid = c.create_text(rx+rw/2, ry+rh/2,
                    text=f"{nm}\n{fmt_size(app['size'])}",
                    fill="white", font=("Segoe UI", 8 if rw<120 else 10),
                    justify="center", width=rw-8)
            self._rects.append((rx,ry,rx+rw,ry+rh,app,rid,tid))

    def _at(self, x, y):
        for x1,y1,x2,y2,app,*_ in self._rects:
            if x1<=x<=x2 and y1<=y<=y2: return app
        return None

    def _hover(self, e):
        app = self._at(e.x, e.y)
        if not app: self._hide_tip(); self._c.configure(cursor=""); return
        self._c.configure(cursor="hand2")
        last = app.get("last_used")
        lu = f"il y a {(datetime.now()-last).days} j" if last else "inconnu"
        self._show_tip(e.x, e.y,
            f"{app['name']}\nÉditeur : {app.get('publisher') or '—'}\n"
            f"Taille : {fmt_size(app['size'])}\nDernière utilisation : {lu}")

    def _show_tip(self, x, y, text):
        self._hide_tip()
        self._tip = tk.Toplevel(self)
        self._tip.wm_overrideredirect(True)
        tk.Label(self._tip, text=text, justify="left", bg="#1F2937", fg="white",
                 font=("Segoe UI",10), padx=10, pady=8).pack()
        self._tip.wm_geometry(f"+{self.winfo_rootx()+x+14}+{self.winfo_rooty()+y+14}")

    def _hide_tip(self):
        if self._tip:
            try: self._tip.destroy()
            except: pass
            self._tip = None

    def _on_click(self, e):
        self._hide_tip()
        app = self._at(e.x, e.y)
        if app and self._on_uninstall is not None:
            AppDetailDialog(self, app, self._on_uninstall)

# ═══════════════════════════════════════════════════════════════════════════════
# TABLE
# ═══════════════════════════════════════════════════════════════════════════════
def _style():
    s = ttk.Style(); s.theme_use("clam")
    s.configure("App.Treeview", background=BG_EVEN, foreground="#E5E7EB",
        fieldbackground=BG_EVEN, borderwidth=0, rowheight=38, font=("Segoe UI",11))
    s.configure("App.Treeview.Heading", background=BG_BAR, foreground="#9CA3AF",
        font=("Segoe UI",11,"bold"), relief="flat", borderwidth=0, padding=(8,8))
    s.map("App.Treeview",
        background=[("selected","#1D3461"),("!selected",BG_EVEN)],
        foreground=[("selected","#E5E7EB")])
    s.map("App.Treeview.Heading", background=[("active","#374151")])
    s.layout("App.Treeview",[("Treeview.treearea",{"sticky":"nswe"})])
    s.configure("Dark.Vertical.TScrollbar", background="#1F2937",
        troughcolor=BG_DARK, arrowcolor="#6B7280", borderwidth=0, arrowsize=13)
    s.map("Dark.Vertical.TScrollbar",
        background=[("active","#374151"),("!active","#1F2937")])

class AppTable(ctk.CTkFrame):
    COLS    = ("sel","name","publisher","size","last_used","location")
    HEADERS = ("","Application","Éditeur","Taille","Dernière utilisation","Emplacement")
    WIDTHS  = (36, 280, 165, 100, 155, 300)

    def __init__(self, parent, on_change, **kw):
        super().__init__(parent, corner_radius=0, fg_color=BG_DARK, **kw)
        self._on_change = on_change
        self._apps = []; self._checked = set()
        self._build()

    def _build(self):
        _style()
        self.tree = ttk.Treeview(self, columns=self.COLS, show="headings",
                                  style="App.Treeview", selectmode="none")
        for col,hdr,w in zip(self.COLS,self.HEADERS,self.WIDTHS):
            self.tree.heading(col, text=hdr)
            self.tree.column(col, width=w, minwidth=max(w//2,36),
                anchor="center" if col in ("sel","size") else "w",
                stretch=(col=="location"))
        vsb = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview,
                            style="Dark.Vertical.TScrollbar")
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        self.grid_rowconfigure(0, weight=1); self.grid_columnconfigure(0, weight=1)
        for tag,fg in [("success",SUCCESS),("warning",WARNING),("danger",DANGER),("muted",MUTED)]:
            self.tree.tag_configure(tag, foreground=fg)
        self.tree.tag_configure("odd",     background=BG_ODD)
        self.tree.tag_configure("even",    background=BG_EVEN)
        self.tree.tag_configure("checked", foreground="#60A5FA", font=("Segoe UI",11,"bold"))
        self.tree.bind("<Button-1>", self._click)

    def populate(self, apps):
        self._apps = apps; self._checked.clear()
        self.tree.delete(*self.tree.get_children())
        for i,app in enumerate(apps):
            last = app.get("last_used")
            sz   = fmt_size(app["size"]) if app.get("size") else "—"
            pub  = (app.get("publisher") or "—")[:30]
            badges = ("  [Store]" if app.get("store") else "") + \
                     ("  [portable]" if app.get("portable") else "")
            name = app["name"] + badges
            if last:
                d = (datetime.now()-last).days
                lu = "aujourd'hui" if d==0 else f"il y a {d} j"
                tt = "success" if d<30 else ("warning" if d<90 else "danger")
            else:
                lu, tt = "inconnu", "muted"
            base = "odd" if i%2 else "even"
            self.tree.insert("","end",iid=str(i),
                values=("☐",name,pub,sz,lu,app.get("location") or "—"),
                tags=(base,tt))
        self._on_change()

    def _click(self, e):
        iid = self.tree.identify_row(e.y)
        if not iid or self.tree.identify_region(e.x,e.y)!="cell": return
        idx  = int(iid); name = self._apps[idx]["name"]
        vals = list(self.tree.item(iid,"values"))
        last = self._apps[idx].get("last_used")
        d    = (datetime.now()-last).days if last else 999
        tt   = "success" if d<30 else ("warning" if d<90 else "danger") if last else "muted"
        base = "odd" if idx%2 else "even"
        if name in self._checked:
            self._checked.discard(name); vals[0]="☐"
            self.tree.item(iid, values=vals, tags=(base,tt))
        else:
            self._checked.add(name); vals[0]="☑"
            self.tree.item(iid, values=vals, tags=(base,tt,"checked"))
        self._on_change()

    def get_selected(self): return [a for a in self._apps if a["name"] in self._checked]

    def select_all(self, v):
        self._checked.clear()
        for i,app in enumerate(self._apps):
            iid  = str(i)
            vals = list(self.tree.item(iid,"values"))
            last = app.get("last_used")
            d    = (datetime.now()-last).days if last else 999
            tt   = "success" if d<30 else ("warning" if d<90 else "danger") if last else "muted"
            base = "odd" if i%2 else "even"
            if v:
                self._checked.add(app["name"]); vals[0]="☑"
                self.tree.item(iid, values=vals, tags=(base,tt,"checked"))
            else:
                vals[0]="☐"; self.tree.item(iid, values=vals, tags=(base,tt))
        self._on_change()

# ═══════════════════════════════════════════════════════════════════════════════
# DIALOGUES
# ═══════════════════════════════════════════════════════════════════════════════
class AppDetailDialog(ctk.CTkToplevel):
    def __init__(self, parent, app, on_uninstall):
        super().__init__(parent)
        self.title(app["name"]); self.geometry("520x370")
        self.resizable(False, False); self.grab_set()
        self._app = app; self._on_uninstall = on_uninstall

        # 3 fixed rows: header / info / buttons
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # ── Header ──
        hdr = ctk.CTkFrame(self, fg_color=BG_HDR, corner_radius=0, height=56)
        hdr.grid(row=0, column=0, sticky="ew")
        hdr.grid_propagate(False)
        hdr.grid_columnconfigure(1, weight=1)
        col = pub_color(app.get("publisher") or app["name"])
        ctk.CTkFrame(hdr, width=6, fg_color=col, corner_radius=0
                     ).grid(row=0, column=0, sticky="ns")
        ctk.CTkLabel(hdr, text=app["name"], font=("Segoe UI", 15, "bold"),
                     anchor="w", wraplength=460
                     ).grid(row=0, column=1, sticky="ew", padx=16)

        # ── Info grid ──
        info = ctk.CTkFrame(self, fg_color="transparent")
        info.grid(row=1, column=0, sticky="nsew", padx=20, pady=6)
        rows = [
            ("Éditeur",              app.get("publisher") or "—"),
            ("Version",              app.get("version")   or "—"),
            ("Taille",               fmt_size(app["size"]) if app.get("size") else "—"),
            ("Dernière utilisation", _days_ago(app.get("last_used"))),
            ("Emplacement",          app.get("location")  or "—"),
            ("Type",                 _type_badges(app)),
        ]
        for i, (lbl, val) in enumerate(rows):
            ctk.CTkLabel(info, text=lbl + " :", font=("Segoe UI", 11, "bold"),
                         text_color=MUTED, anchor="e", width=145
                         ).grid(row=i, column=0, sticky="e", pady=3)
            ctk.CTkLabel(info, text=val, font=("Segoe UI", 11), anchor="w",
                         wraplength=310, justify="left"
                         ).grid(row=i, column=1, sticky="w", padx=10, pady=3)

        # ── Buttons ──
        bf = ctk.CTkFrame(self, fg_color="transparent", height=56)
        bf.grid(row=2, column=0, sticky="ew")
        bf.grid_propagate(False)
        bf.grid_columnconfigure(0, weight=1)
        inner = ctk.CTkFrame(bf, fg_color="transparent")
        inner.place(relx=0.5, rely=0.5, anchor="center")
        ctk.CTkButton(inner, text="Fermer", width=110, fg_color="#374151",
                      hover_color="#4B5563", command=self.destroy
                      ).pack(side="left", padx=10)
        ctk.CTkButton(inner, text="Désinstaller", width=150, fg_color=DANGER,
                      hover_color="#DC2626", command=self._uninstall
                      ).pack(side="left", padx=10)

    def _uninstall(self):
        self.destroy()
        self._on_uninstall(self._app)


class UninstallDialog(ctk.CTkToplevel):
    def __init__(self, parent, apps):
        super().__init__(parent)
        self.title("Confirmer la désinstallation"); self.geometry("520x430")
        self.resizable(False,False); self.grab_set(); self.result = False
        sz = sum(a.get("size",0) for a in apps)
        ctk.CTkLabel(self, text=f"Désinstaller {len(apps)} application(s) ?",
                     font=("Segoe UI",17,"bold")).pack(pady=(24,6))
        ctk.CTkLabel(self, text=f"Espace récupéré estimé : {fmt_size(sz)}",
                     font=("Segoe UI",13), text_color=SUCCESS).pack(pady=4)
        box = ctk.CTkTextbox(self, height=210, font=("Segoe UI",11), wrap="word")
        box.pack(fill="x", padx=24, pady=12)
        box.insert("end","\n".join("  • "+a["name"]+
            (" [Store]" if a.get("store") else "")+
            (" [portable]" if a.get("portable") else "") for a in apps))
        box.configure(state="disabled")
        if any(a.get("portable") for a in apps):
            ctk.CTkLabel(self, text="⚠  Les apps portables seront supprimées définitivement.",
                font=("Segoe UI",10), text_color=WARNING, wraplength=480).pack(pady=(0,8))
        f = ctk.CTkFrame(self, fg_color="transparent"); f.pack(pady=12)
        ctk.CTkButton(f,text="Annuler",width=110,fg_color="#374151",hover_color="#4B5563",
                      command=self.destroy).pack(side="left",padx=10)
        ctk.CTkButton(f,text="Désinstaller",width=140,fg_color=DANGER,hover_color="#DC2626",
                      command=self._ok).pack(side="left",padx=10)
    def _ok(self): self.result=True; self.destroy()

class ProgressDialog(ctk.CTkToplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("Désinstallation…"); self.geometry("520x270")
        self.resizable(False,False); self.grab_set()
        self.protocol("WM_DELETE_WINDOW",lambda:None)
        self.lbl = ctk.CTkLabel(self, text="", font=("Segoe UI",12)); self.lbl.pack(pady=(24,4))
        self.bar = ctk.CTkProgressBar(self, width=460); self.bar.pack(pady=8); self.bar.set(0)
        self.lbl_n = ctk.CTkLabel(self, text="", font=("Segoe UI",11), text_color=MUTED)
        self.lbl_n.pack()
        self.log = ctk.CTkTextbox(self, height=100, font=("Consolas",10))
        self.log.pack(fill="x", padx=20, pady=12)
    def update(self, d, t, n):
        self.bar.set(d/t if t else 0)
        self.lbl.configure(text=f"Désinstallation de {n}…")
        self.lbl_n.configure(text=f"{d} / {t}")
    def log_line(self, l):
        self.log.insert("end", l+"\n"); self.log.see("end")

class ResultDialog(ctk.CTkToplevel):
    def __init__(self, parent, ok, fail, freed, on_rescan):
        super().__init__(parent)
        self.title("Résultat"); self.geometry("420x320")
        self.resizable(False,False); self.grab_set()
        icon = "✅" if not fail else ("⚠️" if ok else "❌")
        ctk.CTkLabel(self, text=f"{icon}  Désinstallation terminée",
                     font=("Segoe UI",17,"bold")).pack(pady=(28,8))
        ctk.CTkLabel(self, text=f"✓  {ok} application(s) désinstallée(s)",
                     font=("Segoe UI",13), text_color=SUCCESS).pack(pady=4)
        if fail:
            ctk.CTkLabel(self, text=f"✗  {fail} échec(s)",
                         font=("Segoe UI",13), text_color=DANGER).pack(pady=4)
        ctk.CTkLabel(self, text=f"💾  Espace libéré : {fmt_size(freed)}",
                     font=("Segoe UI",15,"bold"), text_color=ACCENT).pack(pady=12)
        f = ctk.CTkFrame(self, fg_color="transparent"); f.pack(pady=12)
        ctk.CTkButton(f,text="Rescanner",width=120,
                      command=lambda:(self.destroy(),on_rescan())).pack(side="left",padx=8)
        ctk.CTkButton(f,text="Fermer",width=100,fg_color="#374151",hover_color="#4B5563",
                      command=self.destroy).pack(side="left",padx=8)

class UpdateDialog(ctk.CTkToplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("Mise à jour des applications"); self.geometry("680x520")
        self.resizable(True,True); self.grab_set()
        self.protocol("WM_DELETE_WINDOW",lambda:None); self._proc=None
        ctk.CTkLabel(self, text="⬆  Mise à jour en cours…",
                     font=("Segoe UI",16,"bold")).pack(pady=(22,2))
        ctk.CTkLabel(self, text="winget upgrade --all --include-unknown",
                     font=("Consolas",10), text_color=MUTED).pack(pady=(0,10))
        self._bar = ctk.CTkProgressBar(self, width=580, mode="indeterminate")
        self._bar.pack(pady=(0,10)); self._bar.start()
        self._log = ctk.CTkTextbox(self, font=("Consolas",10), wrap="word")
        self._log.pack(fill="both", expand=True, padx=20, pady=(0,8))
        self._btn = ctk.CTkButton(self,text="Fermer",width=110,state="disabled",
            fg_color="#374151",hover_color="#4B5563",command=self.destroy)
        self._btn.pack(pady=(0,16))
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        try:
            self._proc = subprocess.Popen(
                ["winget","upgrade","--all","--include-unknown"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW)
            updated = 0
            for raw in self._proc.stdout:
                line = raw.rstrip()
                if not line: continue
                self.after(0, lambda l=line:(self._log.insert("end",l+"\n"),self._log.see("end")))
                if any(k in line.lower() for k in ("successfully installed","successfully upgraded")): updated+=1
            self._proc.wait()
            self.after(0, lambda: self._done(updated, self._proc.returncode))
        except FileNotFoundError:
            self.after(0, lambda: self._err("winget introuvable — installez 'App Installer' depuis le Microsoft Store."))
        except Exception as e:
            self.after(0, lambda: self._err(str(e)))

    def _done(self, n, rc):
        self._bar.stop(); self._bar.configure(mode="determinate"); self._bar.set(1)
        sep = "\n"+"─"*55+"\n"
        self._log.insert("end", f"{sep}✅  Terminé — {n} mise(s) à jour\n" if rc==0
                         else f"{sep}⚠️  Terminé (code {rc})\n")
        self._log.see("end"); self._btn.configure(state="normal")
        self.protocol("WM_DELETE_WINDOW",self.destroy); self.title("Mise à jour terminée")

    def _err(self, msg):
        self._bar.stop(); self._log.insert("end",f"\n❌  {msg}\n"); self._log.see("end")
        self._btn.configure(state="normal"); self.protocol("WM_DELETE_WINDOW",self.destroy)

# ═══════════════════════════════════════════════════════════════════════════════
# FENÊTRE PRINCIPALE
# ═══════════════════════════════════════════════════════════════════════════════
class AppCleaner(ctk.CTk):
    DAYS_MAP = {"Toutes":0,"30 jours":30,"60 jours":60,"90 jours":90,
                "6 mois":180,"1 an":365,"2 ans":730}

    def __init__(self):
        super().__init__()
        self.title("AppCleaner"); self.geometry("1300x860"); self.minsize(960,600)
        self._all_apps = []; self._filtered = []
        self._build_ui(); self._start_scan()

    def _build_ui(self):
        self.grid_rowconfigure(1,weight=1); self.grid_columnconfigure(0,weight=1)

        # Header
        hdr = ctk.CTkFrame(self, height=64, corner_radius=0, fg_color=BG_HDR)
        hdr.grid(row=0, column=0, sticky="ew"); hdr.grid_columnconfigure(1,weight=1)
        ctk.CTkLabel(hdr,text="🗑  AppCleaner",font=("Segoe UI",20,"bold")
                     ).grid(row=0,column=0,padx=20,pady=14)
        ctk.CTkLabel(hdr,text="Gérez et nettoyez vos applications Windows",
                     font=("Segoe UI",11),text_color=MUTED).grid(row=0,column=1,padx=8,sticky="w")
        ctk.CTkButton(hdr,text="⬆  Mettre à jour tout",width=180,height=36,
                      font=("Segoe UI",12,"bold"),fg_color="#059669",hover_color="#047857",
                      command=lambda:UpdateDialog(self)).grid(row=0,column=2,padx=12,pady=14)
        self._lbl_status = ctk.CTkLabel(hdr,text="",font=("Segoe UI",11),text_color=MUTED)
        self._lbl_status.grid(row=0,column=3,padx=20)

        # Tabs
        self._tabs = ctk.CTkTabview(self, corner_radius=0, fg_color=BG_DARK,
            segmented_button_fg_color=BG_HDR, segmented_button_selected_color=ACCENT,
            segmented_button_selected_hover_color="#2563EB",
            segmented_button_unselected_color=BG_HDR,
            segmented_button_unselected_hover_color=BG_BAR)
        self._tabs.grid(row=1,column=0,sticky="nsew")
        self._tabs.add("  Applications  ")
        self._tabs.add("  Espace disque  ")
        self._tabs.configure(command=self._tab_change)

        # ── Tab Applications ──
        t1 = self._tabs.tab("  Applications  ")
        t1.grid_rowconfigure(1,weight=1); t1.grid_columnconfigure(0,weight=1)

        bar = ctk.CTkFrame(t1,height=56,corner_radius=0,fg_color=BG_BAR)
        bar.grid(row=0,column=0,sticky="ew"); bar.grid_columnconfigure(99,weight=1)
        c=0
        ctk.CTkLabel(bar,text="Recherche :",font=("Segoe UI",12)).grid(row=0,column=c,padx=(16,4),pady=12); c+=1
        self._sv = tk.StringVar(); self._sv.trace_add("write",lambda *_:self._filter())
        ctk.CTkEntry(bar,textvariable=self._sv,width=200,placeholder_text="Nom d'application…"
                     ).grid(row=0,column=c,padx=(0,16),pady=12); c+=1
        ctk.CTkLabel(bar,text="Non utilisée depuis :",font=("Segoe UI",12)).grid(row=0,column=c,padx=(0,4),pady=12); c+=1
        self._dc = ctk.CTkComboBox(bar,values=list(self.DAYS_MAP),width=130,command=lambda _:self._filter())
        self._dc.set("Toutes"); self._dc.grid(row=0,column=c,padx=(0,16),pady=12); c+=1
        ctk.CTkLabel(bar,text="Trier par :",font=("Segoe UI",12)).grid(row=0,column=c,padx=(0,4),pady=12); c+=1
        self._sc = ctk.CTkComboBox(bar,values=["Nom A→Z","Taille ↓","Taille ↑","Dernière utilisation"],
                                    width=180,command=lambda _:self._filter())
        self._sc.set("Nom A→Z"); self._sc.grid(row=0,column=c,padx=(0,16),pady=12); c+=1
        self._vp = tk.BooleanVar(value=True)
        ctk.CTkCheckBox(bar,text="Portables",variable=self._vp,command=self._filter
                        ).grid(row=0,column=c,padx=(0,8),pady=12); c+=1
        self._vs = tk.BooleanVar(value=True)
        ctk.CTkCheckBox(bar,text="Store",variable=self._vs,command=self._filter
                        ).grid(row=0,column=c,padx=(0,16),pady=12); c+=99
        self._va = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(bar,text="Tout sélectionner",variable=self._va,
                        command=lambda:self._table.select_all(self._va.get())
                        ).grid(row=0,column=c,padx=16,pady=12)

        self._table = AppTable(t1, on_change=self._update_bot)
        self._table.grid(row=1,column=0,sticky="nsew")

        bot = ctk.CTkFrame(t1,height=64,corner_radius=0,fg_color=BG_HDR)
        bot.grid(row=2,column=0,sticky="ew"); bot.grid_columnconfigure(1,weight=1)
        self._lbl_cnt = ctk.CTkLabel(bot,text="",font=("Segoe UI",12))
        self._lbl_cnt.grid(row=0,column=0,padx=20,pady=16)
        self._lbl_sel = ctk.CTkLabel(bot,text="",font=("Segoe UI",11),text_color=MUTED)
        self._lbl_sel.grid(row=0,column=1,padx=8,sticky="w")
        self._btn_u = ctk.CTkButton(bot,text="Désinstaller la sélection",width=220,height=42,
            font=("Segoe UI",13,"bold"),fg_color=DANGER,hover_color="#DC2626",
            state="disabled",command=self._ask_uninstall)
        self._btn_u.grid(row=0,column=2,padx=20,pady=10)

        # ── Tab Espace disque ──
        t2 = self._tabs.tab("  Espace disque  ")
        t2.grid_rowconfigure(0,weight=1); t2.grid_columnconfigure(0,weight=1)
        self._treemap = TreemapView(t2, on_uninstall=lambda a: self._run_uninstall([a]))
        self._treemap.grid(row=0,column=0,sticky="nsew")

    def _tab_change(self):
        if "disque" in self._tabs.get().lower():
            self._treemap.update_apps(self._all_apps)

    # ── Scan ──
    def _start_scan(self):
        self._lbl_status.configure(text="Lecture du registre…")
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self):
        self.after(0,lambda:self._lbl_status.configure(text="Lecture UserAssist…"))
        ua = get_userassist()
        self.after(0,lambda:self._lbl_status.configure(text="Scan registre…"))
        apps = scan_registry()
        self.after(0,lambda:self._lbl_status.configure(text="Scan Microsoft Store…"))
        seen = {a["name"] for a in apps}
        for a in scan_store():
            if a["name"] not in seen:
                apps.append(a); seen.add(a["name"])
        total = len(apps)
        for i,app in enumerate(apps):
            self.after(0,lambda i=i,t=total:self._lbl_status.configure(text=f"Analyse {i+1}/{t}…"))
            loc = app.get("location","")
            if loc and os.path.isdir(loc):
                app["size"]     = folder_size(loc)
            app["last_used"] = best_last_used(app, ua)
        apps.sort(key=lambda a:a["name"].lower())
        self._all_apps = apps
        self.after(0, self._scan_done)

    def _scan_done(self):
        n  = len(self._all_apps)
        sz = sum(a.get("size",0) for a in self._all_apps)
        self._lbl_status.configure(text=f"{n} applications — {fmt_size(sz)} au total")
        self._filter()

    # ── Filtres ──
    def _filter(self, *_):
        s    = self._sv.get().lower()
        days = self.DAYS_MAP.get(self._dc.get(),0)
        sort = self._sc.get()
        f    = self._all_apps.copy()
        if s:    f = [a for a in f if s in a["name"].lower() or s in (a.get("publisher") or "").lower()]
        if not self._vp.get(): f = [a for a in f if not a.get("portable")]
        if not self._vs.get(): f = [a for a in f if not a.get("store")]
        if days:
            cut = datetime.now()-timedelta(days=days)
            f = [a for a in f if a.get("last_used") is None or a["last_used"]<cut]
        if   sort=="Taille ↓": f.sort(key=lambda a:a.get("size",0),reverse=True)
        elif sort=="Taille ↑": f.sort(key=lambda a:a.get("size",0))
        elif sort=="Dernière utilisation": f.sort(key=lambda a:a.get("last_used") or datetime.min)
        else: f.sort(key=lambda a:a["name"].lower())
        self._filtered = f
        self._table.populate(f)
        self._lbl_cnt.configure(text=f"{len(f)} application(s) — {fmt_size(sum(a.get('size',0) for a in f))}")

    def _update_bot(self):
        sel = self._table.get_selected()
        if sel:
            self._lbl_sel.configure(text=f"{len(sel)} sélectionnée(s) · {fmt_size(sum(a.get('size',0) for a in sel))} à libérer")
            self._btn_u.configure(state="normal")
        else:
            self._lbl_sel.configure(text=""); self._btn_u.configure(state="disabled")

    # ── Désinstallation ──
    def _ask_uninstall(self):
        sel = self._table.get_selected()
        if not sel: return
        dlg = UninstallDialog(self, sel); self.wait_window(dlg)
        if dlg.result: self._run_uninstall(sel)

    def _run_uninstall(self, apps):
        self._btn_u.configure(state="disabled")
        prog = ProgressDialog(self)
        def worker():
            ok=fail=freed=0
            for i,app in enumerate(apps):
                prog.after(0,lambda n=app["name"],d=i,t=len(apps):prog.update(d,t,n))
                s,msg = do_uninstall(app)
                if s: ok+=1; freed+=app.get("size",0); prog.after(0,lambda n=app["name"]:prog.log_line(f"✓  {n}"))
                else: fail+=1; prog.after(0,lambda n=app["name"],m=msg:prog.log_line(f"✗  {n}  ({m})"))
            prog.after(0,lambda:self._result(prog,ok,fail,freed))
        threading.Thread(target=worker,daemon=True).start()

    def _result(self,prog,ok,fail,freed):
        prog.grab_release(); prog.destroy()
        ResultDialog(self,ok,fail,freed,self._rescan)

    def _rescan(self):
        self._all_apps.clear(); self._table.populate([]); self._start_scan()

if __name__ == "__main__":
    if not is_admin():
        if messagebox.askyesno("Droits administrateur",
            "AppCleaner fonctionne mieux en administrateur.\n\nRelancer en tant qu'administrateur ?"):
            elevate()
    AppCleaner().mainloop()
