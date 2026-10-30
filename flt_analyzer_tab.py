"""
====================================================================================================
                        TATA COMMUNICATIONS - NETWORK OPERATIONS CENTER
====================================================================================================
Module: flt_analyzer_tab.py
Purpose:
  Fault / FLT Incident Analysis & RFO Diagnostic Engine
  1. Parses ticket details (Incident Window, Alarm Start Time, Hostname, Circuit).
  2. Resolves Org & Control/Transport naming rules:
     - If Hostname contains "IFT-MB"  -> MB (MB-NET)
     - If Hostname contains "IFT-RBI" -> RBI (RBI-NET)
     - If Hostname contains "P2P"     -> P2P
     - Else                           -> MB (MB-NET)
  3. Executes sequential FLT verification commands via PAM PuTTY terminal:
     - Port Identification: show interfaces brief | match <circuit> | except 172
     - VR BGP Status:       show bgp neighbor brief <VR> (Captures Neighbor & Uptime)
     - Port Alarms:         show alarms all | match <date> | match <interface>
     - BGP Alarms:          show alarms all | match <date> | match <peer_ip>
     - SD-WAN SLA Down:     show orgs org <ORG>-NET sd-wan sla-monitor status-down | tab | match <circuit>
     - SD-WAN Detail:       show orgs org <ORG>-NET sd-wan detail <hub_name> | match <circuit>
  4. Generates an Executive FLT RFO & Incident Resolution Summary ready for 1-click clipboard copy.
====================================================================================================
"""

import tkinter as tk
from tkinter import ttk, messagebox
import threading
import time
import re
from datetime import datetime
import pyperclip

try:
    from pam_terminal_controller import PamTerminalController
    HAS_PAM = True
except ImportError:
    HAS_PAM = False

try:
    from sleep_preventer import sleep_preventer
except ImportError:
    sleep_preventer = None


def resolve_org_from_hostname(hostname):
    """
    Org resolution rule:
    - If Hostname contains "IFT-MB"  -> MB
    - If Hostname contains "IFT-RBI" -> RBI
    - If Hostname contains "P2P"     -> P2P
    - Else                           -> MB
    """
    hn_up = (hostname or "").upper()
    if "IFT-MB" in hn_up:
        return "MB"
    elif "IFT-RBI" in hn_up:
        return "RBI"
    elif "P2P" in hn_up:
        return "P2P"
    return "MB"


class FltAnalyzerFrame(ttk.Frame):
    def __init__(self, parent, root=None, parent_gui=None, *args, **kwargs):
        super().__init__(parent, *args, **kwargs)
        self.root = root or parent
        self.parent_gui = parent_gui

        self.is_running = False
        self.stop_event = threading.Event()
        self.command_outputs = {}

        # Parsed / user parameters
        self.var_tkt = tk.StringVar(value="")
        self.var_hostname = tk.StringVar(value="")
        self.var_circuit = tk.StringVar(value="")
        self.var_alarm_start = tk.StringVar(value="")
        self.var_alarm_end = tk.StringVar(value="Ongoing")
        self.var_org = tk.StringVar(value="MB")
        self.var_hub_name = tk.StringVar(value="")
        self.var_hub_ip = tk.StringVar(value="")
        self.var_port_vni = tk.StringVar(value="vni-0/0.0")
        self.var_peer_ip = tk.StringVar(value="")
        self.var_vr = tk.StringVar(value="")

        # Auth
        self.director_ip_var = tk.StringVar(value="10.29.11.32")
        self.director_user_var = tk.StringVar(value="admin")
        self.director_pass_var = tk.StringVar(value="e#mi$Wp@U8%f")

        self._setup_ui()

    def _setup_ui(self):
        # Top Auth Bar
        auth_frame = ttk.LabelFrame(self, text=" ⚙️ Versa Director & Credentials Target ", padding=6)
        auth_frame.pack(side="top", fill="x", padx=6, pady=(4, 2))

        ttk.Label(auth_frame, text="Director IP:").grid(row=0, column=0, padx=4, sticky="w")
        ttk.Entry(auth_frame, textvariable=self.director_ip_var, width=14).grid(row=0, column=1, padx=4, sticky="w")

        ttk.Label(auth_frame, text="User:").grid(row=0, column=2, padx=4, sticky="w")
        ttk.Entry(auth_frame, textvariable=self.director_user_var, width=12).grid(row=0, column=3, padx=4, sticky="w")

        ttk.Label(auth_frame, text="Password:").grid(row=0, column=4, padx=4, sticky="w")
        ttk.Entry(auth_frame, textvariable=self.director_pass_var, show="*", width=14).grid(row=0, column=5, padx=4, sticky="w")

        # Main Paned Window: Left (Inputs & Parameters) | Right (Results & Live Log)
        paned = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        paned.pack(side="top", fill="both", expand=True, padx=6, pady=4)

        # ── Left Frame: Ticket Raw Paste + Form Parameters ──
        left_box = ttk.Frame(paned, padding=4)
        paned.add(left_box, weight=4)

        # Raw Ticket Paste Box
        paste_lf = ttk.LabelFrame(left_box, text=" 📋 Paste Incident / Ticket Text Here ", padding=4)
        paste_lf.pack(side="top", fill="both", expand=True)

        paste_btn_bar = tk.Frame(paste_lf, bg="#f8f9fa")
        paste_btn_bar.pack(fill="x", pady=(0, 2))

        tk.Button(
            paste_btn_bar,
            text="📋 Paste from Clipboard",
            font=("Segoe UI", 8, "bold"),
            bg="#e0f2fe",
            fg="#0369a1",
            relief="flat",
            cursor="hand2",
            padx=8,
            pady=2,
            command=self.paste_from_clipboard
        ).pack(side="left")

        tk.Button(
            paste_btn_bar,
            text="✨ Load Sample FLT Incident",
            font=("Segoe UI", 8),
            bg="#f1f5f9",
            fg="#475569",
            relief="flat",
            cursor="hand2",
            padx=8,
            pady=2,
            command=self.load_sample
        ).pack(side="left", padx=4)

        tk.Button(
            paste_btn_bar,
            text="🧹 Clear",
            font=("Segoe UI", 8),
            bg="#f1f5f9",
            fg="#dc2626",
            relief="flat",
            cursor="hand2",
            padx=8,
            pady=2,
            command=self.clear_all
        ).pack(side="right")

        self.txt_input = tk.Text(paste_lf, font=("Consolas", 8), wrap="word", height=7, padx=4, pady=4)
        self.txt_input.pack(fill="both", expand=True)
        self.txt_input.bind("<KeyRelease>", lambda e: self.parse_input_text())

        # Parameters Frame (Grid of auto-parsed & editable fields)
        param_lf = ttk.LabelFrame(left_box, text=" 🔍 Parsed FLT Parameters (Editable) ", padding=6)
        param_lf.pack(side="top", fill="x", pady=(4, 0))

        # Row 0: Ticket # & Hostname
        ttk.Label(param_lf, text="Ticket Number:").grid(row=0, column=0, sticky="w", padx=3, pady=2)
        ttk.Entry(param_lf, textvariable=self.var_tkt, width=18).grid(row=0, column=1, sticky="w", padx=3, pady=2)

        ttk.Label(param_lf, text="Hostname:").grid(row=0, column=2, sticky="w", padx=3, pady=2)
        self.entry_hn = ttk.Entry(param_lf, textvariable=self.var_hostname, width=24)
        self.entry_hn.grid(row=0, column=3, sticky="w", padx=3, pady=2)
        self.var_hostname.trace_add("write", lambda *a: self._on_hostname_changed())

        # Row 1: Circuit & Org
        ttk.Label(param_lf, text="Circuit Name:").grid(row=1, column=0, sticky="w", padx=3, pady=2)
        self.entry_circ = ttk.Entry(param_lf, textvariable=self.var_circuit, width=18)
        self.entry_circ.grid(row=1, column=1, sticky="w", padx=3, pady=2)
        self.var_circuit.trace_add("write", lambda *a: self._on_circuit_changed())

        ttk.Label(param_lf, text="Resolved Org:").grid(row=1, column=2, sticky="w", padx=3, pady=2)
        ttk.Entry(param_lf, textvariable=self.var_org, width=12).grid(row=1, column=3, sticky="w", padx=3, pady=2)

        # Row 2: Alarm Start Time & End Time
        ttk.Label(param_lf, text="Alarm Start Time:").grid(row=2, column=0, sticky="w", padx=3, pady=2)
        ttk.Entry(param_lf, textvariable=self.var_alarm_start, width=18).grid(row=2, column=1, sticky="w", padx=3, pady=2)

        ttk.Label(param_lf, text="Alarm End / Status:").grid(row=2, column=2, sticky="w", padx=3, pady=2)
        ttk.Entry(param_lf, textvariable=self.var_alarm_end, width=16).grid(row=2, column=3, sticky="w", padx=3, pady=2)

        # Row 3: Target Interface VNI & Peer IP
        ttk.Label(param_lf, text="Port VNI:").grid(row=3, column=0, sticky="w", padx=3, pady=2)
        ttk.Entry(param_lf, textvariable=self.var_port_vni, width=14).grid(row=3, column=1, sticky="w", padx=3, pady=2)

        ttk.Label(param_lf, text="Peer IP:").grid(row=3, column=2, sticky="w", padx=3, pady=2)
        ttk.Entry(param_lf, textvariable=self.var_peer_ip, width=18).grid(row=3, column=3, sticky="w", padx=3, pady=2)

        # Row 4: Hub Location Name & Hub End IP
        ttk.Label(param_lf, text="Hub Router Name:").grid(row=4, column=0, sticky="w", padx=3, pady=2)
        ttk.Entry(param_lf, textvariable=self.var_hub_name, width=24).grid(row=4, column=1, columnspan=2, sticky="w", padx=3, pady=2)

        ttk.Label(param_lf, text="Hub End IP:").grid(row=4, column=2, sticky="e", padx=3, pady=2)
        ttk.Entry(param_lf, textvariable=self.var_hub_ip, width=16).grid(row=4, column=3, sticky="w", padx=3, pady=2)

        # ── Right Frame: Output Tabs (Customer Message & Live Terminal Logs) ──
        right_box = ttk.Frame(paned)
        paned.add(right_box, weight=6)

        self.nb_output = ttk.Notebook(right_box)
        self.nb_output.pack(fill="both", expand=True)

        # Tab A: Formatted FLT RFO / Incident Summary
        tab_msg = ttk.Frame(self.nb_output)
        self.nb_output.add(tab_msg, text=" 📑 Executive FLT RFO & Summary ")

        msg_top = tk.Frame(tab_msg, bg="#f8f9fa", pady=2)
        msg_top.pack(fill="x")
        tk.Label(msg_top, text="Ready-to-paste Executive Customer / ISP FLT Incident Communication:", font=("Segoe UI", 9, "bold"), fg="#1e293b", bg="#f8f9fa").pack(side="left", padx=4)

        tk.Button(
            msg_top,
            text="📋 Copy FLT Summary",
            font=("Segoe UI", 9, "bold"),
            bg="#0284c7",
            fg="white",
            relief="flat",
            cursor="hand2",
            padx=10,
            pady=2,
            command=self.copy_summary_message
        ).pack(side="right", padx=4)

        self.txt_summary = tk.Text(tab_msg, font=("Segoe UI", 9), wrap="word", bg="#ffffff", fg="#0f172a", padx=10, pady=8)
        self.txt_summary.pack(fill="both", expand=True)

        # Tab B: Live Terminal Logs
        tab_log = ttk.Frame(self.nb_output)
        self.nb_output.add(tab_log, text=" 🖥️ Live Terminal Shell Logs ")

        self.txt_log = tk.Text(tab_log, font=("Consolas", 9), bg="#1e1e1e", fg="#d4d4d4", wrap="char", padx=8, pady=6)
        self.txt_log.pack(fill="both", expand=True)

        # ── Bottom Action Bar ──
        bot_bar = tk.Frame(self, bg="#f8f9fa", pady=4)
        bot_bar.pack(side="bottom", fill="x", padx=6, pady=(2, 4))

        self.btn_run = tk.Button(
            bot_bar,
            text="▶  Analyze FLT Incident & Run Diagnostics",
            font=("Segoe UI", 10, "bold"),
            bg="#16a34a",
            fg="white",
            activebackground="#15803d",
            activeforeground="white",
            relief="flat",
            cursor="hand2",
            padx=16,
            pady=6,
            command=self.start_flt_investigation
        )
        self.btn_run.pack(side="left", padx=(0, 8))

        self.btn_stop = tk.Button(
            bot_bar,
            text="⏹ Stop",
            font=("Segoe UI", 9, "bold"),
            bg="#dc2626",
            fg="white",
            activebackground="#b91c1c",
            activeforeground="white",
            relief="flat",
            cursor="hand2",
            state="disabled",
            padx=12,
            pady=6,
            command=self.stop_flt_investigation
        )
        self.btn_stop.pack(side="left", padx=(0, 8))

        self.progress_bar = ttk.Progressbar(bot_bar, mode="indeterminate", length=160)
        self.progress_bar.pack(side="left", padx=8)

        self.status_var = tk.StringVar(value="Status: Ready | Paste FLT ticket text and click 'Analyze FLT Incident & Run Diagnostics'")
        lbl_status = tk.Label(bot_bar, textvariable=self.status_var, font=("Segoe UI", 9), fg="#475569", bg="#f8f9fa")
        lbl_status.pack(side="left", padx=8)

    def _on_hostname_changed(self):
        hn = self.var_hostname.get().strip()
        org = resolve_org_from_hostname(hn)
        self.var_org.set(org)

    def _on_circuit_changed(self):
        circ = self.var_circuit.get().strip()
        if circ and not self.var_vr.get():
            self.var_vr.set(f"{circ}-Transport-VR")

    def parse_input_text(self):
        raw = self.txt_input.get("1.0", tk.END).strip()
        if not raw:
            return

        # 1. Strict Ticket Number
        m_tkt = re.search(r"\b(IN\d{10,16})\b", raw)
        if m_tkt:
            self.var_tkt.set(m_tkt.group(1).strip())

        # 2. Hostname and Circuit from Pipe
        m_pipe = re.search(r"([A-Za-z0-9_-]+)\s*\|\|\s*([A-Za-z0-9_-]+)", raw)
        if m_pipe:
            self.var_hostname.set(m_pipe.group(1).lstrip("-").strip())
            self.var_circuit.set(m_pipe.group(2).strip())
        else:
            # Check SD-WAN Datapath Down format
            m_sdwan = re.search(r"SD-WAN\s+Datapath\s+Down\s+from\s+[^\s/]+(?:/[^\s]+)?\s+to\s+([A-Za-z0-9_-]+)/([A-Za-z0-9_-]+)", raw, re.IGNORECASE)
            if m_sdwan:
                self.var_hostname.set(m_sdwan.group(1).strip())
                self.var_circuit.set(m_sdwan.group(2).strip())

        # 3. Alarm start time
        m_start = re.search(r"(?:Alarm\s*Start\s*Date|Problem\s*started\s*at)\s*[\t:]*\s*([0-9-.:]+\s*(?:on\s*[0-9.]+)?|[0-9]{4}-[0-9]{2}-[0-9]{2}\s+[0-9:]+)", raw, re.IGNORECASE)
        if m_start:
            self.var_alarm_start.set(m_start.group(1).strip())

        # 4. Management IP / Peer IP
        m_ip = re.search(r"(?:Management\s*IP|peer\s*\(?)\s*[:-]*\s*([0-9.]+)", raw, re.IGNORECASE)
        if m_ip:
            self.var_peer_ip.set(m_ip.group(1).strip())

        # 5. Extract Hub Router Name and Hub End IP if present in ticket text
        m_hub = re.search(r"SD-WAN\s+Datapath\s+Down\s+from\s+([A-Za-z0-9_-]+)", raw, re.IGNORECASE)
        if m_hub:
            from_device = m_hub.group(1).strip()
            # If the from_device is a Hub router (starts with IFT- or not a VC-/CGW-)
            if not from_device.startswith("VC-") and not from_device.startswith("CGW-"):
                self.var_hub_name.set(from_device)

        m_hub_explicit = re.search(r"(?:Hub\s*Router|Hub\s*Location|Hub\s*End\s*Device)\s*[:-]\s*([A-Za-z0-9_-]+)", raw, re.IGNORECASE)
        if m_hub_explicit:
            self.var_hub_name.set(m_hub_explicit.group(1).strip())

        m_hub_ip = re.search(r"(?:Hub\s*End\s*IP|Hub\s*IP)\s*[:-]\s*([0-9.]+)", raw, re.IGNORECASE)
        if m_hub_ip:
            self.var_hub_ip.set(m_hub_ip.group(1).strip())

        # Auto-resolve Org
        self._on_hostname_changed()
        self._on_circuit_changed()

    def paste_from_clipboard(self):
        try:
            content = pyperclip.paste()
            if content:
                self.txt_input.delete("1.0", tk.END)
                self.txt_input.insert(tk.END, content)
                self.parse_input_text()
        except Exception as e:
            messagebox.showerror("Clipboard Error", str(e))

    def load_sample(self):
        sample = """TICKET NUMBER\tIN202610300188
LOCATION\tHyderabad
CONFIGURATION ITEM\tIFT-MB-3059-IFTHYD-R1||TCL-MPLS1-MB
TITLE\tProblem: SD-WAN Datapath Down from VC-MB-GRP1-02-PDC to IFT-MB-3059-IFTHYD-R1/TCL-MPLS1-MB
ALARM START DATE\t2026-10-30 11:15:00
Management IP :- 10.116.195.21/30
Link Type :- TCL-MPLS1-MB"""
        self.txt_input.delete("1.0", tk.END)
        self.txt_input.insert(tk.END, sample)
        self.parse_input_text()

    def clear_all(self):
        self.txt_input.delete("1.0", tk.END)
        self.txt_log.delete("1.0", tk.END)
        self.txt_summary.delete("1.0", tk.END)
        self.var_tkt.set("")
        self.var_hostname.set("")
        self.var_circuit.set("")
        self.var_alarm_start.set("")
        self.var_peer_ip.set("")
        self.var_hub_name.set("")
        self.var_hub_ip.set("")

    def append_log(self, text):
        def _insert():
            self.txt_log.insert(tk.END, text)
            self.txt_log.see(tk.END)
        self.root.after(0, _insert)

    def start_flt_investigation(self):
        hn = self.var_hostname.get().strip()
        circ = self.var_circuit.get().strip()
        if not hn:
            messagebox.showerror("Missing Target Hostname", "Please specify the Target Router Hostname to audit.")
            return
        if not circ:
            messagebox.showerror("Missing Circuit", "Please specify the Circuit name (e.g. TCL-MPLS1-MB).")
            return

        if not HAS_PAM:
            messagebox.showerror("Dependency Error", "win32gui / win32api is required for terminal automation.")
            return

        self.is_running = True
        self.stop_event.clear()
        self.command_outputs = {}

        self.btn_run.config(state="disabled")
        self.btn_stop.config(state="normal")
        self.progress_bar.start(10)

        if sleep_preventer:
            sleep_preventer.start("FLT Diagnostics")

        self.status_var.set(f"Connecting to Versa Director & Auditing FLT on {hn}...")
        self.txt_log.delete("1.0", tk.END)
        self.txt_summary.delete("1.0", tk.END)

        self.append_log(f"{'='*75}\n")
        self.append_log(f"[*] Starting Automated FLT Incident Diagnostics\n")
        self.append_log(f"[*] Target Router : {hn}\n")
        self.append_log(f"[*] Circuit       : {circ}\n")
        self.append_log(f"[*] Org Resolved  : {self.var_org.get()}\n")
        self.append_log(f"{'='*75}\n\n")

        threading.Thread(target=self._run_flt_worker, daemon=True).start()

    def stop_flt_investigation(self):
        self.stop_event.set()
        self.status_var.set("Stopping FLT audit...")
        self.append_log("\n[!] User clicked STOP. Halting operations...\n")

    def _run_flt_worker(self):
        controller = PamTerminalController(worker_id=1, log_callback=self.append_log)
        try:
            target_hostname = self.var_hostname.get().strip()
            circuit = self.var_circuit.get().strip()
            org = self.var_org.get().strip() or "MB"
            vr = self.var_vr.get().strip() or f"{circuit}-Transport-VR"
            peer_ip = self.var_peer_ip.get().strip()
            hub_name = self.var_hub_name.get().strip()
            today_str = datetime.now().strftime("%Y-%m-%d")

            # Alarm date string: check if input alarm start date has YYYY-MM-DD, otherwise use today's date
            alarm_date_str = today_str
            t_start_val = self.var_alarm_start.get().strip()
            m_date = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", t_start_val)
            if m_date:
                alarm_date_str = m_date.group(1).strip()
            self.append_log(f"[*] Audit Alarm Filter Date: {alarm_date_str}\n")
            director_user = self.director_user_var.get().strip() or "admin"
            director_pass = self.director_pass_var.get().strip() or "e#mi$Wp@U8%f"

            # 1. Login to Branch Router
            login_ok = self._login_to_target_router(controller, target_hostname, director_ip, director_user, director_pass)
            if not login_ok:
                self._finish_flt(success=False, error_msg=f"Could not establish CLI on {target_hostname}")
                return

            # Command 1: SD-WAN SLA Monitor Status Down ("Make it First Run")
            # show orgs org <ORG>-NET sd-wan sla-monitor status-down | tab | match <circuit>
            cmd_sla = f"show orgs org {org}-NET sd-wan sla-monitor status-down | tab | match {circuit}"
            self.append_log(f"\n[*] [1/6] SD-WAN SLA Monitor Down (First Run): {cmd_sla}\n")
            out_sla, err = self._execute_cmd_with_retry(controller, cmd_sla, max_timeout=25.0, max_tries=2)
            if err:
                self.append_log(f"[!] {err}\n")
                self._safe_disconnect_to_bastion(controller)
                self._finish_flt(success=False, error_msg=err)
                return
            self.command_outputs["sla_down"] = out_sla or ""
            self.append_log(f"[✓] Captured SLA Monitor Status-Down.\n")

            # Extract down hub hostnames from SLA down output (excluding CGW- and VC-)
            down_hubs = []
            for l in (out_sla or "").splitlines():
                if "CGW-" in l or "VC-" in l or "show orgs" in l or "cli>" in l or "[ok]" in l:
                    continue
                parts = l.strip().split()
                if len(parts) >= 2:
                    r_hn = parts[1]
                    if r_hn not in down_hubs and not r_hn.startswith("-"):
                        down_hubs.append(r_hn)
            self.command_outputs["down_hubs"] = down_hubs

            # Command 2: Port Identification
            # show interfaces brief | match <circuit> | except 172
            cmd1 = f"show interfaces brief | match {circuit} | except 172"
            self.append_log(f"\n[*] [2/6] Port Identification: {cmd1}\n")
            out1, err = self._execute_cmd_with_retry(controller, cmd1, max_timeout=20.0, max_tries=2)
            if err:
                self.append_log(f"[!] {err}\n")
                self._safe_disconnect_to_bastion(controller)
                self._finish_flt(success=False, error_msg=err)
                return
            self.command_outputs["port_ident"] = out1 or ""
            self.append_log(f"[✓] Captured Port Identification.\n")

            # Auto-extract Transport VR and VNI from Port output
            m_vr = re.search(r"\b([A-Za-z0-9_-]+-Transport-VR)\b", self.command_outputs["port_ident"])
            if m_vr:
                vr = m_vr.group(1).strip()
                self.var_vr.set(vr)
                self.append_log(f"[✓] Auto-detected Transport VR from Port: {vr}\n")

            m_vni = re.search(r"\b(vni-[0-9/.]+)\b", self.command_outputs["port_ident"])
            if m_vni:
                vni_found = m_vni.group(1).strip()
                self.var_port_vni.set(vni_found)
                self.append_log(f"[✓] Auto-detected Port Interface: {vni_found}\n")

            # Command 3: VR BGP Status (Neighbor and Uptime)
            # show bgp neighbor brief <VR>
            cmd2 = f"show bgp neighbor brief {vr}"
            self.append_log(f"\n[*] [3/6] Specific VR BGP Status: {cmd2}\n")
            out2, err = self._execute_cmd_with_retry(controller, cmd2, max_timeout=20.0, max_tries=2)
            if err:
                self.append_log(f"[!] {err}\n")
                self._safe_disconnect_to_bastion(controller)
                self._finish_flt(success=False, error_msg=err)
                return
            self.command_outputs["bgp_vr"] = out2 or ""
            self.append_log(f"[✓] Captured VR BGP Status.\n")

            # Extract Peer IP from BGP output if not entered or to guarantee accuracy
            for l in self.command_outputs["bgp_vr"].splitlines():
                parts = l.strip().split()
                if parts and re.match(r"^\d+\.\d+\.\d+\.\d+$", parts[0]):
                    peer_ip = parts[0].strip()
                    self.var_peer_ip.set(peer_ip)
                    self.append_log(f"[✓] Auto-detected Neighbor Peer IP from BGP: {peer_ip}\n")
                    break

            # Command 4: Port Alarms
            # show alarms all | match <date> | match <port_vni>
            port_vni = self.var_port_vni.get().strip() or "vni"
            cmd3 = f"show alarms all | match {alarm_date_str} | match {port_vni}"
            self.append_log(f"\n[*] [4/6] Port Alarms: {cmd3}\n")
            out3, err = self._execute_cmd_with_retry(controller, cmd3, max_timeout=20.0, max_tries=2)
            if err:
                self.append_log(f"[!] {err}\n")
                self._safe_disconnect_to_bastion(controller)
                self._finish_flt(success=False, error_msg=err)
                return
            self.command_outputs["port_alarms"] = out3 or ""
            self.append_log(f"[✓] Captured Port Alarms.\n")

            # Command 5: BGP Alarms
            # show alarms all | match <date> | match <peer_ip>
            if peer_ip:
                cmd4 = f"show alarms all | match {alarm_date_str} | match {peer_ip}"
            else:
                cmd4 = f"show alarms all | match {alarm_date_str} | match bgp"
            self.append_log(f"\n[*] [5/6] BGP Alarms: {cmd4}\n")
            out4, err = self._execute_cmd_with_retry(controller, cmd4, max_timeout=20.0, max_tries=2)
            if err:
                self.append_log(f"[!] {err}\n")
                self._safe_disconnect_to_bastion(controller)
                self._finish_flt(success=False, error_msg=err)
                return
            self.command_outputs["bgp_alarms"] = out4 or ""
            self.append_log(f"[✓] Captured BGP Alarms.\n")

            # Command 6: SD-WAN Detail Hub
            # show orgs org <ORG>-NET sd-wan detail <hub_name> | match <circuit>
            # If hub_name not provided by user, auto-select the first down HUB from SLA monitor output
            target_hub = hub_name
            if not target_hub and "down_hubs" in self.command_outputs and self.command_outputs["down_hubs"]:
                target_hub = self.command_outputs["down_hubs"][0]
                self.var_hub_name.set(target_hub)
                self.append_log(f"[✓] Auto-selected down Hub from SLA monitor: {target_hub}\n")

            if target_hub:
                cmd6 = f"show orgs org {org}-NET sd-wan detail {target_hub} | match {circuit}"
                self.append_log(f"\n[*] [6/6] SD-WAN Detail for Hub {target_hub}: {cmd6}\n")
                out6, err = self._execute_cmd_with_retry(controller, cmd6, max_timeout=25.0, max_tries=2)
                if err:
                    self.append_log(f"[!] {err}\n")
                    self._safe_disconnect_to_bastion(controller)
                    self._finish_flt(success=False, error_msg=err)
                    return
                self.command_outputs["sdwan_detail"] = out6 or ""
                self.append_log(f"[✓] Captured SD-WAN Detail.\n")

                # Try to auto-extract Hub End IP from SD-WAN Detail output if not set
                if not self.var_hub_ip.get().strip():
                    for line in (out6 or "").splitlines():
                        if "ipv4" in line:
                            m_ip_detail = re.search(r"\b(10\.\d+\.\d+\.\d+)\b", line)
                            if m_ip_detail:
                                detected_hub_ip = m_ip_detail.group(1).strip()
                                self.var_hub_ip.set(detected_hub_ip)
                                self.append_log(f"[✓] Auto-detected Hub End IP: {detected_hub_ip}\n")
                                break

            # Disconnect cleanly all the way back to Bastion mcpeuser (4-5 exits)
            self._safe_disconnect_to_bastion(controller)

            # Generate Executive FLT Communication Summary
            flt_summary = self._generate_flt_summary()

            def _show():
                self.txt_summary.delete("1.0", tk.END)
                self.txt_summary.insert(tk.END, flt_summary)
                self.nb_output.select(0)

            self.root.after(0, _show)
            self._finish_flt(success=True)

        except Exception as e:
            self.append_log(f"\n[!] Unexpected Error during FLT Audit: {e}\n")
            self._finish_flt(success=False, error_msg=str(e))

    def _execute_cmd_with_retry(self, controller, cmd, max_timeout=20.0, max_tries=2):
        """
        Executes a command on the terminal controller with retry (up to max_tries, e.g. 2).
        If an error prompt or failure is observed after 2 tries, returns None and error message.
        """
        for attempt in range(1, max_tries + 1):
            if self.stop_event.is_set():
                return None, "Aborted by user"
            controller.send_command(cmd, wait_after=1.5)
            out = self._wait_prompt(controller, cmd=cmd, max_timeout=max_timeout)
            cleaned = self._clean_cli_output(out, cmd)
            # Check for critical execution errors in command output
            has_error = False
            for line in cleaned.splitlines():
                ll = line.lower()
                if "syntax error:" in ll or "[error]" in ll or "command not found" in ll or "unknown command" in ll:
                    has_error = True
                    break
            if not has_error and cleaned != "No output captured.":
                return cleaned, None
            if attempt < max_tries:
                self.append_log(f"[!] Warning: Issue executing '{cmd}', retrying ({attempt+1}/{max_tries})...\n")
                time.sleep(1.0)
            else:
                return cleaned, f"Error observed after command: {cmd}. Check manually."
        return None, f"Error observed after command: {cmd}. Check manually."

    def _safe_disconnect_to_bastion(self, controller):
        """Cleanly issues exit commands (up to 4-5 times) back to bastion jump host."""
        self.append_log("\n[*] Disconnecting nested router sessions back to Bastion mcpeuser...\n")
        for _ in range(5):
            out = controller.copy_terminal_output() or ""
            last_lines = "\n".join([l.strip() for l in out.strip().splitlines()[-4:] if l.strip()])
            if "mcpeuser" in last_lines or "PU1PAMMCPSGW" in last_lines or "[mcpeuser" in last_lines:
                self.append_log("[✓] Confirmed back at Bastion prompt (mcpeuser).\n")
                break
            controller.send_command("exit", wait_after=1.2)
        self.append_log("[✓] Session disconnection sequence completed.\n")

    def _detect_session_state(self, controller):
        """
        Inspects the very latest active prompt line in the PuTTY terminal to determine exact session state:
        - 'router_cli'     : inside branch router CLI prompt (e.g. admin@BRANCH-cli>)
        - 'router_shell'   : inside branch router bash shell (e.g. [admin@BRANCH: ~]$)
        - 'director_cli'   : inside Versa Director CLI (e.g. Administrator@VD-...>)
        - 'director_shell' : inside Versa Director Linux bash (e.g. [admin@VD-...: ~]$)
        - 'bastion'        : at Bastion jump host prompt (e.g. [mcpeuser1@PU1PAMMCPSGW01 ~]$)
        - 'unknown'        : unknown or intermediate prompt
        """
        out = controller.copy_terminal_output() or ""
        lines = [l.strip() for l in out.strip().splitlines() if l.strip()]
        if not lines:
            return "unknown", ""
        last_line = lines[-1]

        if "-cli>" in last_line and "@VD-" not in last_line:
            return "router_cli", last_line
        if "[admin@" in last_line and ": ~]" in last_line and "VD-" not in last_line:
            return "router_shell", last_line
        if ("@VD-" in last_line or "Administrator@VD" in last_line) and last_line.endswith(">"):
            return "director_cli", last_line
        if "VD-" in last_line and (last_line.endswith("$") or last_line.endswith("#")):
            return "director_shell", last_line
        if any(bp in last_line for bp in ["mcpeuser", "PU1PAMMCPSGW", "MCPESGW01"]) or (last_line.endswith("$") and "VD-" not in last_line and "admin@" not in last_line):
            return "bastion", last_line

        return "unknown", last_line

    def _login_to_target_router(self, controller, target_hostname, director_ip, director_user, director_pass):
        self.append_log(f"[*] Navigating PuTTY session to reach {target_hostname}...\n")
        controller.activate_window()
        time.sleep(0.3)
        controller.send_enter(wait_after=1.0)

        state, last_line = self._detect_session_state(controller)
        self.append_log(f"[*] Detected terminal prompt state: '{state}' ({last_line})\n")

        # Step 1: If inside another router CLI or shell, exit out
        if state == "router_cli":
            self.append_log("[*] Inside active router CLI. Exiting to router shell...\n")
            controller.send_command("exit", wait_after=2.0)
            state, last_line = self._detect_session_state(controller)

        if state == "router_shell":
            self.append_log("[*] Inside router Linux shell. Exiting to Director...\n")
            controller.send_command("exit", wait_after=2.0)
            state, last_line = self._detect_session_state(controller)

        # Step 2: If at Bastion prompt or disconnected, SSH into Versa Director
        if state == "bastion" or ("10.29.11.32" not in last_line and state not in ["director_cli", "director_shell"]):
            self.append_log(f"[*] At Bastion prompt. Logging into Versa Director ({director_ip})...\n")
            controller.send_command(f"ssh -l {director_user} {director_ip}", wait_after=3.0)
            controller.send_command(director_pass, wait_after=5.0, is_password=True)
            controller.send_enter(wait_after=1.0)
            state, last_line = self._detect_session_state(controller)

        # Step 3: If in Director Linux shell, launch Director CLI
        if state == "director_shell" or "admin@VD-" in last_line or (":~$" in last_line and "VD-" in last_line):
            self.append_log("[*] At Director shell. Entering Director CLI...\n")
            controller.send_command("cli", wait_after=4.0)
            controller.send_command("set paginate false", wait_after=1.5)
            state, last_line = self._detect_session_state(controller)
        elif state == "director_cli" or "Administrator@VD-" in last_line:
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
        time.sleep(0.5)
        while time.time() - start_t < max_timeout:
            if self.stop_event.is_set():
                break
            time.sleep(check_interval)
            out = controller.copy_terminal_output() or ""
            if cmd and cmd in out:
                idx = out.rfind(cmd)
                after_cmd = out[idx + len(cmd):]
                after_lines = [l.strip() for l in after_cmd.strip().splitlines() if l.strip()]
                if after_lines and any(l.endswith("-cli>") or "[ok]" in l or "[error]" in l for l in after_lines):
                    return out
        return controller.copy_terminal_output() or ""

    def _clean_cli_output(self, raw_output, cmd_str):
        if not raw_output:
            return "No output captured."
        idx = raw_output.rfind(cmd_str)
        if idx == -1:
            core = cmd_str.split("|")[0].strip() if "|" in cmd_str else cmd_str.split()[0]
            idx = raw_output.rfind(core)
            if idx == -1:
                return "No output captured."
        snippet = raw_output[idx:]
        lines = snippet.splitlines()
        clean = []
        for i, line in enumerate(lines):
            if i > 0 and "-cli>" in line:
                prompt_prefix = line.split("-cli>")[0] + "-cli>"
                clean.append(prompt_prefix)
                break
            clean.append(line)
        return "\n".join(clean[:50]).strip()

    def _detect_wan_status(self, port_out, port_alarms):
        """
        Dynamically determine WAN interface status from CLI outputs.
        Manager rule:
        - If alarm output found:
          - If crosses 2 time transitions/alarm records (> 2 alarms) -> "Up/down/flapping frequently"
          - If 1 or 2 transitions -> "Up/down/flapping frequently" or "Down and up now" (if currently up)
        - If currently up and alarms present -> "Up/down/flapping frequently"
        - If currently down and alarms present -> "Down"
        - If currently up and no alarms -> "Up"
        - If currently down and no alarms -> "Down"
        """
        # Parse interface line: NAME  MAC  OPER  ADMIN  TENANT  VRF  IP
        is_port_up = False
        is_port_down = False
        for line in port_out.splitlines():
            if line.strip() and "show" not in line and "cli>" not in line and "[ok]" not in line:
                parts = line.split()
                if len(parts) >= 4:
                    oper = parts[2].lower()
                    admin = parts[3].lower()
                    if oper == "up" and admin == "up":
                        is_port_up = True
                    elif oper == "down" or admin == "down":
                        is_port_down = True

        # Check if there are any real alarm lines (exclude command echoes and prompt lines)
        alarm_data_lines = [
            l for l in port_alarms.splitlines()
            if l.strip()
            and "show alarms" not in l
            and "cli>" not in l
            and "[ok]" not in l
        ]
        alarm_count = len(alarm_data_lines)

        if alarm_count > 2 and is_port_up:
            return "Up/down/flapping frequently"
        elif alarm_count > 0 and is_port_up:
            return "Up/down/flapping frequently"
        elif alarm_count > 0 and is_port_down:
            return "Down"
        elif is_port_up:
            return "Up"
        elif is_port_down:
            return "Down"
        else:
            return "Up"

    def _detect_bgp_status(self, bgp_out, bgp_alarms):
        """
        Dynamically determine BGP session status from CLI outputs.
        Manager rule:
        - If currently up and alarm records > 2 -> "Up/down/flapping frequently"
        - If currently up and alarm records in 1..2 -> "Down and up now"
        - If currently down -> "Down"
        - If currently up and no alarms -> "Up"
        """
        is_bgp_up = False
        is_bgp_down = False
        for line in bgp_out.splitlines():
            parts = line.strip().split()
            if parts and re.match(r"^\d+\.\d+\.\d+\.\d+$", parts[0]) and len(parts) >= 6:
                state_col = parts[5]
                if state_col.isdigit():
                    is_bgp_up = True   # Numeric PfxRcd = Established/Up
                elif state_col.lower() in ["connect", "idle", "active", "opensent", "openconfirm"]:
                    is_bgp_down = True

        # Check BGP alarm lines
        bgp_alarm_data = [
            l for l in bgp_alarms.splitlines()
            if l.strip()
            and "show alarms" not in l
            and "cli>" not in l
            and "[ok]" not in l
        ]
        alarm_count = len(bgp_alarm_data)

        if is_bgp_up:
            if alarm_count > 2:
                return "Up/down/flapping frequently"
            elif alarm_count >= 1:
                return "Down and up now"
            else:
                return "Up"
        elif is_bgp_down:
            return "Down"
        else:
            if alarm_count > 2:
                return "Up/down/flapping frequently"
            elif alarm_count >= 1:
                return "Down and up now"
            return "Down"

    def _generate_flt_summary(self):
        hn = self.var_hostname.get().strip() or "Target Router"
        circ = self.var_circuit.get().strip() or "Circuit"
        t_start = self.var_alarm_start.get().strip() or "Alarm Start Time"
        t_end = self.var_alarm_end.get().strip() or "Ongoing"
        hub_name = self.var_hub_name.get().strip()
        hub_ip = self.var_hub_ip.get().strip()

        # If hub_name still empty, check if we found down_hubs
        if not hub_name and "down_hubs" in self.command_outputs and self.command_outputs["down_hubs"]:
            hub_name = self.command_outputs["down_hubs"][0]

        hub_name_str = hub_name or "Location-A"
        hub_ip_str = hub_ip or "0.0.0.0"

        # ── Real-time dynamic status analysis from captured CLI logs ──
        port_out = self.command_outputs.get("port_ident", "")
        bgp_out  = self.command_outputs.get("bgp_vr", "")
        port_alarms = self.command_outputs.get("port_alarms", "")
        bgp_alarms  = self.command_outputs.get("bgp_alarms", "")

        wan_status = self._detect_wan_status(port_out, port_alarms)
        bgp_status = self._detect_bgp_status(bgp_out, bgp_alarms)

        # Filter out CGW- and VC- from SLA monitor output and check for down HUB hostnames
        sla_raw = self.command_outputs.get("sla_down", "")
        sla_filtered_lines = []
        down_hub_hostnames = []
        for line in sla_raw.splitlines():
            # Don't consider HN with CGW-, VC-
            if "CGW-" in line or "VC-" in line:
                continue
            if line.strip() and "show orgs" not in line and "cli>" not in line and "[ok]" not in line:
                sla_filtered_lines.append(line)
                parts = line.strip().split()
                # Second column is usually the remote hostname in tab format
                if len(parts) >= 2:
                    remote_hn = parts[1]
                    if remote_hn not in down_hub_hostnames and not remote_hn.startswith("-"):
                        down_hub_hostnames.append(remote_hn)
            elif "show orgs" in line or "cli>" in line or "[ok]" in line:
                sla_filtered_lines.append(line)

        sla_clean = "\n".join(sla_filtered_lines).strip()

        # Check if SLA output is empty (no down paths excluding CGW/VC)
        has_real_sla_output = any(
            l.strip() and "show orgs" not in l and "cli>" not in l and "[ok]" not in l
            for l in sla_filtered_lines
        )

        if not has_real_sla_output:
            # If no Output -> "Link is at present - Proceed with Resolved FLT Process and Conclude Manually"
            resolved_summary = []
            resolved_summary.append("Dear Team,\n")
            resolved_summary.append("Link is at present - Proceed with Resolved FLT Process and Conclude Manually.\n")
            resolved_summary.append(f"WAN interface status observed {wan_status}. The local-loop BGP session between the CPE and PE is {bgp_status}.\n")
            resolved_summary.append("ISP recommended to restore circuit with stability, identify the root cause, and provide RFO.\n")
            resolved_summary.append(f"{'='*75}\n")
            resolved_summary.append(f"TECHNICAL VERIFICATION & AUDIT LOGS ({hn} | {circ}):\n")
            resolved_summary.append(f"{'='*75}\n")
            resolved_summary.append(f"\n[1] Port Identification:\n{port_out}\n")
            resolved_summary.append(f"\n[2] Specific VR BGP Status:\n{bgp_out}\n")
            resolved_summary.append(f"\n[3] Port Alarms:\n{self.command_outputs.get('port_alarms', 'No alarms captured.')}\n")
            resolved_summary.append(f"\n[4] BGP Alarms:\n{self.command_outputs.get('bgp_alarms', 'No alarms captured.')}\n")
            resolved_summary.append(f"\n[5] SD-WAN SLA Monitor Status-Down (Filtered - excluding Controller/Gateway):\n{sla_clean or 'No active SLA monitor down paths detected.'}\n")
            if "sdwan_detail" in self.command_outputs:
                resolved_summary.append(f"\n[6] SD-WAN Detail ({hub_name_str}):\n{self.command_outputs.get('sdwan_detail', '')}\n")
            resolved_summary.append(f"\nWarm Regards,\nTata Communications - NOC Network Operations")
            return "\n".join(resolved_summary)

        # Determine Hub disruption phrase:
        # and All other Hub Locations = >2 HN in output of "show orgs org MB-NET sd-wan sla-monitor status-down | tab | match <circuit>"
        if len(down_hub_hostnames) > 2:
            hub_phrase = f"HUB locations ({hub_name_str}, HUB End IP - {hub_ip_str}), and All other Hub Locations."
        elif len(down_hub_hostnames) == 2:
            second_hub = [h for h in down_hub_hostnames if h != hub_name_str]
            second_str = second_hub[0] if second_hub else down_hub_hostnames[1]
            hub_phrase = f"HUB locations ({hub_name_str}, HUB End IP - {hub_ip_str}), ({second_str})."
        elif len(down_hub_hostnames) == 1:
            hub_phrase = f"HUB locations ({hub_name_str}, HUB End IP - {hub_ip_str})."
        else:
            hub_phrase = f"HUB locations ({hub_name_str}, HUB End IP - {hub_ip_str}), and All other Hub Locations."

        summary = []
        summary.append("Dear Team,\n")
        if t_start and t_start != "Alarm Start Time":
            summary.append(f"During the incident window from {t_start} to {t_end}, the site experienced an SDWAN connectivity disruption with {hub_phrase}\n")
        else:
            summary.append(f"During the incident window, the site experienced an SDWAN connectivity disruption with {hub_phrase}\n")
        summary.append(f"WAN interface status observed {wan_status}. The local-loop BGP session between the CPE and PE is {bgp_status}.\n")
        summary.append("ISP recommended to restore circuit with stability, identify the root cause, and provide RFO.\n")
        summary.append(f"{'='*75}\n")
        summary.append(f"TECHNICAL VERIFICATION & AUDIT LOGS ({hn} | {circ}):\n")
        summary.append(f"{'='*75}\n")

        summary.append(f"\n[1] Port Identification:\n{port_out}\n")
        summary.append(f"\n[2] Specific VR BGP Status:\n{bgp_out}\n")
        summary.append(f"\n[3] Port Alarms:\n{self.command_outputs.get('port_alarms', 'No alarms captured.')}\n")
        summary.append(f"\n[4] BGP Alarms:\n{self.command_outputs.get('bgp_alarms', 'No alarms captured.')}\n")
        summary.append(f"\n[5] SD-WAN SLA Monitor Status-Down (Filtered - excluding Controller/Gateway):\n{sla_clean}\n")
        if "sdwan_detail" in self.command_outputs:
            summary.append(f"\n[6] SD-WAN Detail ({hub_name_str}):\n{self.command_outputs.get('sdwan_detail', '')}\n")

        summary.append(f"\nWarm Regards,\nTata Communications - NOC Network Operations")
        return "\n".join(summary)

    def copy_summary_message(self):
        text = self.txt_summary.get("1.0", tk.END).strip()
        if text:
            try:
                pyperclip.copy(text)
                messagebox.showinfo("Copied", "Executive FLT Summary & Technical Logs copied to clipboard!")
            except Exception as e:
                messagebox.showerror("Error", str(e))
        else:
            messagebox.showwarning("Empty", "No FLT Summary has been generated yet.")

    def _finish_flt(self, success=True, error_msg=None):
        self.is_running = False
        self.root.after(0, self.progress_bar.stop)
        self.root.after(0, lambda: self.btn_run.config(state="normal"))
        self.root.after(0, lambda: self.btn_stop.config(state="disabled"))

        if success:
            self.root.after(0, lambda: self.status_var.set("Status: FLT Investigation Complete! Summary generated."))
            self.append_log(f"\n{'='*75}\n")
            self.append_log(f"[✓] FLT Incident Diagnostics Completed Successfully at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            self.append_log(f"[✓] Ready-to-paste FLT RFO Communication populated in summary tab.\n")
            self.append_log(f"{'='*75}\n\n")
        else:
            err = f": {error_msg}" if error_msg else ""
            self.root.after(0, lambda: self.status_var.set(f"Status: FLT Investigation halted{err}"))
            self.append_log(f"\n[!] FLT Investigation stopped{err}\n")
