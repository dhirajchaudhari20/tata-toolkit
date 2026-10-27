"""
====================================================================================================
                        TATA COMMUNICATIONS - NETWORK OPERATIONS CENTER
                            AUDIT HISTORY & PAST RECORDS MODULE
====================================================================================================
Designed and Developed for Tata Communications / Advanced Network Operations
Maintains persistent execution records of all past Router Health Audits and CID Punchability checks.
Allows one-click opening of Excel workbooks, PDF reports, text logs, and screenshots, as well as
reloading historical scorecards directly into the main inspection table.
====================================================================================================
"""

import os
import sys
import json
import re
import subprocess
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from datetime import datetime

HISTORY_FILE = os.path.join(os.getcwd(), "audit_history.json")


class AuditHistoryFrame(ttk.Frame):
    """
    Modular Audit History and Past Records UI Frame.
    Displays all past runs, auto-discovers previous reports on disk, and allows direct inspection.
    """
    def __init__(self, parent, root=None, parent_gui=None):
        super().__init__(parent)
        self.root = root if root is not None else parent.winfo_toplevel()
        self.parent_gui = parent_gui
        self.records = []

        self._build_ui()
        self.load_history()

    def _build_ui(self):
        # ── Header & Filter Toolbar ──────────────────────────────────────────
        top_bar = tk.Frame(self, bg="#0f172a", padx=16, pady=12)
        top_bar.pack(fill="x", padx=10, pady=(8, 4))

        left_title = tk.Frame(top_bar, bg="#0f172a")
        left_title.pack(side="left")

        tk.Label(
            left_title,
            text="🕒 Audit History & Past Execution Records",
            font=("Segoe UI", 12, "bold"),
            fg="#38bdf8",
            bg="#0f172a"
        ).pack(anchor="w")

        tk.Label(
            left_title,
            text="Persistent logs of past Router Health Audits & TTBS CID checks — double-click any row to open report",
            font=("Segoe UI", 8),
            fg="#94a3b8",
            bg="#0f172a"
        ).pack(anchor="w", pady=(2, 0))

        # Right Summary Pill
        self.summary_badge = tk.Label(
            top_bar,
            text="Total Records: 0",
            font=("Segoe UI", 9, "bold"),
            bg="#1e293b",
            fg="#e2e8f0",
            padx=12,
            pady=4,
            relief="flat",
            highlightbackground="#334155",
            highlightthickness=1
        )
        self.summary_badge.pack(side="right")

        # ── Filter Controls Frame ────────────────────────────────────────────
        filter_frame = ttk.LabelFrame(self, text=" Filter & Search Past Records ", padding=(10, 6))
        filter_frame.pack(fill="x", padx=10, pady=(0, 6))

        # Module Filter
        ttk.Label(filter_frame, text="Filter Module:", font=("Segoe UI", 9, "bold")).pack(side="left", padx=(4, 6))
        self.filter_var = tk.StringVar(value="All")
        module_combo = ttk.Combobox(
            filter_frame,
            textvariable=self.filter_var,
            values=["All", "📡 Router Health Audits", "⚡ CID Punchability Checks"],
            state="readonly",
            width=26
        )
        module_combo.pack(side="left", padx=(0, 15))
        module_combo.bind("<<ComboboxSelected>>", lambda e: self.apply_filter())

        # Search Box
        ttk.Label(filter_frame, text="🔍 Search:", font=("Segoe UI", 9, "bold")).pack(side="left", padx=(4, 6))
        self.search_var = tk.StringVar()
        search_entry = ttk.Entry(filter_frame, textvariable=self.search_var, width=28)
        search_entry.pack(side="left", padx=(0, 10))
        search_entry.bind("<KeyRelease>", lambda e: self.apply_filter())

        ttk.Button(filter_frame, text="Clear Filter", command=self.clear_filter).pack(side="left", padx=(0, 8))
        ttk.Button(filter_frame, text="🔄 Refresh All", command=self.refresh).pack(side="right", padx=(4, 4))

        # ── History Treeview Table ───────────────────────────────────────────
        table_frame = ttk.Frame(self)
        table_frame.pack(fill="both", expand=True, padx=10, pady=(0, 6))

        columns = ("Timestamp", "Module", "Processed", "Passed", "Failed", "Status", "Excel File", "PDF / Log")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="browse")

        self.tree.heading("Timestamp", text="Date & Time")
        self.tree.heading("Module", text="Audit Module")
        self.tree.heading("Processed", text="Total Items")
        self.tree.heading("Passed", text="Passed / Punchable")
        self.tree.heading("Failed", text="Failed / Non-Punchable")
        self.tree.heading("Status", text="Status")
        self.tree.heading("Excel File", text="Excel Report")
        self.tree.heading("PDF / Log", text="PDF / Log File")

        self.tree.column("Timestamp", width=140, anchor="center")
        self.tree.column("Module", width=190, anchor="w")
        self.tree.column("Processed", width=95, anchor="center")
        self.tree.column("Passed", width=125, anchor="center")
        self.tree.column("Failed", width=135, anchor="center")
        self.tree.column("Status", width=100, anchor="center")
        self.tree.column("Excel File", width=220, anchor="w")
        self.tree.column("PDF / Log", width=190, anchor="w")

        # Scrollbars
        vsb = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(table_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        vsb.pack(side="right", fill="y")
        hsb.pack(side="bottom", fill="x")
        self.tree.pack(side="left", fill="both", expand=True)

        # Style tags
        self.tree.tag_configure("completed", foreground="#15803d", font=("Segoe UI", 9))
        self.tree.tag_configure("stopped", foreground="#b91c1c", font=("Segoe UI", 9))
        self.tree.tag_configure("discovered", foreground="#0369a1", font=("Segoe UI", 9))

        self.tree.bind("<Double-1>", lambda e: self.open_selected_excel())

        # ── Bottom Action Toolbar ────────────────────────────────────────────
        action_bar = tk.Frame(self, bg="#f1f5f9", padx=10, pady=6)
        action_bar.pack(fill="x", padx=10, pady=(0, 6))

        tk.Button(
            action_bar,
            text="📊 Open Excel File",
            font=("Segoe UI", 9, "bold"),
            bg="#0f766e",
            fg="white",
            activebackground="#0d9488",
            activeforeground="white",
            relief="flat",
            cursor="hand2",
            padx=10,
            pady=4,
            command=self.open_selected_excel
        ).pack(side="left", padx=(0, 6))

        tk.Button(
            action_bar,
            text="📄 Open PDF Report",
            font=("Segoe UI", 9, "bold"),
            bg="#7c3aed",
            fg="white",
            activebackground="#6d28d9",
            activeforeground="white",
            relief="flat",
            cursor="hand2",
            padx=10,
            pady=4,
            command=self.open_selected_pdf
        ).pack(side="left", padx=(0, 6))

        tk.Button(
            action_bar,
            text="📝 Open Text Log",
            font=("Segoe UI", 9),
            bg="#0284c7",
            fg="white",
            activebackground="#0369a1",
            activeforeground="white",
            relief="flat",
            cursor="hand2",
            padx=10,
            pady=4,
            command=self.open_selected_log
        ).pack(side="left", padx=(0, 6))

        tk.Button(
            action_bar,
            text="📸 Open Screenshots Folder",
            font=("Segoe UI", 9),
            bg="#475569",
            fg="white",
            activebackground="#334155",
            activeforeground="white",
            relief="flat",
            cursor="hand2",
            padx=10,
            pady=4,
            command=self.open_screenshots
        ).pack(side="left", padx=(0, 6))

        tk.Button(
            action_bar,
            text="📋 Load into Live Scorecard Table",
            font=("Segoe UI", 9, "bold"),
            bg="#16a34a",
            fg="white",
            activebackground="#15803d",
            activeforeground="white",
            relief="flat",
            cursor="hand2",
            padx=10,
            pady=4,
            command=self.load_into_scorecard
        ).pack(side="left", padx=(0, 6))

        tk.Button(
            action_bar,
            text="🗑️ Delete Record",
            font=("Segoe UI", 9),
            bg="#dc2626",
            fg="white",
            activebackground="#b91c1c",
            activeforeground="white",
            relief="flat",
            cursor="hand2",
            padx=8,
            pady=4,
            command=self.delete_selected
        ).pack(side="right")

    # ── Persistence & Data Operations ────────────────────────────────────────

    def load_history(self):
        """Loads records from audit_history.json and auto-discovers disk reports."""
        self.records = []
        if os.path.exists(HISTORY_FILE):
            try:
                with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                    self.records = json.load(f)
            except Exception:
                self.records = []

        # Auto-discover existing reports on disk that may not be in json yet
        self._auto_discover_disk_reports()
        self.save_history()
        self.apply_filter()

    def save_history(self):
        """Saves current records to audit_history.json."""
        try:
            with open(HISTORY_FILE, "w", encoding="utf-8") as f:
                json.dump(self.records, f, indent=4)
        except Exception:
            pass

    def add_record(self, module, total, passed, failed, status="Completed",
                   excel_path="", log_path="", pdf_path="", screenshot_dir=""):
        """Appends a new execution record and saves it persistently."""
        record = {
            "id": datetime.now().strftime("%Y%m%d%H%M%S%f"),
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "module": module,
            "total": total,
            "passed": passed,
            "failed": failed,
            "status": status,
            "excel_path": excel_path,
            "log_path": log_path,
            "pdf_path": pdf_path,
            "screenshot_dir": screenshot_dir or os.path.join(os.getcwd(), "screenshots")
        }
        # Insert at the beginning so latest appears first
        self.records.insert(0, record)
        self.save_history()
        self.apply_filter()

    def _auto_discover_disk_reports(self):
        """Scans the working directory for existing reports created before history was enabled."""
        known_files = {r.get("excel_path") for r in self.records if r.get("excel_path")}

        cwd = os.getcwd()
        try:
            files = os.listdir(cwd)
        except Exception:
            files = []

        discovered = []

        # 1. Discover Router Batch Audit Reports
        for fname in sorted(files):
            if fname.startswith("Router_Batch_Audit_Report_") and fname.endswith(".xlsx"):
                if fname not in known_files:
                    m = re.search(r"(\d{8})_(\d{6})", fname)
                    if m:
                        d, t = m.group(1), m.group(2)
                        ts = f"{d[:4]}-{d[4:6]}-{d[6:8]} {t[:2]}:{t[2:4]}:{t[4:6]}"
                    else:
                        ts = datetime.fromtimestamp(os.path.getmtime(fname)).strftime("%Y-%m-%d %H:%M:%S")

                    discovered.append({
                        "id": f"disc_{fname}",
                        "timestamp": ts,
                        "module": "📡 Router Health Audit",
                        "total": "Historical",
                        "passed": "In Excel",
                        "failed": "In Excel",
                        "status": "Archived",
                        "excel_path": fname,
                        "log_path": "",
                        "pdf_path": "",
                        "screenshot_dir": os.path.join(cwd, "screenshots")
                    })

            # 2. Discover CID Punchability Reports
            elif (fname.startswith("CID_Punchability_Report_") or fname.startswith("CID_Result_Comparison_")) and fname.endswith(".xlsx"):
                if fname not in known_files:
                    m = re.search(r"(\d{8})_(\d{6})", fname)
                    if m:
                        d, t = m.group(1), m.group(2)
                        ts = f"{d[:4]}-{d[4:6]}-{d[6:8]} {t[:2]}:{t[2:4]}:{t[4:6]}"
                    else:
                        ts = datetime.fromtimestamp(os.path.getmtime(fname)).strftime("%Y-%m-%d %H:%M:%S")

                    # Check if matching PDF exists in CID_Reports
                    pdf_guess = os.path.join("CID_Reports", fname.replace(".xlsx", ".pdf"))
                    pdf_actual = pdf_guess if os.path.exists(pdf_guess) else ""

                    discovered.append({
                        "id": f"disc_{fname}",
                        "timestamp": ts,
                        "module": "⚡ CID Punchability Check",
                        "total": "Historical",
                        "passed": "In Excel",
                        "failed": "In Excel",
                        "status": "Archived",
                        "excel_path": fname,
                        "log_path": "",
                        "pdf_path": pdf_actual,
                        "screenshot_dir": ""
                    })

        # Prepend discovered historical files
        if discovered:
            self.records.extend(discovered)
            # Sort by timestamp descending
            self.records.sort(key=lambda r: r.get("timestamp", ""), reverse=True)

    # ── UI Rendering & Filters ───────────────────────────────────────────────

    def apply_filter(self):
        """Filters treeview items based on module combo and search string."""
        for item in self.tree.get_children():
            self.tree.delete(item)

        mod_filter = self.filter_var.get()
        query = self.search_var.get().strip().lower()

        router_count = 0
        cid_count = 0
        visible_count = 0

        for r in self.records:
            mod = r.get("module", "")
            if "Router" in mod:
                router_count += 1
            elif "CID" in mod:
                cid_count += 1

            # Apply module filter
            if mod_filter == "📡 Router Health Audits" and "Router" not in mod:
                continue
            if mod_filter == "⚡ CID Punchability Checks" and "CID" not in mod:
                continue

            # Apply search query
            row_str = f"{r.get('timestamp')} {r.get('module')} {r.get('excel_path')} {r.get('pdf_path')} {r.get('status')}".lower()
            if query and query not in row_str:
                continue

            visible_count += 1
            status = r.get("status", "Completed")
            tag = "completed" if "Complete" in status else ("stopped" if "Stop" in status else "discovered")

            excel_name = os.path.basename(r.get("excel_path", "")) if r.get("excel_path") else "—"
            pdf_log_name = (
                os.path.basename(r.get("pdf_path", "")) if r.get("pdf_path")
                else (os.path.basename(r.get("log_path", "")) if r.get("log_path") else "—")
            )

            self.tree.insert(
                "",
                "end",
                iid=r.get("id"),
                values=(
                    r.get("timestamp", "—"),
                    r.get("module", "—"),
                    r.get("total", "—"),
                    r.get("passed", "—"),
                    r.get("failed", "—"),
                    status,
                    excel_name,
                    pdf_log_name
                ),
                tags=(tag,)
            )

        self.summary_badge.config(
            text=f"Total: {len(self.records)} Records | Routers: {router_count} | CIDs: {cid_count} | Showing: {visible_count}"
        )

    def clear_filter(self):
        self.filter_var.set("All")
        self.search_var.set("")
        self.apply_filter()

    def refresh(self):
        self.load_history()

    # ── Action Handlers ──────────────────────────────────────────────────────

    def _get_selected_record(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("Select Record", "Please select a record from the history table first.")
            return None
        rec_id = sel[0]
        for r in self.records:
            if r.get("id") == rec_id:
                return r
        return None

    def open_selected_excel(self):
        rec = self._get_selected_record()
        if not rec:
            return
        p = rec.get("excel_path", "")
        if p and os.path.exists(p):
            try:
                os.startfile(os.path.abspath(p))
            except Exception as e:
                messagebox.showerror("Error", f"Could not launch Excel file:\n{e}")
        else:
            messagebox.showwarning("File Missing", f"Excel report file was not found on disk:\n{p}")

    def open_selected_pdf(self):
        rec = self._get_selected_record()
        if not rec:
            return
        p = rec.get("pdf_path", "")
        if p and os.path.exists(p):
            try:
                os.startfile(os.path.abspath(p))
            except Exception as e:
                messagebox.showerror("Error", f"Could not open PDF file:\n{e}")
        else:
            messagebox.showinfo(
                "PDF Not Available",
                "No PDF report is associated with this record.\n"
                "(PDF reports are generated for TTBS CID Punchability checks)."
            )

    def open_selected_log(self):
        rec = self._get_selected_record()
        if not rec:
            return
        p = rec.get("log_path", "")
        if p and os.path.exists(p):
            try:
                os.startfile(os.path.abspath(p))
            except Exception as e:
                messagebox.showerror("Error", f"Could not open log file:\n{e}")
        else:
            messagebox.showinfo("Log Not Available", "No text log file path is registered for this record.")

    def open_screenshots(self):
        rec = self._get_selected_record()
        p = rec.get("screenshot_dir") if rec else os.path.join(os.getcwd(), "screenshots")
        if not p or not os.path.exists(p):
            p = os.path.join(os.getcwd(), "screenshots")
        os.makedirs(p, exist_ok=True)
        try:
            os.startfile(os.path.abspath(p))
        except Exception as e:
            messagebox.showerror("Error", f"Could not open screenshots folder:\n{e}")

    def load_into_scorecard(self):
        """Loads historical router audit records directly into Tab 1 live scorecard table."""
        rec = self._get_selected_record()
        if not rec:
            return
        if "Router" not in rec.get("module", ""):
            messagebox.showinfo("Module Notice", "Scorecard loading is available for Router Health Audit records.")
            return

        excel_p = rec.get("excel_path", "")
        if not excel_p or not os.path.exists(excel_p):
            messagebox.showerror("File Error", f"Cannot load scorecard — Excel file missing:\n{excel_p}")
            return

        if not self.parent_gui or not hasattr(self.parent_gui, "tree"):
            messagebox.showerror("UI Error", "Cannot connect to Router Health Auditor scorecard.")
            return

        try:
            import pandas as pd
            df = pd.read_excel(excel_p)
            rows = df.to_dict(orient="records")

            # Clear current tree and populate with historical rows
            self.parent_gui.tree.delete(*self.parent_gui.tree.get_children())
            self.parent_gui.audit_results = []

            for r in rows:
                verdict = str(r.get("SLA Verdict", "N/A"))
                tag = "pass" if verdict == "PASS" else ("fail" if verdict == "FAIL" else "error")

                rtt = r.get("Avg RTT (ms)")
                try:
                    rtt_disp = f"{float(rtt):.3f}" if pd.notna(rtt) else "N/A"
                except Exception:
                    rtt_disp = str(rtt) if pd.notna(rtt) else "N/A"

                loss = r.get("Packet Loss (%)", 0.0)
                loss_disp = f"{loss}%" if pd.notna(loss) else "N/A"

                self.parent_gui.tree.insert(
                    "",
                    "end",
                    values=(
                        str(r.get("Source Router", "")),
                        str(r.get("Destination Site", "")),
                        str(r.get("Access Circuit", "")),
                        str(r.get("Destination IP", "")),
                        str(r.get("Source IP", "")),
                        str(r.get("Control BGP (IP/Uptime)", "N/A")),
                        str(r.get("BGP Uptime", "N/A")),
                        str(r.get("BGP State", "N/A")),
                        str(r.get("WAN Interface", "N/A")),
                        str(r.get("VRRP State", "N/A")),
                        str(r.get("VRRP VIP", "N/A")),
                        loss_disp,
                        rtt_disp,
                        verdict
                    ),
                    tags=(tag,)
                )
                self.parent_gui.audit_results.append(r)

            # Update count badge and switch to Tab 1
            if hasattr(self.parent_gui, "total_circuits_label"):
                self.parent_gui.total_circuits_label.config(text=f"Total Tests: {len(rows)} (Historical Loaded)")
            if hasattr(self.parent_gui, "main_notebook") and hasattr(self.parent_gui, "tab_router"):
                self.parent_gui.main_notebook.select(self.parent_gui.tab_router)

            messagebox.showinfo(
                "Scorecard Loaded",
                f"Successfully loaded {len(rows)} historical records from:\n{os.path.basename(excel_p)}\n\n"
                f"Switched to 'Versa Router Health Auditor' tab to inspect the scorecard."
            )

        except Exception as e:
            messagebox.showerror("Load Error", f"Failed to parse and load historical Excel scorecard:\n{e}")

    def delete_selected(self):
        """Deletes selected record from history index (does not delete Excel file from disk)."""
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("Select Record", "Please select a record to delete.")
            return

        rec_id = sel[0]
        if messagebox.askyesno("Confirm Delete", "Remove this record from the history list?\n(The actual Excel/PDF report files will be kept on disk)."):
            self.records = [r for r in self.records if r.get("id") != rec_id]
            self.save_history()
            self.apply_filter()
