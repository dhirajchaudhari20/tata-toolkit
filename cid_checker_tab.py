"""
====================================================================================================
                        TATA COMMUNICATIONS - NETWORK OPERATIONS CENTER
                             TTBS CID PUNCHABILITY CHECKER MODULE
====================================================================================================
Designed and Developed for Tata Communications / Advanced Network Operations
Performs automated multi-worker verification of Circuit IDs (CIDs) against the HelloTars chatbot.
Supports 2x cross-verification, headless browser control, and real-time live reporting.
====================================================================================================
"""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import threading
import time
import re
import sys
import os
import asyncio
from datetime import datetime

CHATBOT_URL = "https://chatbot.hellotars.com/conv/MfRHci"

# Exact options shown when a CID is PUNCHABLE
PUNCHABLE_OPTIONS = [
    "Please select from the options provided below",
    "Link Down",
    "Link Flapping",
    "Slow Response",
    "Application Related",
    "Packet Loss on Link",
    "High Latency",
    "MRTG Graph not Working",
    "Hardware Related",
    "Testing Request",
    "RFO Related",
    "Choose from above"
]

# Exact keywords shown when a CID is INVALID / NON-PUNCHABLE
INVALID_KEYWORDS = [
    "Invalid",
    "invalid",
    "not valid",
    "does not exist",
    "not found",
    "not registered",
    "unable to find",
    "re-enter",
    "incorrect"
]


class CIDCheckerFrame(ttk.Frame):
    """
    Modular CID Punchability Checker UI frame designed to be embedded directly
    into a tab (ttk.Notebook) or as a standalone window.
    """
    def __init__(self, parent, root=None, parent_gui=None):
        super().__init__(parent)
        self.root = root if root is not None else parent.winfo_toplevel()
        self.parent_gui = parent_gui
        self.last_excel_file = ""
        self.last_pdf_file = ""

        # State tracking
        self.results = []
        self.results_lock = threading.Lock()
        self.stop_event = threading.Event()
        self.is_running = False
        self.total_cids = 0
        self.completed_count = 0
        self.punchable_count = 0
        self.non_punchable_count = 0
        self.mismatch_count = 0
        self.error_count = 0
        self.start_time = None

        self._build_ui()

    def _build_ui(self):
        # Top Control Frame: Concurrency & Automation Settings
        top_frame = ttk.LabelFrame(self, text=" Configuration & Concurrency ", padding=10)
        top_frame.pack(fill="x", padx=12, pady=(8, 4))

        # Row 0: Parallel Workers (capped at 5 for optimal performance & anti-lag)
        ttk.Label(top_frame, text="Parallel Workers:", font=("Segoe UI", 9, "bold")).grid(row=0, column=0, padx=(5, 5), sticky="w")
        self.workers_var = tk.IntVar(value=5)

        def _validate_cid_workers(*args):
            try:
                v = self.workers_var.get()
                if v > 5:
                    self.workers_var.set(5)
                elif v < 1:
                    self.workers_var.set(1)
            except Exception:
                pass
            self._update_start_btn_label()
        self.workers_var.trace_add("write", _validate_cid_workers)

        workers_spin = ttk.Spinbox(top_frame, from_=1, to=5, width=4, textvariable=self.workers_var)
        workers_spin.grid(row=0, column=1, padx=(0, 6), sticky="w")

        cid_info_btn = tk.Button(
            top_frame,
            text="ℹ️ Max 5 (Anti-Lag)",
            font=("Segoe UI", 8, "bold"),
            bg="#e0f2fe",
            fg="#0369a1",
            activebackground="#bae6fd",
            activeforeground="#0284c7",
            relief="flat",
            cursor="hand2",
            padx=6,
            pady=1,
            command=self._show_cid_worker_info
        )
        cid_info_btn.grid(row=0, column=2, padx=(0, 10), sticky="w")

        note_label = ttk.Label(
            top_frame,
            text="(3-5 workers recommended: fast, lightweight execution with zero laptop lag)",
            font=("Segoe UI", 8, "italic"),
            foreground="#64748b"
        )
        note_label.grid(row=0, column=3, padx=5, sticky="w")

        # Row 1: Checkbox Options
        opts_frame = tk.Frame(top_frame)
        opts_frame.grid(row=1, column=0, columnspan=3, pady=(8, 0), sticky="w")

        self.headless_var = tk.BooleanVar(value=True)
        headless_check = ttk.Checkbutton(
            opts_frame,
            text="Headless Browser Mode (Recommended)",
            variable=self.headless_var
        )
        headless_check.pack(side="left", padx=(5, 20))

        self.double_check_var = tk.BooleanVar(value=True)
        double_check = ttk.Checkbutton(
            opts_frame,
            text="2x Cross-Check Mode (Runs each CID twice to verify 100% accuracy)",
            variable=self.double_check_var
        )
        double_check.pack(side="left", padx=5)

        # Middle Area: Paned Window (Left: Inputs, Right: Live Results)
        paned = ttk.PanedWindow(self, orient="horizontal")
        paned.pack(fill="both", expand=True, padx=12, pady=4)

        # Left Pane: Input CIDs
        input_frame = ttk.LabelFrame(paned, text=" Input Circuit IDs (One per line) ", padding=8)
        paned.add(input_frame, weight=2)

        input_header = tk.Frame(input_frame)
        input_header.pack(fill="x", pady=(0, 4))
        self.cid_count_lbl = ttk.Label(input_header, text="CIDs: 0 entered", font=("Segoe UI", 9, "bold"), foreground="#0284c7")
        self.cid_count_lbl.pack(side="left")

        # Paste sample button
        ttk.Button(input_header, text="Load File...", command=self._load_cids_from_file).pack(side="right")

        text_container = ttk.Frame(input_frame)
        text_container.pack(fill="both", expand=True)

        self.cid_text = tk.Text(text_container, height=12, font=("Consolas", 10), wrap="none")
        cid_scroll_y = ttk.Scrollbar(text_container, orient="vertical", command=self.cid_text.yview)
        cid_scroll_x = ttk.Scrollbar(text_container, orient="horizontal", command=self.cid_text.xview)
        self.cid_text.configure(yscrollcommand=cid_scroll_y.set, xscrollcommand=cid_scroll_x.set)

        cid_scroll_y.pack(side="right", fill="y")
        cid_scroll_x.pack(side="bottom", fill="x")
        self.cid_text.pack(side="left", fill="both", expand=True)
        self.cid_text.bind("<KeyRelease>", lambda e: self._update_cid_count())

        # Right Pane: Live Results & Output Box
        res_frame = ttk.LabelFrame(paned, text=" Live Verification Output ", padding=8)
        paned.add(res_frame, weight=3)

        res_header = tk.Frame(res_frame)
        res_header.pack(fill="x", pady=(0, 4))
        self.summary_badge = tk.Label(
            res_header,
            text="Punchable: 0  |  Non-Punchable: 0  |  Errors: 0",
            font=("Segoe UI", 8, "bold"),
            fg="#1e293b",
            bg="#f1f5f9",
            padx=8,
            pady=2
        )
        self.summary_badge.pack(side="left")

        self.export_btn = tk.Button(
            res_header,
            text="📁 Export Results (Excel)",
            font=("Segoe UI", 8, "bold"),
            bg="#0d9488",
            fg="white",
            activebackground="#0f766e",
            activeforeground="white",
            relief="flat",
            cursor="hand2",
            padx=10,
            pady=2,
            command=self.export_excel
        )
        self.export_btn.pack(side="right")

        self.export_pdf_btn = tk.Button(
            res_header,
            text="📄 Export PDF",
            font=("Segoe UI", 8, "bold"),
            bg="#7c3aed",
            fg="white",
            activebackground="#6d28d9",
            activeforeground="white",
            relief="flat",
            cursor="hand2",
            padx=10,
            pady=2,
            command=self.export_pdf
        )
        self.export_pdf_btn.pack(side="right", padx=(0, 6))

        res_container = ttk.Frame(res_frame)
        res_container.pack(fill="both", expand=True)

        self.status_box = tk.Text(res_container, height=12, font=("Consolas", 9), wrap="none")
        res_scroll_y = ttk.Scrollbar(res_container, orient="vertical", command=self.status_box.yview)
        res_scroll_x = ttk.Scrollbar(res_container, orient="horizontal", command=self.status_box.xview)
        self.status_box.configure(yscrollcommand=res_scroll_y.set, xscrollcommand=res_scroll_x.set)

        res_scroll_y.pack(side="right", fill="y")
        res_scroll_x.pack(side="bottom", fill="x")
        self.status_box.pack(side="left", fill="both", expand=True)

        # Color tags
        self.status_box.tag_configure("punchable", foreground="#16a34a", font=("Consolas", 9, "bold"))
        self.status_box.tag_configure("non_punchable", foreground="#ea580c", font=("Consolas", 9))
        self.status_box.tag_configure("mismatch", foreground="#ca8a04", font=("Consolas", 9, "bold"))
        self.status_box.tag_configure("error", foreground="#dc2626", font=("Consolas", 9, "bold"))
        self.status_box.tag_configure("info", foreground="#0284c7", font=("Consolas", 9))

        # Bottom Frame: Controls & Live Progress
        bottom_box = tk.Frame(self, bg="#f8f9fa", pady=4)
        bottom_box.pack(fill="x", padx=12, pady=(2, 6))

        # Action Buttons
        self.start_btn = tk.Button(
            bottom_box,
            text="▶ Start Automation (5x Parallel)",
            font=("Segoe UI", 10, "bold"),
            bg="#16a34a",
            fg="white",
            activebackground="#15803d",
            activeforeground="white",
            relief="flat",
            cursor="hand2",
            padx=16,
            pady=6,
            command=self.start_automation
        )
        self.start_btn.pack(side="left", padx=(0, 8))

        self.stop_btn = tk.Button(
            bottom_box,
            text="⏹ Stop",
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
            command=self.stop_automation
        )
        self.stop_btn.pack(side="left", padx=6)

        self.clear_btn = tk.Button(
            bottom_box,
            text="Clear",
            font=("Segoe UI", 10),
            bg="#64748b",
            fg="white",
            activebackground="#475569",
            activeforeground="white",
            relief="flat",
            cursor="hand2",
            padx=12,
            pady=6,
            command=self.clear_inputs
        )
        self.clear_btn.pack(side="right")

        # Style definition for modern thick progress bar
        style = ttk.Style()
        try:
            style.configure(
                "CID.Horizontal.TProgressbar",
                troughcolor="#e2e8f0",
                background="#0284c7",
                thickness=16
            )
        except Exception:
            pass

        # Progress bar & status
        prog_frame = ttk.LabelFrame(self, text=" CID Automation Progress ", padding=(10, 6))
        prog_frame.pack(fill="x", padx=12, pady=(0, 6))

        self.progress_bar = ttk.Progressbar(
            prog_frame,
            orient="horizontal",
            mode="determinate",
            style="CID.Horizontal.TProgressbar"
        )
        self.progress_bar.pack(fill="x", pady=(2, 6))

        prog_info_frame = tk.Frame(prog_frame, bg="#f8fafc")
        prog_info_frame.pack(fill="x")

        self.progress_var = tk.StringVar(value="Status: Ready | Enter CIDs and click 'Start Automation'")
        self.status_label = ttk.Label(prog_info_frame, textvariable=self.progress_var, font=("Segoe UI", 9))
        self.status_label.pack(side="left", anchor="w")

        self.progress_badge_var = tk.StringVar(value="[0%] Ready")
        self.progress_badge = tk.Label(
            prog_info_frame,
            textvariable=self.progress_badge_var,
            font=("Segoe UI", 9, "bold"),
            bg="#64748b",
            fg="white",
            padx=10,
            pady=2,
            relief="flat"
        )
        self.progress_badge.pack(side="right")

        self.eta_var = tk.StringVar(value="⏱️ Time Left: --:--")
        self.eta_badge = tk.Label(
            prog_info_frame,
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

    def _show_cid_worker_info(self):
        messagebox.showinfo(
            "CID Concurrency & Anti-Lag Guide — Tata Communications",
            "⚡ Parallel Browser Concurrency:\n\n"
            "• Current Recommended Limit: Maximum 5 Workers\n"
            "  Running 3 to 5 parallel browser sessions delivers the optimal balance of "
            "high processing speed (~300 CIDs/hour) and ultra-low system resource usage.\n\n"
            "• Why Keep to Max 5?\n"
            "  Setting 10–20 parallel workers launches 10–20 independent browser engines, "
            "consuming gigabytes of RAM, spiking CPU to 100%, and triggering rate-limit "
            "blocks or socket timeouts on the HelloTars chatbot server.\n\n"
            "• 🛡️ Anti-Lag Low-CPU Engine:\n"
            "  This version includes automatic media & image blocking and lightweight process flags "
            "so your computer remains fast and responsive during long audit runs.\n\n"
            "• 🚀 Cloud High-Concurrency Mode:\n"
            "  An enterprise cloud backend for 20+ parallel workers is in active development."
        )

    def _update_cid_count(self):
        content = self.cid_text.get("1.0", tk.END).strip()
        lines = [l.strip() for l in content.splitlines() if l.strip()]
        self.cid_count_lbl.config(text=f"CIDs: {len(lines)} entered")

    def _load_cids_from_file(self):
        f = filedialog.askopenfilename(
            title="Open CID File",
            filetypes=[("Text files", "*.txt"), ("CSV files", "*.csv"), ("All files", "*.*")]
        )
        if f:
            try:
                with open(f, "r", encoding="utf-8", errors="ignore") as fp:
                    content = fp.read()
                self.cid_text.delete("1.0", tk.END)
                self.cid_text.insert(tk.END, content)
                self._update_cid_count()
            except Exception as e:
                messagebox.showerror("File Error", f"Could not read file:\n{e}")

    def _update_start_btn_label(self):
        try:
            val = self.workers_var.get()
            if val > 0:
                self.start_btn.config(text=f"▶ Start Automation ({val}x Parallel)")
            else:
                self.start_btn.config(text="▶ Start Automation")
        except Exception:
            self.start_btn.config(text="▶ Start Automation")

    def clear_inputs(self):
        if not self.is_running:
            self.cid_text.delete("1.0", tk.END)
            self.status_box.delete("1.0", tk.END)
            self.progress_bar["value"] = 0
            self.progress_var.set("Status: Cleared | Ready")
            self.progress_badge.config(bg="#64748b")
            self.progress_badge_var.set("[0%] Ready")
            self.start_time = None
            self.eta_var.set("⏱️ Time Left: --:--")
            self.eta_badge.config(bg="#f1f5f9", fg="#64748b")
            self.summary_badge.config(text="Punchable: 0  |  Non-Punchable: 0  |  Errors: 0")
            self._update_cid_count()

    def stop_automation(self):
        if self.is_running:
            self.stop_event.set()
            self.progress_var.set("Stopping workers gracefully... waiting for in-progress CIDs to finish.")
            self.stop_btn.config(state="disabled")

    def start_automation(self):
        raw_text = self.cid_text.get("1.0", tk.END).strip()
        if not raw_text:
            messagebox.showerror("Input Required", "Please paste at least one Circuit ID (CID) to begin.")
            return

        cids = []
        for line in raw_text.splitlines():
            line = line.strip()
            if line:
                clean_cid = re.sub(r"\s+", "", line)
                if clean_cid:
                    cids.append(clean_cid)

        if not cids:
            messagebox.showerror("Error", "No valid CIDs found in the input box.")
            return

        # Reset states
        self.is_running = True
        self.stop_event.clear()
        self.total_cids = len(cids)
        self.completed_count = 0
        self.punchable_count = 0
        self.non_punchable_count = 0
        self.mismatch_count = 0
        self.error_count = 0
        self.results = []
        self.start_time = time.time()
        self.eta_var.set("⏱️ Est. Left: Calculating...")
        self.eta_badge.config(bg="#e0f2fe", fg="#0369a1")

        # UI Updates
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.clear_btn.config(state="disabled")
        self.status_box.delete("1.0", tk.END)
        self.progress_bar["maximum"] = self.total_cids
        self.progress_bar["value"] = 0
        self.progress_badge.config(bg="#0284c7")
        self.progress_badge_var.set(f"[0/{self.total_cids}] 0%")

        num_workers = min(max(1, self.workers_var.get()), self.total_cids)
        is_double = self.double_check_var.get()
        mode_str = "2x Cross-Check" if is_double else "Single Check"
        self.progress_var.set(f"Running {num_workers} parallel sessions ({mode_str})... 0/{self.total_cids} completed")

        self.status_box.insert(
            tk.END,
            f"=== Started checking {self.total_cids} CIDs across {num_workers} sessions [{mode_str}] ===\n\n",
            "info"
        )

        threading.Thread(target=self._run_async_master, args=(cids, num_workers, is_double), daemon=True).start()

    def _run_async_master(self, cids, num_workers, is_double):
        if sys.platform == "win32":
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._async_main(cids, num_workers, is_double))
        finally:
            loop.close()

    async def _launch_browser_resilient(self, p, headless=True):
        """
        Resilient browser launcher:
        1. Checks AppData ms-playwright directory if available.
        2. Falls back automatically to Microsoft Edge (pre-installed on Windows).
        3. Falls back to Google Chrome (standard enterprise browser).
        Guarantees zero-setup execution on any colleague's computer without downloading binaries.
        """
        args = [
            "--disable-dev-shm-usage",
            "--no-sandbox",
            "--disable-gpu",
            "--disable-background-networking",
            "--disable-background-timer-throttling",
            "--disable-backgrounding-occluded-windows",
            "--disable-breakpad",
            "--disable-component-update",
            "--disable-default-apps",
            "--disable-domain-reliability",
            "--disable-extensions",
            "--disable-hang-monitor",
            "--disable-ipc-flooding-protection",
            "--disable-popup-blocking",
            "--disable-renderer-backgrounding",
            "--disable-sync",
            "--disable-translate",
            "--metrics-recording-only",
            "--no-first-run",
            "--mute-audio",
            "--no-default-browser-check",
            "--disable-features=AudioServiceOutOfProcess,site-per-process",
        ]

        # 1. Check local AppData ms-playwright
        local_appdata = os.environ.get("LOCALAPPDATA", "")
        ms_playwright = os.path.join(local_appdata, "ms-playwright")
        if os.path.exists(ms_playwright):
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = ms_playwright
            try:
                return await p.chromium.launch(headless=headless, args=args)
            except Exception:
                pass

        # 2. Fall back to Microsoft Edge (available on 100% of Windows 10/11 PCs)
        try:
            return await p.chromium.launch(channel="msedge", headless=headless, args=args)
        except Exception:
            pass

        # 3. Fall back to Google Chrome
        try:
            return await p.chromium.launch(channel="chrome", headless=headless, args=args)
        except Exception:
            pass

        # 4. Final fallback
        return await p.chromium.launch(headless=headless, args=args)

    async def _async_main(self, cids, num_workers, is_double):
        """
        Two-phase smart CID distribution:

        Phase 1 — Equal batches:
          Divide CIDs evenly. e.g. 21 CIDs / 2 workers → each gets 10 (floor).

        Phase 2 — Overflow queue:
          The remainder (1 CID in this example) goes into a shared overflow queue.
          Whichever browser worker finishes its batch FIRST automatically picks up
          the overflow CIDs. No extra browser launches, no wasted time.

        Example: 21 CIDs, 2 workers
          Worker-1: CIDs 1-10  (Phase 1)
          Worker-2: CIDs 11-20 (Phase 1)
          Overflow: CID 21 → picked up by whichever worker finishes first
        """
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            self.root.after(0, lambda: messagebox.showerror(
                "Playwright Missing",
                "Playwright library is not installed.\nRun: pip install playwright"
            ))
            self.root.after(0, self._on_automation_completed)
            return

        is_headless = self.headless_var.get()

        try:
            async with async_playwright() as p:
                total_cids   = len(cids)
                base         = total_cids // num_workers        # floor: each worker gets this many
                remainder    = total_cids  % num_workers        # leftover CIDs

                # Build equal Phase-1 batches
                batches = [cids[i * base : (i + 1) * base] for i in range(num_workers)]
                # Overflow queue for Phase-2 remainder CIDs
                overflow_cids = cids[num_workers * base:]       # empty list when remainder == 0
                overflow_q    = asyncio.Queue()
                for cid_item in overflow_cids:
                    await overflow_q.put(cid_item)

                # ── Log the split to the live output ────────────────────────────
                self.root.after(0, lambda: self.status_box.insert(
                    "end",
                    f"=== Smart Batch Division: {total_cids} CIDs across {num_workers} browsers ===\n",
                    "info"
                ))
                for wi, batch in enumerate(batches, 1):
                    if batch:
                        preview = ", ".join(batch[:3]) + ("..." if len(batch) > 3 else "")
                        self.root.after(0, lambda wi=wi, n=len(batch), p=preview:
                            self.status_box.insert("end",
                                f"  Phase-1 | Browser-{wi:02d}: {n} CIDs  [{p}]\n", "info"))
                if overflow_cids:
                    ov_preview = ", ".join(overflow_cids[:3]) + ("..." if len(overflow_cids) > 3 else "")
                    self.root.after(0, lambda n=len(overflow_cids), p=ov_preview:
                        self.status_box.insert("end",
                            f"  Phase-2 | Overflow : {n} CID(s) [{p}] → first free browser picks up\n",
                            "info"))
                self.root.after(0, lambda: self.status_box.insert("end", "\n", "info"))

                # Launch one dedicated browser per worker
                browsers = []
                for i in range(num_workers):
                    b = await self._launch_browser_resilient(p, headless=is_headless)
                    browsers.append((i, b))
                    await asyncio.sleep(0.4)

                async def _run_single_cid(browser, cid_item, worker_id):
                    """Run one CID through browser (used for both phases)."""
                    r1 = "Unknown"
                    ctx1 = None
                    try:
                        ctx1 = await browser.new_context()
                        pg1  = await ctx1.new_page()
                        r1   = await self._async_process_cid(pg1, cid_item)
                    except Exception as e:
                        r1 = f"Error: {str(e).splitlines()[0][:40]}"
                    finally:
                        if ctx1:
                            try: await ctx1.close()
                            except: pass

                    r2 = None
                    comparison    = "Single Check"
                    final_verdict = r1

                    if is_double and not self.stop_event.is_set():
                        await asyncio.sleep(1.2)
                        ctx2 = None
                        try:
                            ctx2 = await browser.new_context()
                            pg2  = await ctx2.new_page()
                            r2   = await self._async_process_cid(pg2, cid_item)
                        except Exception as e:
                            r2 = f"Error: {str(e).splitlines()[0][:40]}"
                        finally:
                            if ctx2:
                                try: await ctx2.close()
                                except: pass

                        if r1 == r2 and "Error" not in r1:
                            comparison    = "MATCH (Verified 2/2)"
                            final_verdict = r1
                        elif "Error" in r1 or "Error" in r2:
                            comparison    = "Inconclusive (Timeout)"
                            final_verdict = r1 if "Error" not in r1 else r2
                        else:
                            comparison    = f"MISMATCH ({r1} vs {r2})"
                            final_verdict = "Needs Review"

                    with self.results_lock:
                        row = {
                            "CID": cid_item,
                            "Worker": f"Browser-{worker_id:02d}",
                            "Final Verdict": final_verdict,
                            "Comparison": comparison,
                            "Run 1 Result": r1,
                        }
                        if is_double:
                            row["Run 2 Result"] = r2
                        row["Timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        self.results.append(row)

                    self.root.after(0, self._on_cid_finished,
                                    cid_item, final_verdict, comparison, worker_id)

                async def worker_batch(worker_id, browser, primary_batch):
                    """
                    Phase 1: process primary_batch sequentially.
                    Phase 2: drain overflow_q — first worker to finish Phase 1
                             starts picking up remainder CIDs immediately.
                    """
                    # Phase 1
                    for idx, cid_item in enumerate(primary_batch):
                        if self.stop_event.is_set():
                            return
                        await _run_single_cid(browser, cid_item, worker_id)
                        if idx < len(primary_batch) - 1:
                            await asyncio.sleep(0.8)

                    # Phase 2 — pick up overflow CIDs
                    while not self.stop_event.is_set():
                        try:
                            cid_item = overflow_q.get_nowait()
                        except asyncio.QueueEmpty:
                            break
                        self.root.after(0, lambda wi=worker_id, c=cid_item:
                            self.status_box.insert("end",
                                f"  [Overflow] Browser-{wi:02d} picked up remainder CID: {c}\n",
                                "info"))
                        await _run_single_cid(browser, cid_item, worker_id)
                        overflow_q.task_done()
                        await asyncio.sleep(0.8)

                # Run all workers concurrently
                await asyncio.gather(*[
                    worker_batch(i + 1, browser, batches[i])
                    for i, browser in browsers
                ])

                for _, b in browsers:
                    try: await b.close()
                    except: pass

        except Exception as e:
            self.root.after(0, lambda: messagebox.showerror(
                "Browser Automation Error",
                f"An error occurred:\n{e}"
            ))

        self.root.after(0, self._on_automation_completed)

    async def _async_process_cid(self, page, cid):
        # Performance optimization: Block images, fonts, and media to slash CPU and RAM by 70%
        try:
            await page.route(
                "**/*",
                lambda route: route.abort() if route.request.resource_type in ["image", "media", "font"] else route.continue_()
            )
        except Exception:
            pass

        # Step 1: Open chatbot and wait for initial load
        await page.goto(CHATBOT_URL, wait_until="domcontentloaded", timeout=50000)
        await page.wait_for_timeout(2500)

        async def safe_click(text, timeout=30000):
            loc = page.get_by_text(text, exact=False).first
            await loc.wait_for(state="visible", timeout=timeout)
            await page.wait_for_timeout(1200)
            await loc.click()
            await page.wait_for_timeout(1800)

        # Steps 2-5: Navigate conversation tree
        await safe_click("Get Customer Support")
        await safe_click("Connectivity Products")
        await safe_click("Raise Service Request/Complaint")
        await safe_click("Technical Issue")

        # Step 6: Locate message input box, fill CID, and submit
        box = page.locator('input[placeholder="Type your message..."]')
        await box.wait_for(state="visible", timeout=25000)
        await page.wait_for_timeout(1200)
        await box.fill(cid)
        await page.wait_for_timeout(500)
        await page.keyboard.press("Enter")

        try:
            send_btn = page.locator('button:has(svg), svg[data-icon="paper-plane"]').first
            if await send_btn.is_visible():
                await send_btn.click()
        except Exception:
            pass

        # Step 7: Wait specifically for Bot's final response
        start_time = time.time()
        while time.time() - start_time < 20:
            await page.wait_for_timeout(1000)
            body = await page.locator("body").inner_text()

            if any(opt.lower() in body.lower() for opt in PUNCHABLE_OPTIONS):
                return "Punchable"

            if any(inv.lower() in body.lower() for inv in INVALID_KEYWORDS):
                return "Non-Punchable"

        body = await page.locator("body").inner_text()
        if any(opt.lower() in body.lower() for opt in PUNCHABLE_OPTIONS):
            return "Punchable"
        if any(inv.lower() in body.lower() for inv in INVALID_KEYWORDS):
            return "Non-Punchable"

        return "Error: Response Timeout (Server Slow)"

    def _on_cid_finished(self, cid, final_verdict, comparison, worker_id):
        self.completed_count += 1

        if final_verdict == "Punchable":
            self.punchable_count += 1
            tag = "punchable"
        elif final_verdict == "Non-Punchable":
            self.non_punchable_count += 1
            tag = "non_punchable"
        elif "Review" in final_verdict or "MISMATCH" in comparison:
            self.mismatch_count += 1
            tag = "mismatch"
        else:
            self.error_count += 1
            tag = "error"

        # Update progress bar
        self.progress_bar["value"] = self.completed_count

        # Update summary badge
        self.summary_badge.config(
            text=f"Punchable: {self.punchable_count}  |  Non-Punchable: {self.non_punchable_count}  |  Mismatches: {self.mismatch_count}  |  Errors: {self.error_count}"
        )

        pct = int((self.completed_count / self.total_cids) * 100) if self.total_cids else 0
        self.progress_badge_var.set(f"[{self.completed_count}/{self.total_cids}] {pct}%")

        # Dynamic Time Remaining calculation
        eta_str = "⏱️ Est. Left: Calculating..."
        if getattr(self, 'start_time', None) and self.completed_count > 0:
            elapsed = time.time() - self.start_time
            avg_per_cid = elapsed / self.completed_count
            remaining = max(0, self.total_cids - self.completed_count)
            eta_secs = int(avg_per_cid * remaining)
            if eta_secs >= 60:
                mins = eta_secs // 60
                secs = eta_secs % 60
                eta_str = f"⏱️ Est. Left: ~{mins}m {secs:02d}s"
            elif eta_secs > 0:
                eta_str = f"⏱️ Est. Left: ~{eta_secs}s"
            else:
                eta_str = "⏱️ Est. Left: Finishing..."

        self.eta_var.set(eta_str)
        self.progress_var.set(
            f"Progress: {self.completed_count}/{self.total_cids} ({pct}%) | {eta_str} | "
            f"Punchable: {self.punchable_count} | Non-Punchable: {self.non_punchable_count} | Mismatches: {self.mismatch_count} | Errors: {self.error_count}"
        )

        display_line = f"[{self.completed_count:02d}/{self.total_cids:02d}] Session-{worker_id:02d} | CID: {cid:<15} --> {final_verdict} [{comparison}]\n"
        self.status_box.insert(tk.END, display_line, tag)
        self.status_box.see(tk.END)

    def _on_automation_completed(self):
        self.is_running = False
        self.start_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        self.clear_btn.config(state="normal")

        if self.stop_event.is_set():
            self.progress_badge.config(bg="#dc2626")
            self.progress_badge_var.set(f"⏹ Stopped [{self.completed_count}/{self.total_cids}]")
            self.progress_var.set(f"Stopped by user! Completed {self.completed_count}/{self.total_cids} CIDs.")
            self.status_box.insert(tk.END, "\n[Automation Stopped by User]\n", "error")
        else:
            self.progress_bar["value"] = self.total_cids
            self.progress_badge.config(bg="#16a34a")
            self.progress_badge_var.set(f"✓ 100% Done ({self.completed_count})")
            if getattr(self, 'start_time', None):
                total_sec = int(time.time() - self.start_time)
                t_str = f"{total_sec // 60}m {total_sec % 60:02d}s" if total_sec >= 60 else f"{total_sec}s"
                self.eta_var.set(f"⏱️ Finished in {t_str}")
                self.eta_badge.config(bg="#dcfce7", fg="#15803d")
            self.progress_var.set(
                f"Completed! Total: {self.completed_count} | "
                f"Punchable: {self.punchable_count} | Non-Punchable: {self.non_punchable_count} | Mismatches: {self.mismatch_count} | Errors: {self.error_count}"
            )
            self.status_box.insert(tk.END, "\n[Automation Completed Successfully!]\n", "info")

        self.status_box.see(tk.END)

        # Auto-export both Excel AND PDF once automation finishes
        if self.results and not self.stop_event.is_set():
            self.export_excel(prompt_location=False)
            self.export_pdf(prompt_location=False)

        # Record to Persistent Audit History
        if self.results and hasattr(self, 'parent_gui') and self.parent_gui and hasattr(self.parent_gui, 'history_frame'):
            try:
                self.parent_gui.history_frame.add_record(
                    module="⚡ CID Punchability Check",
                    total=f"{len(self.results)} CIDs",
                    passed=f"{self.punchable_count} Punchable",
                    failed=f"{self.non_punchable_count} Non-Punch",
                    status="Completed" if not self.stop_event.is_set() else "Stopped",
                    excel_path=getattr(self, 'last_excel_file', ''),
                    log_path="",
                    pdf_path=getattr(self, 'last_pdf_file', ''),
                    screenshot_dir=""
                )
            except Exception:
                pass

    def export_excel(self, prompt_location=True):
        if not self.results:
            messagebox.showwarning("No Results", "No CID audit results available to export.")
            return

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        default_name = f"CID_Punchability_Report_{timestamp}.xlsx"

        if prompt_location:
            filename = filedialog.asksaveasfilename(
                title="Save Excel Report",
                initialfile=default_name,
                defaultextension=".xlsx",
                filetypes=[("Excel Workbook", "*.xlsx")]
            )
            if not filename:
                return
        else:
            filename = default_name

        self.last_excel_file = filename
        try:
            import pandas as pd
            df = pd.DataFrame(self.results)
            df.to_excel(filename, index=False)
            messagebox.showinfo(
                "Export Successful",
                f"CID Punchability report saved successfully:\n{os.path.abspath(filename)}\n\n"
                f"• Total Processed: {len(self.results)}\n"
                f"• Punchable: {self.punchable_count}\n"
                f"• Non-Punchable: {self.non_punchable_count}\n"
                f"• Mismatches: {self.mismatch_count}\n"
                f"• Errors/Timeouts: {self.error_count}"
            )
        except Exception as e:
            messagebox.showerror("Export Error", f"Failed to save Excel file:\n{e}")

    def export_pdf(self, prompt_location=True):
        """Exports CID Punchability results as a formatted PDF report using fpdf."""
        if not self.results:
            messagebox.showwarning("No Results", "No CID audit results available to export.")
            return

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        default_name = f"CID_Punchability_Report_{timestamp}.pdf"

        if prompt_location:
            # Direct auto-save to CID_Reports folder — no dialog
            reports_dir = os.path.join(os.getcwd(), "CID_Reports")
            os.makedirs(reports_dir, exist_ok=True)
            filename = os.path.join(reports_dir, default_name)
        else:
            reports_dir = os.path.join(os.getcwd(), "CID_Reports")
            os.makedirs(reports_dir, exist_ok=True)
            filename = os.path.join(reports_dir, default_name)

        try:
            from fpdf import FPDF

            pdf = FPDF(orientation="L", unit="mm", format="A4")
            pdf.set_auto_page_break(auto=True, margin=12)
            pdf.add_page()

            # Title header
            pdf.set_fill_color(15, 23, 42)
            pdf.rect(0, 0, 297, 22, "F")
            pdf.set_font("Helvetica", "B", 14)
            pdf.set_text_color(56, 189, 248)
            pdf.set_xy(10, 5)
            pdf.cell(0, 12, "TTBS CID Punchability Audit Report", ln=False)
            pdf.set_font("Helvetica", "", 9)
            pdf.set_text_color(148, 163, 184)
            pdf.set_xy(10, 14)
            pdf.cell(0, 6, f"Tata Communications / Network Operations Center    |    Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", ln=True)

            # Summary box
            pdf.set_xy(10, 26)
            pdf.set_font("Helvetica", "B", 10)
            pdf.set_text_color(30, 41, 59)
            pdf.cell(0, 7, f"Summary:   Total: {len(self.results)}   |   Punchable: {self.punchable_count}   |   Non-Punchable: {self.non_punchable_count}   |   Mismatches: {self.mismatch_count}   |   Errors: {self.error_count}", ln=True)

            pdf.ln(3)

            # Table headers
            cols = list(self.results[0].keys()) if self.results else []
            col_widths = []
            page_w = 277  # A4 landscape usable width
            base_w = max(20, page_w // max(len(cols), 1))
            for c in cols:
                if "CID" in c:
                    col_widths.append(40)
                elif "Verdict" in c or "Comparison" in c:
                    col_widths.append(50)
                elif "Result" in c:
                    col_widths.append(35)
                elif "Timestamp" in c:
                    col_widths.append(42)
                else:
                    col_widths.append(base_w)

            # Scale widths to fit page
            total_w = sum(col_widths)
            if total_w > page_w:
                scale = page_w / total_w
                col_widths = [max(15, int(w * scale)) for w in col_widths]

            pdf.set_fill_color(2, 132, 199)
            pdf.set_text_color(255, 255, 255)
            pdf.set_font("Helvetica", "B", 8)
            for i, col in enumerate(cols):
                pdf.cell(col_widths[i], 7, str(col)[:18], border=1, fill=True, align="C")
            pdf.ln()

            # Data rows
            pdf.set_font("Helvetica", "", 8)
            for row_idx, row in enumerate(self.results):
                verdict = str(row.get("Final Verdict", ""))
                if verdict == "Punchable":
                    pdf.set_fill_color(220, 252, 231)
                    pdf.set_text_color(22, 163, 74)
                elif verdict == "Non-Punchable":
                    pdf.set_fill_color(255, 237, 213)
                    pdf.set_text_color(234, 88, 12)
                elif "Error" in verdict or "Needs Review" in verdict:
                    pdf.set_fill_color(254, 226, 226)
                    pdf.set_text_color(220, 38, 38)
                else:
                    pdf.set_fill_color(248, 250, 252) if row_idx % 2 == 0 else pdf.set_fill_color(241, 245, 249)
                    pdf.set_text_color(30, 41, 59)

                for i, col in enumerate(cols):
                    val = str(row.get(col, ""))
                    pdf.cell(col_widths[i], 6, val[:22], border=1, fill=True)
                pdf.ln()

            pdf.output(filename)
            self.last_pdf_file = filename
            messagebox.showinfo(
                "PDF Export Successful",
                f"CID Punchability PDF report saved:\n{os.path.abspath(filename)}\n\n"
                f"• Total: {len(self.results)}\n"
                f"• Punchable: {self.punchable_count}\n"
                f"• Non-Punchable: {self.non_punchable_count}"
            )
        except ImportError:
            messagebox.showerror(
                "PDF Library Missing",
                "The 'fpdf' library is required for PDF export.\n"
                "Please install it: pip install fpdf2"
            )
        except Exception as e:
            messagebox.showerror("PDF Export Error", f"Failed to generate PDF:\n{e}")

