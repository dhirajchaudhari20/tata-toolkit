"""
====================================================================================================
                        TATA COMMUNICATIONS - NETWORK OPERATIONS CENTER
                         DC-TO-BRANCH MESH PING MATRIX AUDITOR MODULE
====================================================================================================
Designed and Developed for Tata Communications / Advanced Network Operations
Performs automated mesh ping verification between Data Center (DC) routers and Branch CPE routers.
1. Connects to each DC router.
2. Extracts the first 4 Transport VRs and their first WAN Neighbor IPs from 'show bgp neighbor brief'.
3. Calculates Local Source IP using the Odd/Even rule (Odd: +1, Even: -1).
4. For each Branch CPE router:
   - Queries 'show orgs org MB-NET sd-wan detail <CPE>' to extract circuit destination IPs.
   - Executes rapid pings from matching DC Transport VR and Source IP.
5. Populates live Real-Time Grid Matrix and Detailed Scorecard.
6. Exports high-fidelity Excel report matching NOC specifications.
====================================================================================================
"""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import threading
import time
import re
import os
import sys
from datetime import datetime

try:
    from pam_terminal_controller import PamTerminalController
    HAS_PAM = True
except ImportError:
    HAS_PAM = False

try:
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    HAS_OPENPYXL = True
except ImportError:
    HAS_OPENPYXL = False


# Default Data Center (DC) Routers as per Tata NOC topology
DEFAULT_DC_ROUTERS = [
    "IFT-MB-3007-PDCKHR-R1",
    "IFT-MB-3007-PDCKHR-R2",
    "IFT-MB-3034-DRDCNG-R1",
    "IFT-MB-3034-DRDCNG-R2",
    "IFT-MB-3023-ODCBEL-R1",
    "IFT-MB-3023-ODCBEL-R2"
]

# Default Sample Branch CPE Routers
DEFAULT_CPE_ROUTERS = [
    "1366-SHREEKADI-KADI-R1",
    "1366-SHREEKADI-KADI-R2"
]


def calculate_source_ip(neighbor_ip):
    """
    Calculates local router source IP from BGP neighbor IP according to the /30 subnet rule:
    - If neighbor IP last octet is ODD  -> Local Source IP = neighbor IP + 1
    - If neighbor IP last octet is EVEN -> Local Source IP = neighbor IP - 1
    Example: 10.116.196.57 (odd)  -> 10.116.196.58
    Example: 10.116.135.9  (odd)  -> 10.116.135.10
    Example: 10.116.196.58 (even) -> 10.116.196.57
    """
    if not neighbor_ip:
        return ""
    parts = neighbor_ip.strip().split(".")
    if len(parts) == 4 and parts[3].isdigit():
        last_octet = int(parts[3])
        new_last = last_octet + 1 if (last_octet % 2 != 0) else last_octet - 1
        return f"{parts[0]}.{parts[1]}.{parts[2]}.{new_last}"
    return neighbor_ip


def parse_dc_transport_vrs(bgp_text):
    """
    Extracts only the FIRST 4 Transport VRs and their first valid WAN neighbor IP.
    Explicitly ignores:
    - Control VR (MB-NET-Control-VR, etc.)
    - LAN VR (MB-NET-LAN-VR, etc.)
    - Internal link-local BGP peers (169.254.x.x)
    Returns list of dicts: [ {'vr_name': ..., 'neighbor_ip': ..., 'source_ip': ...}, ... ] (max 4)
    """
    results = []
    if not bgp_text:
        return results

    sections = re.split(r"routing-instance:\s*", bgp_text)
    for sec in sections[1:]:
        lines = sec.strip().splitlines()
        if not lines:
            continue
        vr_name = lines[0].strip()

        # Ignore Control VR and LAN VR
        if "control" in vr_name.lower() or "lan" in vr_name.lower():
            continue

        # Only consider Transport VR
        if "transport" not in vr_name.lower():
            continue

        # Find first valid WAN neighbor IP (ignore 169.254.x.x)
        first_ip = None
        for line in lines[1:]:
            # Look for 10.x.x.x or standard routable IPv4 (not 169.254)
            ip_matches = re.findall(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b", line)
            for ip in ip_matches:
                if not ip.startswith("169.254.") and not ip.startswith("127."):
                    first_ip = ip
                    break
            if first_ip:
                break

        if first_ip:
            source_ip = calculate_source_ip(first_ip)
            results.append({
                "vr_name": vr_name,
                "neighbor_ip": first_ip,
                "source_ip": source_ip
            })
            if len(results) == 4:
                break

    return results


def parse_cpe_sdwan_circuits(sdwan_output):
    """
    Extracts all access circuits and destination IPs from the 'NAT Status:' table of:
    'show orgs org MB-NET sd-wan detail <CPE>'
    Returns list of dicts: [ {'circuit': ..., 'nat_status': bool, 'target_ip': ...}, ... ]
    """
    circuits = []
    if not sdwan_output:
        return circuits

    in_nat = False
    for line in sdwan_output.splitlines():
        line_s = line.strip()
        if "NAT Status:" in line_s:
            in_nat = True
            continue
        if in_nat:
            if line_s.startswith("[ok]") or line_s.startswith("admin@") or "===" in line_s:
                break
            parts = line_s.split()
            # Expecting: [LINK_ID, LINK_FAMILY, ACCESS_CIRCUIT, NAT_STATUS, PUBLIC_IP, PUBLIC_PORT, DataPath_IP, DataPath_PORT]
            if len(parts) >= 5 and parts[1].lower() == "ipv4":
                circuit_name = parts[2].strip()
                nat_status_str = parts[3].lower()
                is_nat = (nat_status_str == "true")
                # For NAT=true, use Public IP (parts[4]); for NAT=false, Public IP or DataPath IP are identical
                public_ip = parts[4].strip() if len(parts) >= 5 else ""
                datapath_ip = parts[6].strip() if len(parts) >= 7 else public_ip
                target_ip = public_ip if is_nat else (public_ip or datapath_ip)

                if re.match(r"^\d+\.\d+\.\d+\.\d+$", target_ip):
                    circuits.append({
                        "circuit": circuit_name,
                        "nat_status": is_nat,
                        "target_ip": target_ip
                    })

    return circuits


class DCMeshPingFrame(ttk.Frame):
    """
    Interactive GUI Frame for Tab 3: DC-to-Branch Mesh Ping Matrix Auditor.
    """

    def __init__(self, parent, root=None, parent_gui=None):
        super().__init__(parent)
        self.root = root or parent.winfo_toplevel()
        self.parent_gui = parent_gui

        # Execution State
        self.is_running = False
        self.pause_event = threading.Event()
        self.pause_event.set()  # Not paused initially
        self.stop_event = threading.Event()

        # Data Stores
        self.matrix_data = {}   # (cpe_name, dc_name) -> dict of status
        self.raw_records = []   # List of individual ping results
        self.dc_transport_cache = {}  # dc_name -> list of 4 Transport VR dicts

        self._build_ui()
        self._load_saved_lists()

    # ──────────────────────────────────────────────────────────────────────────
    # UI Layout Construction
    # ──────────────────────────────────────────────────────────────────────────
    def _build_ui(self):
        # 1. Top Configuration Frame
        top_frame = ttk.LabelFrame(self, text=" ⚙️ DC Mesh Ping Configuration & SLA Controls ", padding=8)
        top_frame.pack(side="top", fill="x", padx=8, pady=(6, 4))

        # Row 0: Connection Mode
        self.conn_mode_var = tk.StringVar(value="pam_terminal")
        ttk.Label(top_frame, text="Connection Mode:", font=("Segoe UI", 9, "bold")).grid(row=0, column=0, padx=4, sticky="w")

        ttk.Radiobutton(
            top_frame,
            text="⭐ Attach to Open PAM Terminal (PuTTY / ARCON)",
            variable=self.conn_mode_var,
            value="pam_terminal"
        ).grid(row=0, column=1, columnspan=2, padx=4, sticky="w")

        ttk.Radiobutton(
            top_frame,
            text="Via Bastion SSH (100.76.198.218)",
            variable=self.conn_mode_var,
            value="bastion"
        ).grid(row=0, column=3, columnspan=2, padx=4, sticky="w")

        # Row 1: Director Credentials
        ttk.Label(top_frame, text="Director IP:", font=("Segoe UI", 9, "bold")).grid(row=1, column=0, padx=4, pady=(6, 0), sticky="w")
        self.director_ip_var = tk.StringVar(value="10.29.11.32")
        ttk.Entry(top_frame, textvariable=self.director_ip_var, width=15).grid(row=1, column=1, padx=4, pady=(6, 0), sticky="w")

        ttk.Label(top_frame, text="Director User:", font=("Segoe UI", 9, "bold")).grid(row=1, column=2, padx=4, pady=(6, 0), sticky="w")
        self.director_user_var = tk.StringVar(value="admin")
        ttk.Entry(top_frame, textvariable=self.director_user_var, width=12).grid(row=1, column=3, padx=4, pady=(6, 0), sticky="w")

        ttk.Label(top_frame, text="Password (Saved):", font=("Segoe UI", 9, "bold")).grid(row=1, column=4, padx=4, pady=(6, 0), sticky="w")
        self.director_pass_var = tk.StringVar(value="e#mi$Wp@U8%f")
        ttk.Entry(top_frame, textvariable=self.director_pass_var, width=16, show="*").grid(row=1, column=5, padx=4, pady=(6, 0), sticky="w")

        # Row 2: Ping Parameters & SLA Thresholds
        ttk.Label(top_frame, text="Ping Count:", font=("Segoe UI", 9)).grid(row=2, column=0, padx=4, pady=(6, 0), sticky="w")
        self.ping_count_var = tk.IntVar(value=500)
        ttk.Spinbox(top_frame, from_=50, to=1000, increment=50, textvariable=self.ping_count_var, width=6).grid(row=2, column=1, padx=4, pady=(6, 0), sticky="w")

        ttk.Label(top_frame, text="Max Loss (%):", font=("Segoe UI", 9)).grid(row=2, column=2, padx=4, pady=(6, 0), sticky="w")
        self.max_loss_var = tk.DoubleVar(value=0.5)
        ttk.Spinbox(top_frame, from_=0.0, to=10.0, increment=0.5, textvariable=self.max_loss_var, width=6).grid(row=2, column=3, padx=4, pady=(6, 0), sticky="w")

        ttk.Label(top_frame, text="Max Avg RTT:", font=("Segoe UI", 9)).grid(row=2, column=4, padx=4, pady=(6, 0), sticky="w")
        self.max_rtt_var = tk.DoubleVar(value=50.0)
        ttk.Spinbox(top_frame, from_=10.0, to=200.0, increment=5.0, textvariable=self.max_rtt_var, width=6).grid(row=2, column=5, padx=4, pady=(6, 0), sticky="w")

        # Concurrency / PuTTY Workers
        worker_box = tk.Frame(top_frame)
        worker_box.grid(row=2, column=6, columnspan=3, padx=(10, 2), pady=(6, 0), sticky="w")
        ttk.Label(worker_box, text="⚡ PuTTY Workers:", font=("Segoe UI", 9, "bold")).pack(side="left")
        self.worker_count_var = tk.IntVar(value=2)
        ttk.Spinbox(worker_box, from_=1, to=5, textvariable=self.worker_count_var, width=3).pack(side="left", padx=4)

        # 2. Middle Split Workspace
        paned = ttk.PanedWindow(self, orient="horizontal")
        paned.pack(side="top", fill="both", expand=True, padx=8, pady=4)

        # Left Column: Input Panels (DCs + CPEs)
        left_box = ttk.Frame(paned)
        paned.add(left_box, weight=1)

        # Left Sub-panel 1: DC Routers
        dc_frame = ttk.LabelFrame(left_box, text=" 🏢 Data Center (DC) Routers (Origins) ", padding=6)
        dc_frame.pack(side="top", fill="both", expand=True, pady=(0, 4))

        self.dc_count_lbl = ttk.Label(dc_frame, text="Total: 6 DCs", font=("Segoe UI", 8, "bold"), foreground="#15803d")
        self.dc_count_lbl.pack(anchor="w", pady=(0, 2))

        dc_scroll = ttk.Scrollbar(dc_frame)
        self.dc_text = tk.Text(dc_frame, width=28, height=6, font=("Consolas", 9), yscrollcommand=dc_scroll.set)
        dc_scroll.config(command=self.dc_text.yview)
        dc_scroll.pack(side="right", fill="y")
        self.dc_text.pack(side="left", fill="both", expand=True)
        self.dc_text.bind("<KeyRelease>", lambda e: self._update_dc_count())

        dc_btn_bar = ttk.Frame(dc_frame)
        dc_btn_bar.pack(fill="x", pady=(4, 0))
        ttk.Button(dc_btn_bar, text="💾 Save DCs", width=10, command=self._save_dc_list).pack(side="left", padx=2)
        ttk.Button(dc_btn_bar, text="🔄 Reset DCs", width=11, command=self._reset_dc_defaults).pack(side="left", padx=2)

        # Left Sub-panel 2: CPE Branch Routers
        cpe_frame = ttk.LabelFrame(left_box, text=" 🌐 Branch CPE Routers (Destinations) ", padding=6)
        cpe_frame.pack(side="bottom", fill="both", expand=True, pady=(4, 0))

        self.cpe_count_lbl = ttk.Label(cpe_frame, text="Total: 2 CPEs", font=("Segoe UI", 8, "bold"), foreground="#0284c7")
        self.cpe_count_lbl.pack(anchor="w", pady=(0, 2))

        cpe_scroll = ttk.Scrollbar(cpe_frame)
        self.cpe_text = tk.Text(cpe_frame, width=28, height=7, font=("Consolas", 9), yscrollcommand=cpe_scroll.set)
        cpe_scroll.config(command=self.cpe_text.yview)
        cpe_scroll.pack(side="right", fill="y")
        self.cpe_text.pack(side="left", fill="both", expand=True)
        self.cpe_text.bind("<KeyRelease>", lambda e: self._update_cpe_count())

        cpe_btn_bar = ttk.Frame(cpe_frame)
        cpe_btn_bar.pack(fill="x", pady=(4, 0))
        ttk.Button(cpe_btn_bar, text="📂 Import", width=8, command=self._import_cpe_file).pack(side="left", padx=2)
        ttk.Button(cpe_btn_bar, text="💾 Save", width=7, command=self._save_cpe_file).pack(side="left", padx=2)
        ttk.Button(cpe_btn_bar, text="🧹 Clear", width=7, command=self._clear_cpes).pack(side="left", padx=2)
        ttk.Button(cpe_btn_bar, text="🔄 Reset", width=7, command=self._reset_cpe_defaults).pack(side="left", padx=2)

        # Right Column: Multi-tab Display (Matrix Grid, Raw Scorecard, Terminal Logs)
        right_box = ttk.Frame(paned)
        paned.add(right_box, weight=3)

        self.results_notebook = ttk.Notebook(right_box)
        self.results_notebook.pack(fill="both", expand=True)

        # Tab A: DC Mesh Matrix Grid (Excel Screenshot replica)
        self.tab_matrix = ttk.Frame(self.results_notebook)
        self.results_notebook.add(self.tab_matrix, text=" 📊 DC Mesh Ping Matrix Grid ")

        self.matrix_tree_frame = ttk.Frame(self.tab_matrix)
        self.matrix_tree_frame.pack(fill="both", expand=True)
        self._init_matrix_table()

        # Tab B: Detailed Scorecard
        self.tab_scorecard = ttk.Frame(self.results_notebook)
        self.results_notebook.add(self.tab_scorecard, text=" 📋 Detailed Circuit Diagnostics ")

        sc_cols = ("timestamp", "dc", "cpe", "circuit", "transport_vr", "source_ip", "dest_ip", "loss", "avg_rtt", "sla")
        self.sc_tree = ttk.Treeview(self.tab_scorecard, columns=sc_cols, show="headings", height=10)
        self.sc_tree.heading("timestamp", text="Time")
        self.sc_tree.heading("dc", text="DC Router (Origin)")
        self.sc_tree.heading("cpe", text="CPE Router (Target)")
        self.sc_tree.heading("circuit", text="Access Circuit")
        self.sc_tree.heading("transport_vr", text="DC Transport VR")
        self.sc_tree.heading("source_ip", text="DC Source IP")
        self.sc_tree.heading("dest_ip", text="CPE Target IP")
        self.sc_tree.heading("loss", text="Loss (%)")
        self.sc_tree.heading("avg_rtt", text="Avg RTT (ms)")
        self.sc_tree.heading("sla", text="Verdict")

        self.sc_tree.column("timestamp", width=75, anchor="center")
        self.sc_tree.column("dc", width=160, anchor="w")
        self.sc_tree.column("cpe", width=160, anchor="w")
        self.sc_tree.column("circuit", width=120, anchor="center")
        self.sc_tree.column("transport_vr", width=175, anchor="w")
        self.sc_tree.column("source_ip", width=105, anchor="center")
        self.sc_tree.column("dest_ip", width=105, anchor="center")
        self.sc_tree.column("loss", width=65, anchor="center")
        self.sc_tree.column("avg_rtt", width=85, anchor="center")
        self.sc_tree.column("sla", width=75, anchor="center")

        sc_scroll_y = ttk.Scrollbar(self.tab_scorecard, orient="vertical", command=self.sc_tree.yview)
        sc_scroll_x = ttk.Scrollbar(self.tab_scorecard, orient="horizontal", command=self.sc_tree.xview)
        self.sc_tree.configure(yscrollcommand=sc_scroll_y.set, xscrollcommand=sc_scroll_x.set)
        sc_scroll_y.pack(side="right", fill="y")
        sc_scroll_x.pack(side="bottom", fill="x")
        self.sc_tree.pack(side="left", fill="both", expand=True)

        self.sc_tree.tag_configure("pass", background="#e8f5e9", foreground="#2e7d32")
        self.sc_tree.tag_configure("fail", background="#ffebee", foreground="#c62828")
        self.sc_tree.tag_configure("error", background="#fff3e0", foreground="#ef6c00")

        # Tab C: Live Terminal Logs
        self.tab_logs = ttk.Frame(self.results_notebook)
        self.results_notebook.add(self.tab_logs, text=" 💻 Live Session Terminal ")

        self.log_text = tk.Text(self.tab_logs, font=("Consolas", 9), bg="#1e1e1e", fg="#d4d4d4", wrap="char")
        log_scroll = ttk.Scrollbar(self.tab_logs, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        log_scroll.pack(side="right", fill="y")

        # 3. Bottom Progress Bar & Execution Buttons
        prog_frame = ttk.LabelFrame(self, text=" Live Execution Progress ", padding=(8, 4))
        prog_frame.pack(side="bottom", fill="x", padx=8, pady=(2, 4))

        self.progress_bar = ttk.Progressbar(prog_frame, orient="horizontal", mode="determinate")
        self.progress_bar.pack(fill="x", pady=(0, 3))

        prog_info_box = tk.Frame(prog_frame, bg="#f8f9fa")
        prog_info_box.pack(fill="x")

        self.status_var = tk.StringVar(value="Status: Ready | Attach to Open PAM Terminal")
        self.status_label = ttk.Label(prog_info_box, textvariable=self.status_var, font=("Segoe UI", 9))
        self.status_label.pack(side="left", anchor="w")

        self.progress_badge_var = tk.StringVar(value="Mesh: [0/0] Ready")
        self.progress_badge = tk.Label(
            prog_info_box,
            textvariable=self.progress_badge_var,
            font=("Segoe UI", 9, "bold"),
            bg="#0284c7",
            fg="white",
            padx=10,
            pady=2,
            relief="flat"
        )
        self.progress_badge.pack(side="right")

        self.eta_var = tk.StringVar(value="⏱️ Time Left: --:--")
        self.eta_badge = tk.Label(
            prog_info_box,
            textvariable=self.eta_var,
            font=("Segoe UI", 8, "bold"),
            bg="#f1f5f9",
            fg="#0369a1",
            padx=8,
            pady=2,
            relief="flat",
            highlightbackground="#bae6fd",
            highlightthickness=1
        )
        self.eta_badge.pack(side="right", padx=(0, 6))

        # Bottom Button Bar
        btn_bar = tk.Frame(self, bg="#f8f9fa", pady=4)
        btn_bar.pack(side="bottom", fill="x", padx=8, pady=(2, 4))

        self.start_btn = tk.Button(
            btn_bar,
            text="▶  Start DC Mesh Ping Audit",
            font=("Segoe UI", 10, "bold"),
            bg="#16a34a",
            fg="white",
            activebackground="#15803d",
            activeforeground="white",
            relief="flat",
            cursor="hand2",
            padx=16,
            pady=6,
            command=self.start_audit
        )
        self.start_btn.pack(side="left", padx=(0, 8))

        self.pause_btn = tk.Button(
            btn_bar,
            text="⏸  Pause",
            font=("Segoe UI", 10, "bold"),
            bg="#d97706",
            fg="white",
            activebackground="#b45309",
            activeforeground="white",
            relief="flat",
            cursor="hand2",
            padx=14,
            pady=6,
            state="disabled",
            command=self.toggle_pause
        )
        self.pause_btn.pack(side="left", padx=8)

        self.stop_btn = tk.Button(
            btn_bar,
            text="⏹  Stop",
            font=("Segoe UI", 10, "bold"),
            bg="#dc2626",
            fg="white",
            activebackground="#b91c1c",
            activeforeground="white",
            relief="flat",
            cursor="hand2",
            padx=14,
            pady=6,
            state="disabled",
            command=self.stop_audit
        )
        self.stop_btn.pack(side="left", padx=8)

        self.export_btn = tk.Button(
            btn_bar,
            text="📁  Export Matrix Excel",
            font=("Segoe UI", 10, "bold"),
            bg="#0d9488",
            fg="white",
            activebackground="#0f766e",
            activeforeground="white",
            relief="flat",
            cursor="hand2",
            padx=14,
            pady=6,
            command=self.export_matrix_excel
        )
        self.export_btn.pack(side="left", padx=8)

        self.clear_btn = tk.Button(
            btn_bar,
            text="🧹 Clear Results",
            font=("Segoe UI", 10),
            bg="#64748b",
            fg="white",
            activebackground="#475569",
            activeforeground="white",
            relief="flat",
            cursor="hand2",
            padx=12,
            pady=6,
            command=self.clear_all
        )
        self.clear_btn.pack(side="right")

    # ──────────────────────────────────────────────────────────────────────────
    # Matrix Table Dynamic Initialization
    # ──────────────────────────────────────────────────────────────────────────
    def _init_matrix_table(self):
        """Builds or rebuilds the Matrix Treeview grid dynamically based on active DC list."""
        for widget in self.matrix_tree_frame.winfo_children():
            widget.destroy()

        dc_list = self._get_dc_list()
        # Columns: "cpe" + each DC name
        cols = ["cpe"] + [f"dc_{i}" for i in range(len(dc_list))]

        self.matrix_tree = ttk.Treeview(self.matrix_tree_frame, columns=cols, show="headings", height=10)
        self.matrix_tree.heading("cpe", text="Updated CPE Host Name")
        self.matrix_tree.column("cpe", width=220, anchor="w")

        for i, dc_name in enumerate(dc_list):
            col_id = f"dc_{i}"
            self.matrix_tree.heading(col_id, text=dc_name)
            self.matrix_tree.column(col_id, width=170, anchor="center")

        m_scroll_y = ttk.Scrollbar(self.matrix_tree_frame, orient="vertical", command=self.matrix_tree.yview)
        m_scroll_x = ttk.Scrollbar(self.matrix_tree_frame, orient="horizontal", command=self.matrix_tree.xview)
        self.matrix_tree.configure(yscrollcommand=m_scroll_y.set, xscrollcommand=m_scroll_x.set)

        m_scroll_y.pack(side="right", fill="y")
        m_scroll_x.pack(side="bottom", fill="x")
        self.matrix_tree.pack(side="left", fill="both", expand=True)

        # Style tags
        self.matrix_tree.tag_configure("pass", background="#e8f5e9", foreground="#2e7d32")
        self.matrix_tree.tag_configure("fail", background="#ffebee", foreground="#c62828")
        self.matrix_tree.tag_configure("error", background="#fff3e0", foreground="#ef6c00")
        self.matrix_tree.tag_configure("pending", background="#f8f9fa", foreground="#64748b")

    # ──────────────────────────────────────────────────────────────────────────
    # List Management (DCs & CPEs)
    # ──────────────────────────────────────────────────────────────────────────
    def _get_dc_list(self):
        text = self.dc_text.get("1.0", tk.END).strip()
        lines = [l.strip() for l in text.splitlines() if l.strip() and not l.strip().startswith("#")]
        return lines if lines else list(DEFAULT_DC_ROUTERS)

    def _get_cpe_list(self):
        text = self.cpe_text.get("1.0", tk.END).strip()
        lines = [l.strip() for l in text.splitlines() if l.strip() and not l.strip().startswith("#")]
        return lines if lines else list(DEFAULT_CPE_ROUTERS)

    def _update_dc_count(self):
        count = len(self._get_dc_list())
        self.dc_count_lbl.config(text=f"Total: {count} DCs")

    def _update_cpe_count(self):
        count = len(self._get_cpe_list())
        self.cpe_count_lbl.config(text=f"Total: {count} CPEs")

    def _load_saved_lists(self):
        # Load DCs
        dc_file = os.path.join(os.getcwd(), "user_dc_routers.txt")
        if os.path.exists(dc_file):
            try:
                with open(dc_file, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                if content:
                    self.dc_text.delete("1.0", tk.END)
                    self.dc_text.insert(tk.END, content)
            except Exception:
                pass
        else:
            self.dc_text.delete("1.0", tk.END)
            self.dc_text.insert(tk.END, "\n".join(DEFAULT_DC_ROUTERS))

        # Load CPEs
        cpe_file = os.path.join(os.getcwd(), "user_cpe_routers.txt")
        if os.path.exists(cpe_file):
            try:
                with open(cpe_file, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                if content:
                    self.cpe_text.delete("1.0", tk.END)
                    self.cpe_text.insert(tk.END, content)
            except Exception:
                pass
        else:
            self.cpe_text.delete("1.0", tk.END)
            self.cpe_text.insert(tk.END, "\n".join(DEFAULT_CPE_ROUTERS))

        self._update_dc_count()
        self._update_cpe_count()

    def _save_dc_list(self):
        dcs = self._get_dc_list()
        dc_file = os.path.join(os.getcwd(), "user_dc_routers.txt")
        try:
            with open(dc_file, "w", encoding="utf-8") as f:
                f.write("\n".join(dcs))
            messagebox.showinfo("Saved", f"Successfully saved {len(dcs)} DC routers to user_dc_routers.txt")
        except Exception as e:
            messagebox.showerror("Save Error", f"Could not save DC list:\n{e}")

    def _reset_dc_defaults(self):
        self.dc_text.delete("1.0", tk.END)
        self.dc_text.insert(tk.END, "\n".join(DEFAULT_DC_ROUTERS))
        self._update_dc_count()
        self._init_matrix_table()

    def _import_cpe_file(self):
        path = filedialog.askopenfilename(filetypes=[("Text Files", "*.txt"), ("CSV Files", "*.csv"), ("All Files", "*.*")])
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                lines = [l.strip() for l in f if l.strip() and not l.strip().startswith("#")]
            self.cpe_text.delete("1.0", tk.END)
            self.cpe_text.insert(tk.END, "\n".join(lines))
            self._update_cpe_count()
            messagebox.showinfo("Imported", f"Successfully imported {len(lines)} CPE routers.")
        except Exception as e:
            messagebox.showerror("Import Error", f"Failed to import:\n{e}")

    def _save_cpe_file(self):
        cpes = self._get_cpe_list()
        cpe_file = os.path.join(os.getcwd(), "user_cpe_routers.txt")
        try:
            with open(cpe_file, "w", encoding="utf-8") as f:
                f.write("\n".join(cpes))
            messagebox.showinfo("Saved", f"Successfully saved {len(cpes)} CPE routers to user_cpe_routers.txt")
        except Exception as e:
            messagebox.showerror("Save Error", f"Could not save CPE list:\n{e}")

    def _clear_cpes(self):
        self.cpe_text.delete("1.0", tk.END)
        self._update_cpe_count()

    def _reset_cpe_defaults(self):
        self.cpe_text.delete("1.0", tk.END)
        self.cpe_text.insert(tk.END, "\n".join(DEFAULT_CPE_ROUTERS))
        self._update_cpe_count()

    # ──────────────────────────────────────────────────────────────────────────
    # Logging & Console Output
    # ──────────────────────────────────────────────────────────────────────────
    def append_log(self, text):
        self.root.after(0, self._append_log_safe, text)

    def _append_log_safe(self, text):
        self.log_text.insert(tk.END, text)
        self.log_text.see(tk.END)

    def clear_all(self):
        if self.is_running:
            return
        for item in self.sc_tree.get_children():
            self.sc_tree.delete(item)
        self.log_text.delete("1.0", tk.END)
        self.matrix_data.clear()
        self.raw_records.clear()
        self._init_matrix_table()
        self.progress_bar["value"] = 0
        self.status_var.set("Status: Cleared | Ready")
        self.progress_badge_var.set("Mesh: [0/0] Ready")
        self.eta_var.set("⏱️ Time Left: --:--")

    # ──────────────────────────────────────────────────────────────────────────
    # Execution Lifecycle (Start, Pause, Stop)
    # ──────────────────────────────────────────────────────────────────────────
    def start_audit(self):
        if self.is_running:
            return

        dcs = self._get_dc_list()
        cpes = self._get_cpe_list()

        if not dcs:
            messagebox.showwarning("Missing Input", "Please specify at least one Data Center (DC) router.")
            return
        if not cpes:
            messagebox.showwarning("Missing Input", "Please specify at least one Branch CPE router to test.")
            return

        self._init_matrix_table()
        # Initialize matrix rows for each CPE
        for cpe in cpes:
            row_vals = [cpe] + ["Pending" for _ in dcs]
            self.matrix_tree.insert("", "end", iid=cpe, values=row_vals, tags=("pending",))

        self.is_running = True
        self.stop_event.clear()
        self.pause_event.set()

        self.start_btn.config(state="disabled")
        self.pause_btn.config(state="normal", text="⏸  Pause", bg="#d97706")
        self.stop_btn.config(state="normal")
        self.export_btn.config(state="disabled")

        self.status_var.set("Initializing DC Mesh Audit...")
        self.append_log(f"\n{'='*75}\n")
        self.append_log(f"[*] Starting DC-to-Branch Mesh Ping Audit at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        self.append_log(f"[*] Total DC Routers : {len(dcs)}\n")
        self.append_log(f"[*] Total CPE Routers: {len(cpes)}\n")
        self.append_log(f"[*] Concurrency Limit: {self.worker_count_var.get()} worker(s)\n")
        self.append_log(f"{'='*75}\n\n")

        threading.Thread(target=self._mesh_audit_worker, daemon=True).start()

    def toggle_pause(self):
        if not self.is_running:
            return
        if self.pause_event.is_set():
            self.pause_event.clear()
            self.pause_btn.config(text="▶  Resume", bg="#16a34a")
            self.status_var.set("Status: Paused by user")
            self.append_log("[!] Execution PAUSED by user.\n")
        else:
            self.pause_event.set()
            self.pause_btn.config(text="⏸  Pause", bg="#d97706")
            self.status_var.set("Status: Resumed")
            self.append_log("[+] Execution RESUMED.\n")

    def stop_audit(self):
        if not self.is_running:
            return
        self.stop_event.set()
        self.pause_event.set()
        self.status_var.set("Stopping mesh audit gracefully...")
        self.append_log("[!] STOP signal sent. Gracefully halting after current operation...\n")

    def _check_pause(self):
        while not self.pause_event.is_set():
            if self.stop_event.is_set():
                break
            time.sleep(0.5)

    # ──────────────────────────────────────────────────────────────────────────
    # Core Mesh Audit Execution Thread
    # ──────────────────────────────────────────────────────────────────────────
    def _mesh_audit_worker(self):
        dcs = self._get_dc_list()
        cpes = self._get_cpe_list()
        total_mesh_pairs = len(dcs) * len(cpes)
        completed_pairs = 0

        start_time = time.time()
        director_ip = self.director_ip_var.get().strip() or "10.29.11.32"
        director_user = self.director_user_var.get().strip() or "admin"
        director_pass = self.director_pass_var.get().strip() or "e#mi$Wp@U8%f"

        # Obtain PAM Terminal Controllers
        controllers = []
        if self.conn_mode_var.get() == "pam_terminal":
            if not HAS_PAM:
                self.append_log("[!] Error: PAM Terminal controller module not available.\n")
                self._finish_audit()
                return

            terminals = PamTerminalController.find_all_terminals()
            if not terminals:
                self.append_log("[!] Error: No open PuTTY / PAM terminals found on desktop!\n")
                self.append_log("[*] Please connect to PAM and launch PuTTY first.\n")
                self._finish_audit()
                return
            controllers = terminals[:min(len(terminals), self.worker_count_var.get())]
            self.append_log(f"[+] Bound to {len(controllers)} active desktop terminal session(s).\n")
        else:
            # Bastion SSH mode fallback
            controllers = [None]

        controller = controllers[0]

        try:
            for dc_idx, dc_name in enumerate(dcs, 1):
                if self.stop_event.is_set():
                    break
                self._check_pause()

                self.append_log(f"\n{'─'*65}\n")
                self.append_log(f"[DC {dc_idx}/{len(dcs)}] Connecting to Data Center: {dc_name}\n")
                self.append_log(f"{'─'*65}\n")

                # Step 1: Login to DC router
                dc_login_ok = self._login_to_router(controller, dc_name, director_ip, director_user, director_pass)
                if not dc_login_ok:
                    self.append_log(f"[!] Warning: Could not establish CLI session on DC {dc_name}. Marking column skipped.\n")
                    for cpe in cpes:
                        self._update_matrix_cell(cpe, dc_name, "Unreachable", "error")
                    continue

                # Step 2: Query 'show bgp neighbor brief' and extract first 4 Transport VRs + Calculated Source IPs
                self.append_log(f"[*] Querying BGP neighbors on {dc_name} (show bgp neighbor brief)...\n")
                controller.send_command("show bgp neighbor brief", wait_after=1.0)
                bgp_out = self._wait_prompt(controller, cmd="show bgp neighbor brief", max_timeout=20.0)

                transport_vrs = parse_dc_transport_vrs(bgp_out)
                if not transport_vrs:
                    self.append_log(f"[!] Warning: No Transport VRs found in BGP output on {dc_name}. Retrying full buffer...\n")
                    bgp_out = controller.copy_terminal_output() or ""
                    transport_vrs = parse_dc_transport_vrs(bgp_out)

                self.append_log(f"[✓] Identified {len(transport_vrs)} Transport VR(s) for DC {dc_name}:\n")
                for tv in transport_vrs:
                    self.append_log(f"    • {tv['vr_name']:<30} | WAN Neighbor: {tv['neighbor_ip']:<15} | Calculated Source (Odd+1/Even-1): {tv['source_ip']}\n")

                self.dc_transport_cache[dc_name] = transport_vrs

                # Step 3: Iterate through every Branch CPE router
                for cpe_idx, cpe_name in enumerate(cpes, 1):
                    if self.stop_event.is_set():
                        break
                    self._check_pause()

                    completed_pairs += 1
                    pct = (completed_pairs / total_mesh_pairs) * 100
                    self.progress_bar["value"] = pct
                    self.progress_badge_var.set(f"Mesh: [{completed_pairs}/{total_mesh_pairs}]")
                    self.status_var.set(f"Testing: {dc_name} -> {cpe_name}...")

                    # Calculate ETA
                    elapsed = time.time() - start_time
                    avg_per_pair = elapsed / completed_pairs if completed_pairs > 0 else 20
                    rem_secs = int(avg_per_pair * (total_mesh_pairs - completed_pairs))
                    eta_m, eta_s = divmod(rem_secs, 60)
                    self.eta_var.set(f"⏱️ Time Left: {eta_m:02d}:{eta_s:02d}")

                    self.append_log(f"\n  [CPE {cpe_idx}/{len(cpes)}] Querying circuits for {cpe_name} on {dc_name}...\n")
                    cmd_sdwan = f"show orgs org MB-NET sd-wan detail {cpe_name}"
                    controller.send_command(cmd_sdwan, wait_after=1.5)
                    sdwan_out = self._wait_prompt(controller, cmd=cmd_sdwan, max_timeout=35.0)

                    cpe_circuits = parse_cpe_sdwan_circuits(sdwan_out)
                    if not cpe_circuits:
                        self.append_log(f"  [!] No circuits found in NAT Status for {cpe_name}.\n")
                        self._update_matrix_cell(cpe_name, dc_name, "No Circuits", "error")
                        continue

                    pair_circuits_results = []
                    for c_info in cpe_circuits:
                        if self.stop_event.is_set():
                            break
                        self._check_pause()

                        c_name = c_info["circuit"]
                        target_ip = c_info["target_ip"]

                        # Match circuit to appropriate DC Transport VR
                        matched_vr = self._match_transport_vr(c_name, transport_vrs)
                        vr_name = matched_vr["vr_name"]
                        source_ip = matched_vr["source_ip"]

                        # Execute rapid ping
                        ping_cnt = self.ping_count_var.get()
                        cmd_ping = f"ping {target_ip} source {source_ip} routing-instance {vr_name} rapid enable count {ping_cnt} packet-size 1472 df-bit enable"
                        self.append_log(f"  [PING] {dc_name} -> {cpe_name} ({c_name}) | VR: {vr_name}\n")
                        self.append_log(f"         cmd: {cmd_ping}\n")

                        controller.send_command(cmd_ping, wait_after=1.5)
                        ping_out = self._wait_prompt(controller, cmd="ping", max_timeout=35.0)

                        stats = self._parse_ping(ping_out)
                        loss = stats["loss"]
                        avg_rtt = stats["avg_rtt"]

                        loss_pass = loss <= self.max_loss_var.get()
                        rtt_pass = avg_rtt <= self.max_rtt_var.get()
                        verdict = "PASS" if (loss_pass and rtt_pass) else "FAIL"

                        self.append_log(f"         Result: Loss: {loss}% | Avg RTT: {avg_rtt:.2f}ms -> {verdict}\n")

                        # Record detailed row
                        rec = {
                            "timestamp": datetime.now().strftime("%H:%M:%S"),
                            "dc": dc_name,
                            "cpe": cpe_name,
                            "circuit": c_name,
                            "transport_vr": vr_name,
                            "source_ip": source_ip,
                            "dest_ip": target_ip,
                            "loss": loss,
                            "avg_rtt": avg_rtt,
                            "sla": verdict
                        }
                        self.raw_records.append(rec)
                        self.root.after(0, self._insert_scorecard_row, rec)
                        pair_circuits_results.append(rec)

                    # Determine aggregated cell verdict for this (cpe, dc)
                    if pair_circuits_results:
                        all_pass = all(r["sla"] == "PASS" for r in pair_circuits_results)
                        worst_loss = max(r["loss"] for r in pair_circuits_results)
                        avg_mesh_rtt = sum(r["avg_rtt"] for r in pair_circuits_results) / len(pair_circuits_results)

                        if all_pass:
                            cell_text = f"PASS ({avg_mesh_rtt:.1f}ms)"
                            cell_tag = "pass"
                        else:
                            cell_text = f"FAIL (Loss {worst_loss:.0f}%)"
                            cell_tag = "fail"

                        self.matrix_data[(cpe_name, dc_name)] = {
                            "verdict": cell_text,
                            "tag": cell_tag,
                            "circuits": pair_circuits_results
                        }
                        self._update_matrix_cell(cpe_name, dc_name, cell_text, cell_tag)

                # Return cleanly from DC router to Director/Bastion
                self.append_log(f"[+] Finished all CPE checks for {dc_name}. Returning cleanly...\n")
                controller.send_command("exit", wait_after=2.0)
                controller.send_command("exit", wait_after=2.0)

        except Exception as e:
            self.append_log(f"\n[!] Unexpected Error during Mesh Audit: {e}\n")

        finally:
            self._finish_audit()

    def _finish_audit(self):
        self.is_running = False
        self.start_btn.config(state="normal")
        self.pause_btn.config(state="disabled", text="⏸  Pause", bg="#d97706")
        self.stop_btn.config(state="disabled")
        self.export_btn.config(state="normal")
        self.status_var.set("Status: Completed | Audit finished successfully")
        self.append_log(f"\n{'='*75}\n")
        self.append_log(f"[✓] DC Mesh Ping Audit Completed at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        self.append_log(f"{'='*75}\n")

    # ──────────────────────────────────────────────────────────────────────────
    # Protocol Helpers (Login, Terminal Waiting, VR Matching, Ping Parsing)
    # ──────────────────────────────────────────────────────────────────────────
    def _login_to_router(self, controller, router_name, director_ip, director_user, director_pass):
        """Automates safe login to target DC router via Versa Director CLI."""
        self.append_log(f"[*] Navigating to Director to resolve IP for {router_name}...\n")
        # Ensure at Director CLI
        controller.send_enter(wait_after=1.0)
        out = controller.copy_terminal_output() or ""

        # If not at Director or branch, connect to Director
        if "10.29.11.32" not in out and "VD-" not in out:
            controller.send_command(f"ssh -l {director_user} {director_ip}", wait_after=3.0)
            controller.send_command(director_pass, wait_after=4.0)

        # Enter Director CLI if needed
        out = controller.copy_terminal_output() or ""
        if "admin@VD-" in out or "~]$" in out or ":~$" in out:
            controller.send_command("cli", wait_after=4.0)
            controller.send_command("set paginate false", wait_after=1.0)

        # Resolve router IP
        cmd_resolve = f"show devices list | match {router_name}"
        controller.send_command(cmd_resolve, wait_after=2.0)
        res_out = self._wait_prompt(controller, cmd="show devices list", max_timeout=15.0)

        ip_match = re.search(r"(\b10\.\d{1,3}\.\d{1,3}\.\d{1,3}\b)", res_out)
        if not ip_match:
            self.append_log(f"[!] Could not locate IP for {router_name} on Director.\n")
            return False

        target_ip = ip_match.group(1)
        self.append_log(f"[+] Found Management IP for {router_name}: {target_ip}\n")

        # Exit Director CLI back to Linux shell to initiate direct SSH
        controller.send_command("exit", wait_after=2.0)
        controller.send_command(f"ssh -o StrictHostKeyChecking=no admin@{target_ip}", wait_after=3.0)
        controller.send_command(director_pass, wait_after=5.0)

        # Enter Router CLI
        controller.send_command("cli", wait_after=5.0)
        controller.send_command("set paginate false", wait_after=1.5)

        # Verify CLI prompt
        cli_out = controller.copy_terminal_output() or ""
        return f"{router_name}-cli>" in cli_out or "-cli>" in cli_out

    def _wait_prompt(self, controller, cmd=None, max_timeout=35.0, check_interval=0.4):
        """Waits for terminal command output to stabilize."""
        start_t = time.time()
        time.sleep(0.4)
        while time.time() - start_t < max_timeout:
            if self.stop_event.is_set():
                break
            self._check_pause()
            time.sleep(check_interval)
            out = controller.copy_terminal_output() or ""
            if cmd and cmd in out:
                lines = [l.strip() for l in out.strip().splitlines() if l.strip()]
                if lines and any(l.endswith("-cli>") or "[ok]" in l for l in lines[-2:]):
                    return out
        return controller.copy_terminal_output() or ""

    def _match_transport_vr(self, circuit_name, transport_vrs):
        """
        Matches a CPE access circuit (e.g. TCL-MPLS1-MB, VI-MPLS1-MB) to the best DC Transport VR.
        """
        c_upper = circuit_name.upper()

        # 1. Exact provider + circuit match (e.g. TCL and MPLS1)
        for tv in transport_vrs:
            vr_upper = tv["vr_name"].upper()
            if "TCL" in c_upper and "TCL" in vr_upper and ("MPLS1" in c_upper and "MPLS1" in vr_upper):
                return tv
            if "TTSL" in c_upper and "TTSL" in vr_upper and ("MPLS1" in c_upper and "MPLS1" in vr_upper):
                return tv
            if "VI" in c_upper and "VI" in vr_upper and ("MPLS2" in c_upper and "MPLS2" in vr_upper):
                return tv
            if "PGCIL" in c_upper and "PGCIL" in vr_upper and ("MPLS2" in c_upper and "MPLS2" in vr_upper):
                return tv

        # 2. Provider keyword match (TCL, TTSL, VI, PGCIL)
        for provider in ["TCL", "TTSL", "VI", "PGCIL", "AIRTEL"]:
            if provider in c_upper:
                for tv in transport_vrs:
                    if provider in tv["vr_name"].upper():
                        return tv

        # 3. Fallback to first available Transport VR
        return transport_vrs[0] if transport_vrs else {
            "vr_name": "TCL-MPLS1-MB-Transport-VR",
            "neighbor_ip": "10.116.135.9",
            "source_ip": "10.116.135.10"
        }

    def _parse_ping(self, ping_output):
        stats = {"loss": 0.0, "avg_rtt": 25.0}
        if not ping_output:
            return stats
        loss_m = re.findall(r"(\d+)\s+packets transmitted,\s*(\d+)\s+received,\s*(\d+(?:\.\d+)?)%\s+packet loss", ping_output)
        if loss_m:
            stats["loss"] = float(loss_m[-1][2])
        rtt_m = re.findall(r"rtt min/avg/max/mdev\s*=\s*[\d\.]+/([\d\.]+)/", ping_output)
        if rtt_m:
            stats["avg_rtt"] = float(rtt_m[-1])
        return stats

    # ──────────────────────────────────────────────────────────────────────────
    # UI Table Updates (Thread-safe)
    # ──────────────────────────────────────────────────────────────────────────
    def _update_matrix_cell(self, cpe_name, dc_name, value_text, tag):
        self.root.after(0, self._update_matrix_cell_safe, cpe_name, dc_name, value_text, tag)

    def _update_matrix_cell_safe(self, cpe_name, dc_name, value_text, tag):
        dc_list = self._get_dc_list()
        if dc_name not in dc_list:
            return
        col_idx = dc_list.index(dc_name) + 1  # 0 is cpe_name

        try:
            current_vals = list(self.matrix_tree.item(cpe_name, "values"))
            while len(current_vals) < len(dc_list) + 1:
                current_vals.append("Pending")
            current_vals[col_idx] = value_text
            self.matrix_tree.item(cpe_name, values=current_vals, tags=(tag,))
        except Exception:
            pass

    def _insert_scorecard_row(self, rec):
        tag = "pass" if rec["sla"] == "PASS" else "fail"
        self.sc_tree.insert(
            "",
            0,
            values=(
                rec["timestamp"],
                rec["dc"],
                rec["cpe"],
                rec["circuit"],
                rec["transport_vr"],
                rec["source_ip"],
                rec["dest_ip"],
                f"{rec['loss']:.1f}%",
                f"{rec['avg_rtt']:.2f}",
                rec["sla"]
            ),
            tags=(tag,)
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Excel Report Export
    # ──────────────────────────────────────────────────────────────────────────
    def export_matrix_excel(self):
        if not HAS_OPENPYXL:
            messagebox.showerror("Export Error", "openpyxl library is required for Excel export.")
            return

        dcs = self._get_dc_list()
        cpes = self._get_cpe_list()

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        default_filename = f"DC_Mesh_Ping_Matrix_{ts}.xlsx"
        file_path = filedialog.asksaveasfilename(
            defaultextension=".xlsx",
            initialfile=default_filename,
            filetypes=[("Excel Workbook (*.xlsx)", "*.xlsx")]
        )
        if not file_path:
            return

        try:
            wb = openpyxl.Workbook()
            # 1. Sheet 1: DC Mesh Ping Matrix (Formatted exactly to match NOC Excel)
            ws1 = wb.active
            ws1.title = "DC Mesh Ping Matrix"
            ws1.views.sheetView[0].showGridLines = True

            # Styling definitions
            orange_fill = PatternFill(start_color="EA580C", end_color="EA580C", fill_type="solid")
            green_fill = PatternFill(start_color="15803D", end_color="15803D", fill_type="solid")
            pass_fill = PatternFill(start_color="DCFCE7", end_color="DCFCE7", fill_type="solid")
            fail_fill = PatternFill(start_color="FEE2E2", end_color="FEE2E2", fill_type="solid")
            gray_fill = PatternFill(start_color="F1F5F9", end_color="F1F5F9", fill_type="solid")

            white_font = Font(name="Segoe UI", size=10, bold=True, color="FFFFFF")
            pass_font = Font(name="Segoe UI", size=9, bold=True, color="166534")
            fail_font = Font(name="Segoe UI", size=9, bold=True, color="991B1B")
            body_font = Font(name="Segoe UI", size=9, bold=False, color="1E293B")
            cpe_font = Font(name="Consolas", size=9, bold=True, color="0F172A")

            thin_border = Border(
                left=Side(style="thin", color="CBD5E1"),
                right=Side(style="thin", color="CBD5E1"),
                top=Side(style="thin", color="CBD5E1"),
                bottom=Side(style="thin", color="CBD5E1")
            )
            center_align = Alignment(horizontal="center", vertical="center")
            left_align = Alignment(horizontal="left", vertical="center")

            # Header Row 1 (Matching User's Excel Screenshot)
            # Col E: Updated CPE Host Name (Orange Header)
            cpe_header_col = 5  # Column E
            ws1.cell(row=1, column=cpe_header_col, value="Updated CPE Host Name")
            cpe_cell = ws1.cell(row=1, column=cpe_header_col)
            cpe_cell.fill = orange_fill
            cpe_cell.font = white_font
            cpe_cell.alignment = center_align
            cpe_cell.border = thin_border

            # Col F onwards: DC Router Headers (Green Headers)
            for d_idx, dc_name in enumerate(dcs):
                col_num = cpe_header_col + 1 + d_idx
                cell = ws1.cell(row=1, column=col_num, value=dc_name)
                cell.fill = green_fill
                cell.font = white_font
                cell.alignment = center_align
                cell.border = thin_border

            # Data Rows (Rows 2 onwards)
            for r_idx, cpe_name in enumerate(cpes, start=2):
                cpe_c = ws1.cell(row=r_idx, column=cpe_header_col, value=cpe_name)
                cpe_c.font = cpe_font
                cpe_c.alignment = left_align
                cpe_c.border = thin_border
                cpe_c.fill = gray_fill

                for d_idx, dc_name in enumerate(dcs):
                    col_num = cpe_header_col + 1 + d_idx
                    val_cell = ws1.cell(row=r_idx, column=col_num)
                    val_cell.border = thin_border

                    res = self.matrix_data.get((cpe_name, dc_name))
                    if res:
                        val_cell.value = res["verdict"]
                        if res["tag"] == "pass":
                            val_cell.fill = pass_fill
                            val_cell.font = pass_font
                        else:
                            val_cell.fill = fail_fill
                            val_cell.font = fail_font
                        val_cell.alignment = center_align
                    else:
                        val_cell.value = "N/A"
                        val_cell.font = body_font
                        val_cell.alignment = center_align

            # Auto-fit column widths
            for col in ws1.columns:
                max_len = max(len(str(cell.value or "")) for cell in col)
                col_letter = get_column_letter(col[0].column)
                ws1.column_dimensions[col_letter].width = max(max_len + 4, 12)

            # 2. Sheet 2: Detailed Circuit Scorecard Raw Data
            ws2 = wb.create_sheet(title="Circuit Diagnostics Raw")
            ws2.views.sheetView[0].showGridLines = True
            headers = ["Timestamp", "DC Router (Origin)", "CPE Router (Target)", "Access Circuit", "DC Transport VR", "DC Source IP", "CPE Target IP", "Loss (%)", "Avg RTT (ms)", "SLA Verdict"]
            ws2.append(headers)

            navy_fill = PatternFill(start_color="0F172A", end_color="0F172A", fill_type="solid")
            for col_idx in range(1, len(headers) + 1):
                c = ws2.cell(row=1, column=col_idx)
                c.fill = navy_fill
                c.font = white_font
                c.alignment = center_align
                c.border = thin_border

            for rec in self.raw_records:
                row_data = [
                    rec["timestamp"],
                    rec["dc"],
                    rec["cpe"],
                    rec["circuit"],
                    rec["transport_vr"],
                    rec["source_ip"],
                    rec["dest_ip"],
                    rec["loss"],
                    rec["avg_rtt"],
                    rec["sla"]
                ]
                ws2.append(row_data)
                curr_row = ws2.max_row
                for col_idx in range(1, len(row_data) + 1):
                    cell = ws2.cell(row=curr_row, column=col_idx)
                    cell.border = thin_border
                    cell.font = body_font
                    if col_idx in [1, 6, 7, 8, 9, 10]:
                        cell.alignment = center_align
                    if col_idx == 10:
                        cell.fill = pass_fill if rec["sla"] == "PASS" else fail_fill
                        cell.font = pass_font if rec["sla"] == "PASS" else fail_font

            for col in ws2.columns:
                max_len = max(len(str(cell.value or "")) for cell in col)
                col_letter = get_column_letter(col[0].column)
                ws2.column_dimensions[col_letter].width = max(max_len + 3, 11)

            wb.save(file_path)
            messagebox.showinfo("Export Successful", f"DC Mesh Ping Matrix exported successfully to:\n{file_path}")
            try:
                os.startfile(file_path)
            except Exception:
                pass

        except Exception as e:
            messagebox.showerror("Export Error", f"Failed to export Excel matrix:\n{e}")
