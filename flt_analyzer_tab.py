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
        self.var_hub_name = tk.StringVar(value="IFT-MB-3059-IFTHYD-R1")
        self.var_hub_ip = tk.StringVar(value="10.116.192.90")
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

            director_ip = self.director_ip_var.get().strip() or "10.29.11.32"
            director_user = self.director_user_var.get().strip() or "admin"
            director_pass = self.director_pass_var.get().strip() or "e#mi$Wp@U8%f"

            # 1. Login to Branch Router
            login_ok = self._login_to_target_router(controller, target_hostname, director_ip, director_user, director_pass)
            if not login_ok:
                self._finish_flt(success=False, error_msg=f"Could not establish CLI on {target_hostname}")
                return

            # Command 1: Port Identification
            # show interfaces brief | match <circuit> | except 172
            cmd1 = f"show interfaces brief | match {circuit} | except 172"
            self.append_log(f"\n[*] [1/6] Port Identification: {cmd1}\n")
            controller.send_command(cmd1, wait_after=1.5)
            out1 = self._wait_prompt(controller, cmd=cmd1, max_timeout=20.0)
            self.command_outputs["port_ident"] = self._clean_cli_output(out1, cmd1)
            self.append_log(f"[✓] Captured Port Identification.\n")

            # Auto-extract Transport VR and VNI from Command 1 output
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

            # Command 2: VR BGP Status (Neighbor and Uptime)
            # show bgp neighbor brief <VR>
            cmd2 = f"show bgp neighbor brief {vr}"
            self.append_log(f"\n[*] [2/6] Specific VR BGP Status: {cmd2}\n")
            controller.send_command(cmd2, wait_after=1.5)
            out2 = self._wait_prompt(controller, cmd=cmd2, max_timeout=20.0)
            self.command_outputs["bgp_vr"] = self._clean_cli_output(out2, cmd2)
            self.append_log(f"[✓] Captured VR BGP Status.\n")

            # Extract Peer IP from Command 2 output if not entered or to guarantee accuracy
            for l in self.command_outputs["bgp_vr"].splitlines():
                parts = l.strip().split()
                if parts and re.match(r"^\d+\.\d+\.\d+\.\d+$", parts[0]):
                    peer_ip = parts[0].strip()
                    self.var_peer_ip.set(peer_ip)
                    self.append_log(f"[✓] Auto-detected Neighbor Peer IP from BGP: {peer_ip}\n")
                    break

            # Command 3: Port Alarms
            # show alarms all | match <date> | match <port_vni>
            port_vni = self.var_port_vni.get().strip() or "vni"
            cmd3 = f"show alarms all | match {today_str} | match {port_vni}"
            self.append_log(f"\n[*] [3/6] Port Alarms: {cmd3}\n")
            controller.send_command(cmd3, wait_after=1.5)
            out3 = self._wait_prompt(controller, cmd=cmd3, max_timeout=20.0)
            self.command_outputs["port_alarms"] = self._clean_cli_output(out3, cmd3)
            self.append_log(f"[✓] Captured Port Alarms.\n")

            # Command 4: BGP Alarms
            # show alarms all | match <date> | match <peer_ip>
            if peer_ip:
                cmd4 = f"show alarms all | match {today_str} | match {peer_ip}"
            else:
                cmd4 = f"show alarms all | match {today_str} | match bgp"
            self.append_log(f"\n[*] [4/6] BGP Alarms: {cmd4}\n")
            controller.send_command(cmd4, wait_after=1.5)
            out4 = self._wait_prompt(controller, cmd=cmd4, max_timeout=20.0)
            self.command_outputs["bgp_alarms"] = self._clean_cli_output(out4, cmd4)
            self.append_log(f"[✓] Captured BGP Alarms.\n")

            # Command 5: SD-WAN SLA Monitor Status Down
            # show orgs org <ORG>-NET sd-wan sla-monitor status-down | tab | match <circuit>
            cmd5 = f"show orgs org {org}-NET sd-wan sla-monitor status-down | tab | match {circuit}"
            self.append_log(f"\n[*] [5/6] SD-WAN SLA Monitor Down: {cmd5}\n")
            controller.send_command(cmd5, wait_after=1.5)
            out5 = self._wait_prompt(controller, cmd=cmd5, max_timeout=25.0)
            self.command_outputs["sla_down"] = self._clean_cli_output(out5, cmd5)
            self.append_log(f"[✓] Captured SLA Monitor Status-Down.\n")

            # Command 6: SD-WAN Detail Hub
            # show orgs org <ORG>-NET sd-wan detail <hub_name> | match <circuit>
            if hub_name:
                cmd6 = f"show orgs org {org}-NET sd-wan detail {hub_name} | match {circuit}"
                self.append_log(f"\n[*] [6/6] SD-WAN Detail for Hub {hub_name}: {cmd6}\n")
                controller.send_command(cmd6, wait_after=1.5)
                out6 = self._wait_prompt(controller, cmd=cmd6, max_timeout=25.0)
                self.command_outputs["sdwan_detail"] = self._clean_cli_output(out6, cmd6)
                self.append_log(f"[✓] Captured SD-WAN Detail.\n")

            # Disconnect cleanly all the way back to Bastion mcpeuser
            self.append_log("\n[*] Disconnecting nested router sessions back to Bastion mcpeuser...\n")
            for _ in range(6):
                out = controller.copy_terminal_output() or ""
                last_lines = "\n".join([l.strip() for l in out.strip().splitlines()[-4:] if l.strip()])
                if "mcpeuser" in last_lines or "PU1PAMMCPSGW" in last_lines or "[mcpeuser" in last_lines:
                    self.append_log("[✓] Confirmed back at Bastion prompt (mcpeuser).\n")
                    break
                controller.send_command("exit", wait_after=1.5)
            self.append_log("[✓] Session disconnection sequence completed.\n")

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

    def _generate_flt_summary(self):
        hn = self.var_hostname.get().strip() or "Target Router"
        circ = self.var_circuit.get().strip() or "Circuit"
        t_start = self.var_alarm_start.get().strip() or "Alarm Start Time"
        t_end = self.var_alarm_end.get().strip() or "Ongoing"
        hub_name = self.var_hub_name.get().strip() or "Location-A"
        hub_ip = self.var_hub_ip.get().strip() or "0.0.0.0"

        # Determine BGP status & WAN status from captured outputs
        bgp_out = self.command_outputs.get("bgp_vr", "")
        port_out = self.command_outputs.get("port_ident", "")

        is_bgp_up = "Established" in bgp_out or ("Connect" not in bgp_out and "Idle" not in bgp_out and "Active" not in bgp_out and bgp_out)
        is_port_up = " up " in port_out

        if is_port_up:
            wan_status = "Up"
        else:
            wan_status = "Down / Flapping"

        if is_bgp_up:
            bgp_status = "Down and up now (Recovered / Established)"
        else:
            bgp_status = "Down"

        # Filter out CGW- and VC- from SLA monitor output
        sla_raw = self.command_outputs.get("sla_down", "")
        sla_filtered_lines = []
        for line in sla_raw.splitlines():
            if "CGW-" in line or "VC-" in line:
                continue
            sla_filtered_lines.append(line)
        sla_clean = "\n".join(sla_filtered_lines)

        summary = []
        summary.append("Dear Team,\n")
        if t_start and t_start != "Alarm Start Time":
            summary.append(f"During the incident window from {t_start} to {t_end}, the site experienced an SDWAN connectivity disruption with HUB locations ({hub_name}, HUB End IP - {hub_ip}), and All other Hub Locations.\n")
        else:
            summary.append(f"During the incident window, the site experienced an SDWAN connectivity disruption with HUB locations ({hub_name}, HUB End IP - {hub_ip}), and All other Hub Locations.\n")
        summary.append("WAN interface status observed Up/down/flapping frequently. The local-loop BGP session between the CPE and PE is Up/Down/Flapping//Down and up now.\n")
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
            summary.append(f"\n[6] SD-WAN Detail ({hub_name}):\n{self.command_outputs.get('sdwan_detail', '')}\n")

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
