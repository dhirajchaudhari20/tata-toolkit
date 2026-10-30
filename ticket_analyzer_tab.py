"""
====================================================================================================
                        TATA COMMUNICATIONS - NETWORK OPERATIONS CENTER
====================================================================================================
Module: ticket_analyzer_tab.py
Purpose:
  Automated Ticket / Alert Analyzer & Diagnostic Dispatcher.
  1. Parses incoming NMS alerts & ticket emails (e.g. infinet_nms_alerts@rbi.org.in).
  2. Extracts Ticket Number, Location, Hostname, Circuit, Transport VR, Peer IP, Circuit ID.
  3. Connects to Versa Director via open PuTTY / ARCON PAM Terminal session.
  4. Discovers Management IP of the target Branch router and SSH logs in.
  5. Selectively runs NOC verification commands based on checkboxes:
     - [x] Port / Interface Status ('show interfaces brief')
     - [x] Specific Transport VR BGP ('show bgp neighbor brief <VR>')
     - [x] All BGP Neighbors ('show bgp neighbor brief')
     - [x] BGP Alarms ('show alarms last-n 30 | match bgp')
     - [x] Peer IP Alarms ('show alarms last-n 30 | match <peer_ip>')
     - [x] VNI Interface Alarms ('show alarms last-n 30 | match vni')
     - [x] Rapid Ping Diagnostics ('ping <peer_ip> source <source_ip> routing-instance <VR>')
     - [x] System & Service Uptime ('show system uptime')
  6. Captures live terminal output/logs into both Console Log and dedicated output sections.
  7. Formats an executive, professional NOC Customer Update Message ready for 1-click copy-paste.
====================================================================================================
"""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import threading
import time
import re
import os
import sys
import pyperclip
from datetime import datetime

try:
    from pam_terminal_controller import PamTerminalController
    HAS_PAM = True
except ImportError:
    HAS_PAM = False

try:
    from sleep_preventer import sleep_preventer
except ImportError:
    sleep_preventer = None


# Sample template for quick lab verification
DEFAULT_SAMPLE_EMAIL = """infinet_nms_alerts@rbi.org.in

Shreyansh Jain;TCL.IFTAS-INFINET3;mitul.mahajan@ttcbank.co.in;infinet_mb.help@iftas.in;banknet.support@iftas.in
CAUTION: This email originated from outside of the organization. Do not click links or open attachments unless you recognize the sender and know the content is safe.

Hi,

Please find ticket details below:

Ticket Details
TICKET NUMBER\tIN202610300104
LOCATION\tAhmedabad
CONFIGURATION ITEM\t1290-TTCCOOP-AHMNVRGPRA-R1||TCL-MPLS1-MB||BGP
TITLE\tProblem: 1290-TTCCOOP-AHMNVRGPRA-R1 BGP: peer (10.116.196.201) on transport TCL-MPLS1-MB-Transport-VR is down
PRESENT STATUS\tIn Progress
COMMENTS\tSelf-assigned by user
RESOLUTION REMARKS\t
RESOLUTION TYPE\t
RFO SPECIFICATION\t
RFO\t
PRIORITY\t2 - High
ISP CIRCUIT ID\t091AHME6230A0417729
CREATED BY\tiftassupportnms@tatacommunications.com
CREATED DATE\t2026-10-30 10:19:38
LAST UPDATED DATE\t2026-10-30 10:20:44
ALARM START DATE\t2026-10-30 10:15:03
ALARM END DATE\t
GROUP NAME\tIFTAS Support
STATUS REMARK\t
ASSIGN TO\tsakshi.patil@contractor.tatacommunications.com"""


def parse_ticket_email(raw_text):
    """
    Parses unstructured ticket / alert email text into a structured dictionary.
    """
    info = {
        "ticket_number": "",
        "location": "",
        "configuration_item": "",
        "title": "",
        "circuit_id": "",
        "priority": "",
        "created_date": "",
        "alarm_start_date": "",
        "assigned_to": "",
        "hostname": "",
        "circuit": "",
        "peer_ip": "",
        "transport_vr": "",
        "local_source_ip": ""
    }
    if not raw_text:
        return info

    patterns = {
        "ticket_number": r"TICKET\s*NUMBER\s*[\t:]*\s*([^\r\n]+)",
        "location": r"LOCATION\s*[\t:]*\s*([^\r\n]+)",
        "configuration_item": r"CONFIGURATION\s*ITEM\s*[\t:]*\s*([^\r\n]+)",
        "title": r"TITLE\s*[\t:]*\s*([^\r\n]+)",
        "priority": r"PRIORITY\s*[\t:]*\s*([^\r\n]+)",
        "circuit_id": r"ISP\s*CIRCUIT\s*ID\s*[\t:]*\s*([^\r\n]+)",
        "created_date": r"CREATED\s*DATE\s*[\t:]*\s*([^\r\n]+)",
        "alarm_start_date": r"ALARM\s*START\s*DATE\s*[\t:]*\s*([^\r\n]+)",
        "assigned_to": r"ASSIGN\s*TO\s*[\t:]*\s*([^\r\n]+)"
    }

    for key, pat in patterns.items():
        m = re.search(pat, raw_text, re.IGNORECASE)
        if m:
            info[key] = m.group(1).strip()

    # Direct Configuration Item or Pattern Match: -HOST||CIRCUIT or HOST||CIRCUIT
    m_pipe = re.search(r"([A-Za-z0-9_-]+)\s*\|\|\s*([A-Za-z0-9_-]+)", raw_text)
    if m_pipe:
        cand_host = m_pipe.group(1).lstrip("-").strip()
        cand_circ = m_pipe.group(2).strip()
        if not info["hostname"]:
            info["hostname"] = cand_host
        if not info["circuit"]:
            info["circuit"] = cand_circ

    # NMS Alert format: "SD-WAN Datapath Down from <SOURCE_DC> to <TARGET_ROUTER>/<CIRCUIT>"
    # Example: "SD-WAN Datapath Down from VC-RBI-GRP1-02-PDC/PGCIL-MPLS-RBI to IFT-RBI-3041-AHMBRF-R1/PGCIL-MPLS2-RBI"
    m_sdwan_path = re.search(
        r"SD-WAN\s+Datapath\s+Down\s+from\s+[^\s/]+(?:/[^\s]+)?\s+to\s+([A-Za-z0-9_-]+)/([A-Za-z0-9_-]+)",
        raw_text,
        re.IGNORECASE
    )
    if m_sdwan_path:
        if not info["hostname"]:
            info["hostname"] = m_sdwan_path.group(1).strip()
        if not info["circuit"]:
            info["circuit"] = m_sdwan_path.group(2).strip()

    # Link Type field: "Link Type :- PGCIL-MPLS2-RBI" or "Link Type : PGCIL-MPLS2-RBI"
    m_link_type = re.search(r"Link\s*Type\s*[:-]+\s*([A-Za-z0-9_-]+)", raw_text, re.IGNORECASE)
    if m_link_type and not info["circuit"]:
        info["circuit"] = m_link_type.group(1).strip()

    # Management IP or Subnet: "Management IP :- 10.128.199.14/30" or "Management IP : 10.128.199.14"
    m_mgmt_ip = re.search(r"Management\s*IP\s*[:-]+\s*([0-9.]+)(?:/\d+)?", raw_text, re.IGNORECASE)
    if m_mgmt_ip:
        parsed_ip = m_mgmt_ip.group(1).strip()
        # In point-to-point /30, this IP can be the WAN Peer IP or Local IP
        if not info["peer_ip"]:
            info["peer_ip"] = parsed_ip

    # BSO ID / CKT ID: "BSO ID :- SN-VNO-02219591 // PGCIL CKT ID - 24950"
    m_bso = re.search(r"BSO\s*ID\s*[:-]+\s*([^\r\n]+)", raw_text, re.IGNORECASE)
    if m_bso and not info["circuit_id"]:
        info["circuit_id"] = m_bso.group(1).strip()

    # Problem ID / Trigger ID as Ticket Number fallback
    m_orig_prob = re.search(r"(?:Original\s*problem\s*ID|Trigger\s*Id)\s*[:=]+\s*([0-9A-Za-z_-]+)", raw_text, re.IGNORECASE)
    if m_orig_prob and not info["ticket_number"]:
        info["ticket_number"] = m_orig_prob.group(1).strip()

    # Problem started timestamp
    m_start_time = re.search(r"Problem\s*started\s*at\s*([0-9:.]+\s*on\s*[0-9.]+)", raw_text, re.IGNORECASE)
    if m_start_time and not info["alarm_start_date"]:
        info["alarm_start_date"] = m_start_time.group(1).strip()

    # Derived extraction from Configuration Item
    if info["configuration_item"]:
        parts = [p.strip() for p in info["configuration_item"].split("||")]
        if len(parts) >= 1 and parts[0]:
            info["hostname"] = parts[0]
        if len(parts) >= 2 and parts[1]:
            info["circuit"] = parts[1]

    # Derived extraction from Title
    if info["title"]:
        # Extract peer IP: peer (10.116.196.201)
        m_peer = re.search(r"peer\s*\(([0-9.]+)\)", info["title"], re.IGNORECASE)
        if m_peer:
            info["peer_ip"] = m_peer.group(1).strip()
        else:
            m_ip = re.search(r"\b(10\.\d{1,3}\.\d{1,3}\.\d{1,3})\b", info["title"])
            if m_ip:
                info["peer_ip"] = m_ip.group(1).strip()

        # Extract Transport VR: transport TCL-MPLS1-MB-Transport-VR
        m_vr = re.search(r"transport\s+([A-Za-z0-9_-]+(?:-VR)?)", info["title"], re.IGNORECASE)
        if m_vr:
            info["transport_vr"] = m_vr.group(1).strip()
            if not info["transport_vr"].endswith("-VR"):
                info["transport_vr"] += "-VR"

        # Fallback hostname from title
        if not info["hostname"]:
            m_h = re.search(r"Problem:\s*([A-Za-z0-9_-]+)", info["title"], re.IGNORECASE)
            if m_h:
                info["hostname"] = m_h.group(1).strip()

    # Derive Transport VR if not yet populated
    if not info["transport_vr"] and info["circuit"]:
        circ_name = info["circuit"].strip()
        info["transport_vr"] = f"{circ_name}-Transport-VR" if not circ_name.endswith("-VR") else circ_name

    # Calculate Local Source IP if peer IP exists (Odd/Even rule)
    if info["peer_ip"]:
        info["local_source_ip"] = calculate_peer_source_ip(info["peer_ip"])

    return info


def calculate_peer_source_ip(peer_ip):
    """
    Point-to-Point /30 subnet rule:
    If Peer WAN IP last octet is ODD -> Local WAN IP is EVEN (+1)
    If Peer WAN IP last octet is EVEN -> Local WAN IP is ODD (-1)
    """
    try:
        octs = peer_ip.strip().split(".")
        if len(octs) == 4:
            last = int(octs[3])
            adj = (last + 1) if (last % 2 != 0) else (last - 1)
            return f"{octs[0]}.{octs[1]}.{octs[2]}.{adj}"
    except Exception:
        pass
    return ""


class TicketAnalyzerFrame(ttk.Frame):
    """
    NOC Ticket / Alert Incident Diagnostics Frame with selective command checkboxes.
    """
    def __init__(self, parent, root=None, parent_gui=None):
        super().__init__(parent)
        self.root = root or parent.winfo_toplevel()
        self.parent_gui = parent_gui

        self.is_running = False
        self.stop_event = threading.Event()
        self.parsed_data = {}
        self.command_outputs = {}

        self._build_ui()

    def _build_ui(self):
        # ── 1. Top Control & Options Bar ──
        top_bar = ttk.LabelFrame(self, text=" ⚙️ Versa Director & Authentication Target ", padding=6)
        top_bar.pack(side="top", fill="x", padx=6, pady=(4, 2))

        ttk.Label(top_bar, text="Director IP:", font=("Segoe UI", 9, "bold")).grid(row=0, column=0, padx=4, sticky="w")
        self.director_ip_var = tk.StringVar(value="10.29.11.32")
        ttk.Entry(top_bar, textvariable=self.director_ip_var, width=14).grid(row=0, column=1, padx=4, sticky="w")

        ttk.Label(top_bar, text="User:", font=("Segoe UI", 9, "bold")).grid(row=0, column=2, padx=4, sticky="w")
        self.director_user_var = tk.StringVar(value="admin")
        ttk.Entry(top_bar, textvariable=self.director_user_var, width=10).grid(row=0, column=3, padx=4, sticky="w")

        ttk.Label(top_bar, text="Password:", font=("Segoe UI", 9, "bold")).grid(row=0, column=4, padx=4, sticky="w")
        self.director_pass_var = tk.StringVar(value="e#mi$Wp@U8%f")
        ttk.Entry(top_bar, textvariable=self.director_pass_var, width=15, show="*").grid(row=0, column=5, padx=4, sticky="w")

        ttk.Label(top_bar, text="Ping Count:", font=("Segoe UI", 9)).grid(row=0, column=6, padx=(12, 4), sticky="w")
        self.ping_count_var = tk.IntVar(value=500)
        ttk.Spinbox(top_bar, from_=10, to=1000, increment=50, textvariable=self.ping_count_var, width=6).grid(row=0, column=7, padx=4, sticky="w")

        # ── 2. Diagnostic Command Selection (Checkboxes) ──
        cmd_box = ttk.LabelFrame(self, text=" 🔍 Diagnostic Commands to Execute (Check All or Select Specific) ", padding=6)
        cmd_box.pack(side="top", fill="x", padx=6, pady=(2, 2))

        # Checkbox state variables
        self.chk_iface_var     = tk.BooleanVar(value=True)   # show interfaces brief
        self.chk_bgp_vr_var    = tk.BooleanVar(value=True)   # show bgp neighbor brief <VR>
        self.chk_bgp_all_var   = tk.BooleanVar(value=True)   # show bgp neighbor brief
        self.chk_alarm_bgp_var = tk.BooleanVar(value=True)   # show alarms last-n 30 | match bgp
        self.chk_alarm_peer_var= tk.BooleanVar(value=True)   # show alarms last-n 30 | match <peer_ip>
        self.chk_alarm_vni_var = tk.BooleanVar(value=True)   # show alarms last-n 30 | match vni
        self.chk_ping_var      = tk.BooleanVar(value=True)   # ping <peer> source <src> routing-instance <vr>
        self.chk_uptime_var    = tk.BooleanVar(value=True)   # show system uptime

        # Row 0: Quick preset buttons
        preset_row = tk.Frame(cmd_box, bg="#f8f9fa")
        preset_row.pack(fill="x", pady=(0, 4))

        tk.Label(preset_row, text="Quick Select:", font=("Segoe UI", 8, "bold"), bg="#f8f9fa", fg="#475569").pack(side="left", padx=(2, 6))

        tk.Button(
            preset_row,
            text="✅ Select All",
            font=("Segoe UI", 8, "bold"),
            bg="#e0f2fe",
            fg="#0369a1",
            relief="flat",
            cursor="hand2",
            padx=8,
            pady=1,
            command=self._select_all_commands
        ).pack(side="left", padx=2)

        tk.Button(
            preset_row,
            text="⚡ Ping Only",
            font=("Segoe UI", 8, "bold"),
            bg="#f1f5f9",
            fg="#334155",
            relief="flat",
            cursor="hand2",
            padx=8,
            pady=1,
            command=self._select_ping_only
        ).pack(side="left", padx=2)

        tk.Button(
            preset_row,
            text="📡 BGP & Alarms Only",
            font=("Segoe UI", 8, "bold"),
            bg="#f1f5f9",
            fg="#334155",
            relief="flat",
            cursor="hand2",
            padx=8,
            pady=1,
            command=self._select_bgp_alarms_only
        ).pack(side="left", padx=2)

        tk.Button(
            preset_row,
            text="❌ Deselect All",
            font=("Segoe UI", 8),
            bg="#f1f5f9",
            fg="#64748b",
            relief="flat",
            cursor="hand2",
            padx=8,
            pady=1,
            command=self._deselect_all_commands
        ).pack(side="left", padx=2)

        # Checkboxes Grid (2 rows x 4 columns)
        chk_grid = tk.Frame(cmd_box)
        chk_grid.pack(fill="x")

        # Row 1
        ttk.Checkbutton(chk_grid, text="1. Port Status (show interfaces brief)", variable=self.chk_iface_var).grid(row=0, column=0, sticky="w", padx=6, pady=2)
        ttk.Checkbutton(chk_grid, text="2. Specific VR BGP (show bgp neighbor brief <VR>)", variable=self.chk_bgp_vr_var).grid(row=0, column=1, sticky="w", padx=6, pady=2)
        ttk.Checkbutton(chk_grid, text="3. All BGP Neighbors (show bgp neighbor brief)", variable=self.chk_bgp_all_var).grid(row=0, column=2, sticky="w", padx=6, pady=2)
        ttk.Checkbutton(chk_grid, text="4. BGP Alarms (show alarms last-n 30 | match bgp)", variable=self.chk_alarm_bgp_var).grid(row=0, column=3, sticky="w", padx=6, pady=2)

        # Row 2
        ttk.Checkbutton(chk_grid, text="5. Peer IP Alarms (match <peer_ip>)", variable=self.chk_alarm_peer_var).grid(row=1, column=0, sticky="w", padx=6, pady=2)
        ttk.Checkbutton(chk_grid, text="6. VNI Alarms (match vni)", variable=self.chk_alarm_vni_var).grid(row=1, column=1, sticky="w", padx=6, pady=2)
        ttk.Checkbutton(chk_grid, text="7. Rapid Ping Test (peer_ip / source_ip)", variable=self.chk_ping_var).grid(row=1, column=2, sticky="w", padx=6, pady=2)
        ttk.Checkbutton(chk_grid, text="8. System Uptime (show system uptime)", variable=self.chk_uptime_var).grid(row=1, column=3, sticky="w", padx=6, pady=2)

        # ── 3. Action Button Bar (Packed Bottom-First to Guarantee Visibility) ──
        btn_bar = tk.Frame(self, bg="#f8f9fa", pady=4)
        btn_bar.pack(side="bottom", fill="x", padx=6, pady=(2, 4))

        self.start_btn = tk.Button(
            btn_bar,
            text="▶  Analyze & Run Selected Diagnostics",
            font=("Segoe UI", 10, "bold"),
            bg="#16a34a",
            fg="white",
            activebackground="#15803d",
            activeforeground="white",
            relief="flat",
            cursor="hand2",
            padx=16,
            pady=6,
            command=self.start_ticket_investigation
        )
        self.start_btn.pack(side="left", padx=(0, 8))

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
            command=self.stop_investigation
        )
        self.stop_btn.pack(side="left", padx=(0, 8))

        self.copy_update_btn = tk.Button(
            btn_bar,
            text="📋  Copy Customer Update Message",
            font=("Segoe UI", 10, "bold"),
            bg="#0284c7",
            fg="white",
            activebackground="#0369a1",
            activeforeground="white",
            relief="flat",
            cursor="hand2",
            padx=14,
            pady=6,
            command=self.copy_customer_message
        )
        self.copy_update_btn.pack(side="left", padx=(0, 8))

        self.load_sample_btn = tk.Button(
            btn_bar,
            text="📄  Load Sample Ticket",
            font=("Segoe UI", 9),
            bg="#64748b",
            fg="white",
            activebackground="#475569",
            activeforeground="white",
            relief="flat",
            cursor="hand2",
            padx=10,
            pady=6,
            command=self.load_sample_ticket
        )
        self.load_sample_btn.pack(side="left", padx=(0, 8))

        self.clear_btn = tk.Button(
            btn_bar,
            text="🧹  Clear All",
            font=("Segoe UI", 9),
            bg="#94a3b8",
            fg="white",
            activebackground="#64748b",
            activeforeground="white",
            relief="flat",
            cursor="hand2",
            padx=10,
            pady=6,
            command=self.clear_all
        )
        self.clear_btn.pack(side="left")

        # Anti-Sleep Keep-Awake Always-ON Status Indicator
        self.anti_sleep_btn = tk.Label(
            btn_bar,
            text="☕ Anti-Sleep: ALWAYS ON",
            font=("Segoe UI", 9, "bold"),
            bg="#065f46",
            fg="#a7f3d0",
            relief="groove",
            padx=12,
            pady=5
        )
        self.anti_sleep_btn.pack(side="right", padx=(4, 0))

        # Status & Progress Footer
        prog_bar_frame = tk.Frame(self, bg="#f8f9fa", pady=2)
        prog_bar_frame.pack(side="bottom", fill="x", padx=6, pady=(0, 2))

        self.progress_bar = ttk.Progressbar(prog_bar_frame, orient="horizontal", mode="indeterminate")
        self.progress_bar.pack(fill="x", pady=(0, 2))

        prog_info_box = tk.Frame(prog_bar_frame, bg="#f8f9fa")
        prog_info_box.pack(fill="x")

        self.status_var = tk.StringVar(value="Status: Ready | Paste ticket email text, select commands, and click 'Run Selected Diagnostics'")
        ttk.Label(prog_info_box, textvariable=self.status_var, font=("Segoe UI", 9)).pack(side="left")

        # ── 4. Main Workspace Split (PanedWindow) ──
        paned = ttk.PanedWindow(self, orient="horizontal")
        paned.pack(side="top", fill="both", expand=True, padx=6, pady=2)

        # ── LEFT PANE: Ticket Input & Parsed Incident Summary ──
        left_box = ttk.Frame(paned, padding=4)
        paned.add(left_box, weight=1)

        input_lbl_frame = tk.Frame(left_box)
        input_lbl_frame.pack(fill="x", pady=(0, 2))
        ttk.Label(input_lbl_frame, text="📩 Paste Ticket / NMS Alert Email Here:", font=("Segoe UI", 9, "bold")).pack(side="left")
        
        tk.Button(
            input_lbl_frame,
            text="📋 Paste from Clipboard",
            font=("Segoe UI", 8, "bold"),
            bg="#e0f2fe",
            fg="#0369a1",
            relief="flat",
            cursor="hand2",
            padx=6,
            pady=1,
            command=self.paste_from_clipboard
        ).pack(side="right")

        self.ticket_input_text = tk.Text(left_box, height=10, font=("Consolas", 9), wrap="word", relief="solid", bd=1)
        self.ticket_input_text.pack(fill="both", expand=True, pady=(0, 4))
        self.ticket_input_text.bind("<KeyRelease>", lambda e: self.on_text_changed())

        # Parsed Information Cards / Grid
        summary_frame = ttk.LabelFrame(left_box, text=" 🔍 Parsed Incident & Target Parameters ", padding=6)
        summary_frame.pack(fill="x", pady=(2, 0))

        self.lbl_tkt_no = self._create_param_row(summary_frame, "Ticket Number:", 0)
        self.lbl_hostname = self._create_param_row(summary_frame, "Target Hostname:", 1)
        self.lbl_circuit = self._create_param_row(summary_frame, "Access Circuit:", 2)
        self.lbl_vr = self._create_param_row(summary_frame, "Transport VR:", 3)
        self.lbl_peer = self._create_param_row(summary_frame, "BGP Peer IP:", 4)
        self.lbl_src = self._create_param_row(summary_frame, "Local Source IP:", 5)
        self.lbl_cid = self._create_param_row(summary_frame, "ISP Circuit ID:", 6)
        self.lbl_location = self._create_param_row(summary_frame, "Location / City:", 7)

        # ── RIGHT PANE: Tabs for Output Message, Diagnostics, and Raw Logs ──
        right_box = ttk.Frame(paned, padding=4)
        paned.add(right_box, weight=2)

        self.nb_output = ttk.Notebook(right_box)
        self.nb_output.pack(fill="both", expand=True)

        # Tab 1: Formatted Customer Update Message (Ready to Copy into Ticketing Tool)
        self.tab_msg = ttk.Frame(self.nb_output, padding=4)
        self.nb_output.add(self.tab_msg, text=" 📝 NOC Customer Update Message ")

        msg_top_bar = tk.Frame(self.tab_msg, bg="#f1f5f9", pady=3, padx=4)
        msg_top_bar.pack(fill="x", pady=(0, 4))
        tk.Label(msg_top_bar, text="Ready-to-paste customer update formatted with all executed logs:", font=("Segoe UI", 9, "bold"), bg="#f1f5f9", fg="#334155").pack(side="left")
        
        tk.Button(
            msg_top_bar,
            text="📋 Copy to Clipboard",
            font=("Segoe UI", 8, "bold"),
            bg="#0284c7",
            fg="white",
            relief="flat",
            cursor="hand2",
            padx=8,
            pady=2,
            command=self.copy_customer_message
        ).pack(side="right")

        self.msg_text = tk.Text(self.tab_msg, font=("Segoe UI", 10), wrap="word", relief="solid", bd=1)
        self.msg_text.pack(fill="both", expand=True)

        # Tab 2: Terminal Console Raw Logs
        self.tab_logs = ttk.Frame(self.nb_output, padding=4)
        self.nb_output.add(self.tab_logs, text=" 🖥️ Live Terminal Shell Logs ")

        self.log_text = tk.Text(self.tab_logs, bg="#0f172a", fg="#f8fafc", font=("Consolas", 9), wrap="none", relief="solid", bd=1)
        log_scroll_y = ttk.Scrollbar(self.tab_logs, orient="vertical", command=self.log_text.yview)
        log_scroll_x = ttk.Scrollbar(self.tab_logs, orient="horizontal", command=self.log_text.xview)
        self.log_text.configure(yscrollcommand=log_scroll_y.set, xscrollcommand=log_scroll_x.set)
        log_scroll_y.pack(side="right", fill="y")
        log_scroll_x.pack(side="bottom", fill="x")
        self.log_text.pack(side="left", fill="both", expand=True)

    def _create_param_row(self, parent, label_text, row_idx):
        ttk.Label(parent, text=label_text, font=("Segoe UI", 8, "bold")).grid(row=row_idx, column=0, sticky="w", padx=2, pady=1)
        lbl_val = tk.Label(parent, text="--", font=("Segoe UI", 8), fg="#0369a1", anchor="w")
        lbl_val.grid(row=row_idx, column=1, sticky="w", padx=4, pady=1)
        return lbl_val

    # ── Preset Checkbox Selectors ──
    def _select_all_commands(self):
        self.chk_iface_var.set(True)
        self.chk_bgp_vr_var.set(True)
        self.chk_bgp_all_var.set(True)
        self.chk_alarm_bgp_var.set(True)
        self.chk_alarm_peer_var.set(True)
        self.chk_alarm_vni_var.set(True)
        self.chk_ping_var.set(True)
        self.chk_uptime_var.set(True)

    def _select_ping_only(self):
        self.chk_iface_var.set(False)
        self.chk_bgp_vr_var.set(False)
        self.chk_bgp_all_var.set(False)
        self.chk_alarm_bgp_var.set(False)
        self.chk_alarm_peer_var.set(False)
        self.chk_alarm_vni_var.set(False)
        self.chk_ping_var.set(True)
        self.chk_uptime_var.set(False)

    def _select_bgp_alarms_only(self):
        self.chk_iface_var.set(False)
        self.chk_bgp_vr_var.set(True)
        self.chk_bgp_all_var.set(True)
        self.chk_alarm_bgp_var.set(True)
        self.chk_alarm_peer_var.set(True)
        self.chk_alarm_vni_var.set(True)
        self.chk_ping_var.set(False)
        self.chk_uptime_var.set(False)

    def _deselect_all_commands(self):
        self.chk_iface_var.set(False)
        self.chk_bgp_vr_var.set(False)
        self.chk_bgp_all_var.set(False)
        self.chk_alarm_bgp_var.set(False)
        self.chk_alarm_peer_var.set(False)
        self.chk_alarm_vni_var.set(False)
        self.chk_ping_var.set(False)
        self.chk_uptime_var.set(False)

    def on_text_changed(self):
        text = self.ticket_input_text.get("1.0", tk.END).strip()
        if not text:
            return
        self.parsed_data = parse_ticket_email(text)
        self._update_parsed_labels()

    def _update_parsed_labels(self):
        d = self.parsed_data
        self.lbl_tkt_no.config(text=d.get("ticket_number") or "--")
        self.lbl_hostname.config(text=d.get("hostname") or "--", font=("Segoe UI", 8, "bold"), fg="#16a34a" if d.get("hostname") else "#0369a1")
        self.lbl_circuit.config(text=d.get("circuit") or "--")
        self.lbl_vr.config(text=d.get("transport_vr") or "--")
        self.lbl_peer.config(text=d.get("peer_ip") or "--", font=("Segoe UI", 8, "bold"), fg="#dc2626" if d.get("peer_ip") else "#0369a1")
        self.lbl_src.config(text=d.get("local_source_ip") or "--")
        self.lbl_cid.config(text=d.get("circuit_id") or "--")
        self.lbl_location.config(text=d.get("location") or "--")

    def paste_from_clipboard(self):
        try:
            content = pyperclip.paste()
            if content:
                self.ticket_input_text.delete("1.0", tk.END)
                self.ticket_input_text.insert(tk.END, content)
                self.on_text_changed()
        except Exception as e:
            messagebox.showerror("Clipboard Error", f"Could not paste clipboard content:\n{e}")

    def load_sample_ticket(self):
        self.ticket_input_text.delete("1.0", tk.END)
        self.ticket_input_text.insert(tk.END, DEFAULT_SAMPLE_EMAIL)
        self.on_text_changed()

    def clear_all(self):
        self.ticket_input_text.delete("1.0", tk.END)
        self.msg_text.delete("1.0", tk.END)
        self.log_text.delete("1.0", tk.END)
        self.parsed_data = {}
        self.command_outputs = {}
        self._update_parsed_labels()
        self.status_var.set("Status: Cleared | Ready")

    def _toggle_anti_sleep(self):
        if sleep_preventer:
            new_state = sleep_preventer.toggle()
            status = "Activated (System will stay awake)" if new_state else "Deactivated (Normal power mode)"
            self.append_log(f"[☕] Anti-Sleep {status}.\n")
            if hasattr(self, 'anti_sleep_btn'):
                self.anti_sleep_btn.config(
                    text="☕ Anti-Sleep: ON" if new_state else "☕ Anti-Sleep: OFF",
                    bg="#0f766e" if new_state else "#64748b"
                )

    def copy_customer_message(self):
        content = self.msg_text.get("1.0", tk.END).strip()
        if not content:
            messagebox.showwarning("Empty Message", "No update message to copy. Please run diagnostics first.")
            return
        pyperclip.copy(content)
        messagebox.showinfo("Copied!", "Customer Update Message copied to clipboard!\nYou can now paste it directly into your ticketing software.")

    def append_log(self, text):
        def _apply():
            try:
                self.log_text.insert(tk.END, text)
                self.log_text.see(tk.END)
            except Exception:
                pass
        self.root.after(0, _apply)

    # ──────────────────────────────────────────────────────────────────────────
    # Execution Lifecycle & Automation Worker
    # ──────────────────────────────────────────────────────────────────────────
    def start_ticket_investigation(self):
        if self.is_running:
            return

        text = self.ticket_input_text.get("1.0", tk.END).strip()
        if not text:
            messagebox.showwarning("Missing Input", "Please paste the ticket / NMS alert email text first.")
            return

        self.parsed_data = parse_ticket_email(text)
        self._update_parsed_labels()

        hostname = self.parsed_data.get("hostname")
        if not hostname:
            messagebox.showerror("Hostname Not Found", "Could not extract Target Router Hostname from the ticket text.\nPlease verify the ticket contents.")
            return

        # Check if at least one diagnostic is selected
        any_selected = any([
            self.chk_iface_var.get(),
            self.chk_bgp_vr_var.get(),
            self.chk_bgp_all_var.get(),
            self.chk_alarm_bgp_var.get(),
            self.chk_alarm_peer_var.get(),
            self.chk_alarm_vni_var.get(),
            self.chk_ping_var.get(),
            self.chk_uptime_var.get()
        ])
        if not any_selected:
            messagebox.showwarning("No Commands Selected", "Please select at least one diagnostic command checkbox to run.")
            return

        self.is_running = True
        self.stop_event.clear()
        self.command_outputs = {}

        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.progress_bar.start(10)

        if sleep_preventer:
            sleep_preventer.start("Ticket Diagnostics")
            if hasattr(self, 'anti_sleep_btn'):
                self.anti_sleep_btn.config(text="☕ Anti-Sleep: ON", bg="#0f766e")

        self.status_var.set(f"Connecting to Versa Director & Auditing {hostname}...")
        self.log_text.delete("1.0", tk.END)
        self.msg_text.delete("1.0", tk.END)

        self.append_log(f"{'='*75}\n")
        self.append_log(f"[*] Starting Automated Incident Diagnostics for Ticket: {self.parsed_data.get('ticket_number') or 'N/A'}\n")
        self.append_log(f"[*] Target Router : {hostname}\n")
        self.append_log(f"[*] Circuit       : {self.parsed_data.get('circuit') or 'Auto-Detect'}\n")
        self.append_log(f"[*] Peer IP       : {self.parsed_data.get('peer_ip') or 'Auto-Detect'}\n")
        self.append_log(f"[*] Transport VR  : {self.parsed_data.get('transport_vr') or 'Auto-Detect'}\n")
        self.append_log(f"{'='*75}\n\n")

        # Run automation worker in background thread
        threading.Thread(target=self._run_diagnostics_worker, daemon=True).start()

    def stop_investigation(self):
        if not self.is_running:
            return
        self.stop_event.set()
        self.status_var.set("Stopping incident diagnostics gracefully...")
        self.append_log("\n[!] STOP requested by user. Halting execution...\n")

    def _run_diagnostics_worker(self):
        try:
            director_ip = self.director_ip_var.get().strip()
            director_user = self.director_user_var.get().strip()
            director_pass = self.director_pass_var.get().strip()

            controller = PamTerminalController()
            self.append_log("[*] Searching for active ARCON PAM PuTTY session...\n")
            if not controller.find_terminal_window():
                self.append_log("[!] ERROR: No active PuTTY window found. Please ensure your PAM PuTTY terminal is open.\n")
                self._finish_investigation(success=False, error_msg="No PuTTY window found")
                return

            self.append_log(f"[✓] Attached to PuTTY window: '{controller.window_title}'\n")

            # 1. Login to target router via Versa Director
            target_hostname = self.parsed_data.get("hostname")
            login_ok = self._login_to_target_router(controller, target_hostname, director_ip, director_user, director_pass)
            if not login_ok:
                self._finish_investigation(success=False, error_msg=f"Could not establish CLI on {target_hostname}")
                return

            if self.stop_event.is_set():
                self._finish_investigation(success=False)
                return

            # Discover peer and VR if missing before running checks
            peer_ip = self.parsed_data.get("peer_ip")
            transport_vr = self.parsed_data.get("transport_vr")
            source_ip = self.parsed_data.get("local_source_ip")
            ping_cnt = self.ping_count_var.get()

            if not peer_ip or not transport_vr:
                self.append_log("[*] Inquiring 'show bgp neighbor brief' to discover active peer and VR...\n")
                controller.send_command("show bgp neighbor brief", wait_after=1.5)
                bgp_disc_raw = self._wait_prompt(controller, cmd="show bgp neighbor brief", max_timeout=20.0)
                disc_peer, disc_vr = self._discover_peer_from_bgp(bgp_disc_raw)
                if not peer_ip and disc_peer:
                    peer_ip = disc_peer
                    self.parsed_data["peer_ip"] = peer_ip
                    source_ip = calculate_peer_source_ip(peer_ip)
                    self.parsed_data["local_source_ip"] = source_ip
                if not transport_vr and disc_vr:
                    transport_vr = disc_vr
                    self.parsed_data["transport_vr"] = transport_vr
                self.root.after(0, self._update_parsed_labels)

            # ── Run Selected Commands in Sequence ──

            # Command 1: show interfaces brief
            if self.chk_iface_var.get() and not self.stop_event.is_set():
                self.append_log(f"\n[*] [1/8] Executing: show interfaces brief\n")
                controller.send_command("show interfaces brief", wait_after=1.5)
                out = self._wait_prompt(controller, cmd="show interfaces brief", max_timeout=25.0)
                self.command_outputs["interfaces_brief"] = self._clean_cli_output(out, "show interfaces brief")
                self.append_log(f"[✓] Captured Interfaces Brief.\n")

            # Command 2: show bgp neighbor brief <VR>
            if self.chk_bgp_vr_var.get() and transport_vr and not self.stop_event.is_set():
                cmd = f"show bgp neighbor brief {transport_vr}"
                self.append_log(f"\n[*] [2/8] Executing: {cmd}\n")
                controller.send_command(cmd, wait_after=1.5)
                out = self._wait_prompt(controller, cmd=cmd, max_timeout=20.0)
                self.command_outputs["bgp_vr"] = self._clean_cli_output(out, cmd)
                self.append_log(f"[✓] Captured BGP Neighbor for {transport_vr}.\n")

            # Command 3: show bgp neighbor brief (All)
            if self.chk_bgp_all_var.get() and not self.stop_event.is_set():
                self.append_log(f"\n[*] [3/8] Executing: show bgp neighbor brief\n")
                controller.send_command("show bgp neighbor brief", wait_after=1.5)
                out = self._wait_prompt(controller, cmd="show bgp neighbor brief", max_timeout=20.0)
                self.command_outputs["bgp_all"] = self._clean_cli_output(out, "show bgp neighbor brief")
                self.append_log(f"[✓] Captured All BGP Neighbors.\n")

            # Command 4: show alarms last-n 30 | match bgp
            if self.chk_alarm_bgp_var.get() and not self.stop_event.is_set():
                cmd = "show alarms last-n 30 | match bgp"
                self.append_log(f"\n[*] [4/8] Executing: {cmd}\n")
                controller.send_command(cmd, wait_after=1.5)
                out = self._wait_prompt(controller, cmd=cmd, max_timeout=20.0)
                self.command_outputs["alarms_bgp"] = self._clean_cli_output(out, cmd)
                self.append_log(f"[✓] Captured BGP Alarms.\n")

            # Command 5: show alarms last-n 30 | match <peer_ip>
            if self.chk_alarm_peer_var.get() and peer_ip and not self.stop_event.is_set():
                cmd = f"show alarms last-n 30 | match {peer_ip}"
                self.append_log(f"\n[*] [5/8] Executing: {cmd}\n")
                controller.send_command(cmd, wait_after=1.5)
                out = self._wait_prompt(controller, cmd=cmd, max_timeout=20.0)
                self.command_outputs["alarms_peer"] = self._clean_cli_output(out, cmd)
                self.append_log(f"[✓] Captured Alarms for Peer {peer_ip}.\n")

            # Command 6: show alarms last-n 30 | match vni
            if self.chk_alarm_vni_var.get() and not self.stop_event.is_set():
                cmd = "show alarms last-n 30 | match vni"
                self.append_log(f"\n[*] [6/8] Executing: {cmd}\n")
                controller.send_command(cmd, wait_after=1.5)
                out = self._wait_prompt(controller, cmd=cmd, max_timeout=20.0)
                self.command_outputs["alarms_vni"] = self._clean_cli_output(out, cmd)
                self.append_log(f"[✓] Captured VNI Interface Alarms.\n")

            # Command 7: Rapid Ping Diagnostics
            ping_stats = {"tx": ping_cnt, "rx": 0, "loss": 100.0, "avg_rtt": 0.0}
            if self.chk_ping_var.get() and peer_ip and not self.stop_event.is_set():
                if transport_vr and source_ip:
                    cmd_ping = f"ping {peer_ip} source {source_ip} routing-instance {transport_vr} rapid enable count {ping_cnt}"
                elif transport_vr:
                    cmd_ping = f"ping {peer_ip} routing-instance {transport_vr} rapid enable count {ping_cnt}"
                else:
                    cmd_ping = f"ping {peer_ip} rapid enable count {ping_cnt}"

                self.append_log(f"\n[*] [7/8] Executing: {cmd_ping}\n")
                controller.send_command(cmd_ping, wait_after=1.5)
                ping_raw = self._wait_prompt(controller, cmd="ping", max_timeout=40.0)
                self.command_outputs["ping"] = self._extract_ping_block(ping_raw)
                ping_stats = self._parse_ping(ping_raw)
                self.append_log(f"[✓] Ping Captured: Loss={ping_stats['loss']}%, Avg RTT={ping_stats['avg_rtt']}ms\n")

            # Command 8: show system uptime
            if self.chk_uptime_var.get() and not self.stop_event.is_set():
                cmd = "show system uptime"
                self.append_log(f"\n[*] [8/8] Executing: {cmd}\n")
                controller.send_command(cmd, wait_after=1.5)
                out = self._wait_prompt(controller, cmd=cmd, max_timeout=15.0)
                self.command_outputs["uptime"] = self._clean_cli_output(out, cmd)
                self.append_log(f"[✓] Captured System Uptime.\n")

            # Exit router session back to Director
            self.append_log("\n[*] Exiting router CLI back to Director...\n")
            controller.send_command("exit", wait_after=1.5)
            controller.send_command("exit", wait_after=2.0)

            # Generate Executive Customer Update Message
            customer_msg = self._generate_customer_message(
                ticket_info=self.parsed_data,
                outputs=self.command_outputs,
                ping_stats=ping_stats
            )

            # Present message in UI
            def _show_msg():
                self.msg_text.delete("1.0", tk.END)
                self.msg_text.insert(tk.END, customer_msg)
                self.nb_output.select(self.tab_msg)

            self.root.after(0, _show_msg)
            self._finish_investigation(success=True)

        except Exception as e:
            self.append_log(f"\n[!] Unexpected Error during Investigation: {e}\n")
            self._finish_investigation(success=False, error_msg=str(e))

    def _login_to_target_router(self, controller, target_hostname, director_ip, director_user, director_pass):
        """
        Navigates Director CLI and reaches the branch router via SSH.
        """
        self.append_log(f"[*] Navigating PuTTY session to reach {target_hostname}...\n")
        controller.activate_window()
        time.sleep(0.3)
        controller.send_enter(wait_after=1.0)
        out = controller.copy_terminal_output() or ""

        # If inside another router CLI, exit
        if "-cli>" in out:
            controller.send_command("exit", wait_after=2.0)
            out = controller.copy_terminal_output() or ""

        if "[admin@" in out and ": ~]" in out:
            controller.send_command("exit", wait_after=2.0)
            out = controller.copy_terminal_output() or ""

        # If at Bastion prompt, SSH to Director
        if "10.29.11.32" not in out and "VD-" not in out:
            self.append_log(f"[*] SSH into Versa Director ({director_ip})...\n")
            controller.send_command(f"ssh -l {director_user} {director_ip}", wait_after=3.0)
            controller.send_command(director_pass, wait_after=5.0, is_password=True)
            out = controller.copy_terminal_output() or ""

        # Enter Director CLI if in shell
        if "admin@VD-" in out or ":~$" in out:
            controller.send_command("cli", wait_after=4.0)
            controller.send_command("set paginate false", wait_after=1.5)
            out = controller.copy_terminal_output() or ""
        elif "Administrator@VD-" in out:
            controller.send_command("set paginate false", wait_after=1.0)

        # Resolve Branch Router IP from Director device list
        self.append_log(f"[*] Inquiring Director for IP of '{target_hostname}'...\n")
        controller.send_command(f"show devices list | match {target_hostname}", wait_after=3.0)
        res_out = self._wait_prompt(controller, cmd="show devices list", max_timeout=15.0)

        target_ip = None
        m = re.search(rf"{target_hostname}\s+(\b10\.\d+\.\d+\.\d+\b)", res_out, re.IGNORECASE)
        if m:
            target_ip = m.group(1)
        else:
            m2 = re.search(r"(\b10\.\d{1,3}\.\d{1,3}\.\d{1,3}\b)", res_out)
            if m2:
                target_ip = m2.group(1)

        if not target_ip:
            self.append_log(f"[!] Could not discover IP for {target_hostname} on Versa Director.\n")
            return False

        self.append_log(f"[✓] Discovered Management IP: {target_ip}\n")
        self.append_log(f"[*] SSH into {target_hostname} ({target_ip})...\n")
        controller.send_command(f"ssh admin@{target_ip}", wait_after=4.0)

        chk = controller.copy_terminal_output() or ""
        if "(yes/no" in chk[-300:].lower() or "fingerprint" in chk[-300:].lower():
            controller.send_command("yes", wait_after=3.0)

        controller.send_command(director_pass, wait_after=6.0, is_password=True)

        self.append_log(f"[*] Entering CLI on {target_hostname}...\n")
        controller.send_command("cli", wait_after=4.5)
        controller.send_command("set paginate false", wait_after=1.5)

        cli_out = controller.copy_terminal_output() or ""
        is_ok = f"{target_hostname}-cli>" in cli_out or "-cli>" in cli_out
        if is_ok:
            self.append_log(f"[✓] Successfully logged into {target_hostname} CLI!\n")
        else:
            self.append_log(f"[!] Warning: Did not detect clean CLI prompt for {target_hostname}.\n")
        return is_ok

    def _wait_prompt(self, controller, cmd=None, max_timeout=35.0, check_interval=0.4):
        start_t = time.time()
        time.sleep(0.4)
        while time.time() - start_t < max_timeout:
            if self.stop_event.is_set():
                break
            time.sleep(check_interval)
            out = controller.copy_terminal_output() or ""
            if cmd and cmd in out:
                lines = [l.strip() for l in out.strip().splitlines() if l.strip()]
                if lines and any(l.endswith("-cli>") or "[ok]" in l for l in lines[-2:]):
                    return out
        return controller.copy_terminal_output() or ""

    def _clean_cli_output(self, raw_output, cmd_str):
        """Extracts clean section from raw terminal output between command and prompt."""
        if not raw_output:
            return "No output captured."
        idx = raw_output.rfind(cmd_str)
        snippet = raw_output[idx:] if idx != -1 else raw_output
        lines = snippet.splitlines()
        clean = []
        for line in lines:
            if "[error]" in line:
                continue
            clean.append(line)
            if line.strip().endswith("-cli>") and len(clean) > 3:
                break
        return "\n".join(clean[:50]).strip()

    def _extract_ping_block(self, raw_ping_output):
        """Extracts the command and summary lines from ping output."""
        if not raw_ping_output:
            return "No ping output captured."
        idx = raw_ping_output.rfind("ping ")
        snippet = raw_ping_output[idx:] if idx != -1 else raw_ping_output
        lines = snippet.splitlines()
        clean = []
        for line in lines:
            clean.append(line)
            if "rtt min/avg/max" in line or line.strip().endswith("-cli>"):
                if len(clean) > 3:
                    break
        return "\n".join(clean[:25]).strip()

    def _discover_peer_from_bgp(self, bgp_output):
        """Fallback to discover first WAN peer IP and Transport VR from show bgp neighbor brief."""
        if not bgp_output:
            return None, None
        vr_m = re.search(r"routing-instance:\s*([A-Za-z0-9_-]+Transport-VR)", bgp_output, re.IGNORECASE)
        vr = vr_m.group(1).strip() if vr_m else None
        
        peer = None
        lines = bgp_output.splitlines()
        for l in lines:
            parts = l.strip().split()
            if parts and re.match(r"^\d+\.\d+\.\d+\.\d+$", parts[0]):
                cand = parts[0]
                if not cand.startswith("169.254.") and not cand.startswith("127."):
                    peer = cand
                    break
        return peer, vr

    def _parse_ping(self, ping_output):
        stats = {"tx": 0, "rx": 0, "loss": 100.0, "avg_rtt": 0.0}
        if not ping_output:
            return stats
        all_loss = re.findall(r"(\d+)\s+packets transmitted,\s*(\d+)\s+received,\s*(\d+(?:\.\d+)?)%\s+packet loss", ping_output)
        if all_loss:
            m = all_loss[-1]
            stats["tx"] = int(m[0])
            stats["rx"] = int(m[1])
            stats["loss"] = float(m[2])
        all_rtt = re.findall(r"rtt min/avg/max/mdev\s*=\s*[\d\.]+/([\d\.]+)/", ping_output)
        if all_rtt:
            stats["avg_rtt"] = float(all_rtt[-1])
        return stats

    def _generate_customer_message(self, ticket_info, outputs, ping_stats):
        """
        Creates professional customer communication text containing all executed diagnostic blocks.
        """
        tkt = ticket_info.get("ticket_number") or "N/A"
        host = ticket_info.get("hostname") or "Target Router"
        circuit = ticket_info.get("circuit") or "Access Circuit"
        cid = ticket_info.get("circuit_id") or "N/A"
        peer = ticket_info.get("peer_ip") or "N/A"
        loc = ticket_info.get("location") or "N/A"
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        loss = ping_stats.get("loss", 0.0 if "ping" in outputs else None)
        avg_rtt = ping_stats.get("avg_rtt", 0.0)

        # Port status analysis
        iface_text = outputs.get("interfaces_brief", "")
        port_down = ("down" in iface_text.lower() or "admin down" in iface_text.lower()) if iface_text else False

        msg = []
        msg.append(f"Dear Customer,\n")
        msg.append(f"With reference to Ticket #{tkt} regarding Site '{host}' ({loc}) [{circuit}]:\n")
        msg.append(f"Our NOC Network Operations team has completed in-depth diagnostics on the Versa SD-WAN appliance.")

        # Status opening
        if port_down and (loss == 100.0 or loss is None):
            msg.append(
                f"We observed that the access port BGP session for circuit '{circuit}' is currently DOWN with 100% packet loss."
            )
        elif loss == 100.0:
            msg.append(
                f"We observed that the access port BGP session for circuit '{circuit}' is currently DOWN with 100% packet loss."
            )
        elif loss is not None and loss > 0.0:
            msg.append(
                f"We observed that the BGP session for circuit '{circuit}' is experiencing intermittent degradation with {loss:.1f}% packet loss (Average Latency: {avg_rtt:.2f} ms)."
            )
        elif loss == 0.0:
            msg.append(
                f"We observed that the access port BGP session for circuit '{circuit}' is currently UP and ESTABLISHED with 0% packet loss (Average Latency: {avg_rtt:.2f} ms)."
            )
        else:
            msg.append(
                f"We observed that the access port BGP session for circuit '{circuit}' is currently DOWN with 100% packet loss."
            )

        msg.append(f"\n--- Incident & Circuit Parameters ---")
        msg.append(f"• Ticket Number    : {tkt}")
        msg.append(f"• Router Hostname  : {host}")
        msg.append(f"• ISP Circuit ID   : {cid}")
        msg.append(f"• Access Circuit   : {circuit}")
        msg.append(f"• Peer Destination : {peer}")
        msg.append(f"• Diagnostic Time  : {now_str}")

        # Section 1: Port Status
        if "interfaces_brief" in outputs:
            msg.append(f"\n--- [1] Port & Interface Status (show interfaces brief) ---")
            msg.append(outputs["interfaces_brief"])

        # Section 2: Specific Transport VR BGP
        if "bgp_vr" in outputs:
            msg.append(f"\n--- [2] Specific Transport VR BGP Status ---")
            msg.append(outputs["bgp_vr"])

        # Section 3: All BGP Neighbors
        if "bgp_all" in outputs:
            msg.append(f"\n--- [3] All BGP Neighbors Status ---")
            msg.append(outputs["bgp_all"])

        # Section 4: BGP Alarms
        if "alarms_bgp" in outputs:
            msg.append(f"\n--- [4] BGP State Change Alarms (show alarms last-n 30 | match bgp) ---")
            msg.append(outputs["alarms_bgp"])

        # Section 5: Peer IP Alarms
        if "alarms_peer" in outputs:
            msg.append(f"\n--- [5] Peer IP Transition Alarms (match {peer}) ---")
            msg.append(outputs["alarms_peer"])

        # Section 6: VNI Interface Alarms
        if "alarms_vni" in outputs:
            msg.append(f"\n--- [6] VNI Interface Alarms (match vni) ---")
            msg.append(outputs["alarms_vni"])

        # Section 7: Rapid Ping Diagnostics
        if "ping" in outputs:
            msg.append(f"\n--- [7] Rapid Ping Diagnostics Output ---")
            msg.append(outputs["ping"])
            msg.append(f"Summary Verdict: Packet Loss: {loss:.1f}%, Avg RTT: {avg_rtt:.2f} ms.")

        # Section 8: System & Service Uptime
        if "uptime" in outputs:
            msg.append(f"\n--- [8] Router System & Service Uptime ---")
            msg.append(outputs["uptime"])

        msg.append(f"\nOur engineering team is actively monitoring the circuit. For any queries, please reply directly to this ticket.\n")
        msg.append(f"Warm Regards,\nTata Communications - NOC Network Operations")

        return "\n".join(msg)

    def _finish_investigation(self, success=True, error_msg=None):
        self.is_running = False
        self.root.after(0, self.progress_bar.stop)
        self.root.after(0, lambda: self.start_btn.config(state="normal"))
        self.root.after(0, lambda: self.stop_btn.config(state="disabled"))

        if success:
            self.root.after(0, lambda: self.status_var.set("Status: Investigation Complete! Customer update message generated."))
            self.append_log(f"\n{'='*75}\n")
            self.append_log(f"[✓] Incident Diagnostics Completed Successfully at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            self.append_log(f"[✓] Ready-to-copy customer update message has been populated in 'NOC Customer Update Message' tab.\n")
            self.append_log(f"{'='*75}\n\n")
        else:
            err = f": {error_msg}" if error_msg else ""
            self.root.after(0, lambda: self.status_var.set(f"Status: Investigation Halted{err}"))
            self.append_log(f"\n[!] Investigation halted{err}.\n")
