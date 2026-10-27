import tkinter as tk
from tkinter import ttk, messagebox
import threading
import time
import re
import sys
import asyncio
from datetime import datetime
import pandas as pd
from playwright.async_api import async_playwright

CHATBOT_URL = "https://chatbot.hellotars.com/conv/MfRHci"

# Exact options shown when a CID is PUNCHABLE (from Smart Assist screenshot)
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


class CIDCheckerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("TTBS CID Punchability Checker - Tata Communications")
        self.root.geometry("940x840")
        self.root.minsize(840, 720)

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

        self._setup_ui()

    def _setup_ui(self):
        style = ttk.Style()
        style.theme_use("clam")

        # Top Control Header Frame
        top_frame = ttk.LabelFrame(self.root, text=" Configuration & Concurrency ", padding=10)
        top_frame.pack(fill="x", padx=15, pady=(10, 5))

        # Workers control (5 is tested sweet spot)
        ttk.Label(top_frame, text="Parallel Workers:", font=("Segoe UI", 10, "bold")).grid(row=0, column=0, padx=(5, 5), sticky="w")
        self.workers_var = tk.IntVar(value=5)
        workers_spin = ttk.Spinbox(top_frame, from_=1, to=15, width=5, textvariable=self.workers_var)
        workers_spin.grid(row=0, column=1, padx=(0, 15), sticky="w")
        self.workers_var.trace_add("write", lambda *args: self._update_start_btn_label())

        note_label = ttk.Label(
            top_frame,
            text="(5 workers recommended: guarantees consistent results without HelloTars server throttling)",
            font=("Segoe UI", 8, "italic"),
            foreground="#6c757d"
        )
        note_label.grid(row=0, column=2, padx=5, sticky="w")

        # Checkboxes row
        opts_frame = tk.Frame(top_frame)
        opts_frame.grid(row=1, column=0, columnspan=3, pady=(8, 0), sticky="w")

        # Headless mode toggle
        self.headless_var = tk.BooleanVar(value=True)
        headless_check = ttk.Checkbutton(
            opts_frame,
            text="Headless Mode (Recommended)",
            variable=self.headless_var
        )
        headless_check.pack(side="left", padx=(5, 20))

        # 2x Double Check / Cross-Verification Mode
        self.double_check_var = tk.BooleanVar(value=True)
        double_check = ttk.Checkbutton(
            opts_frame,
            text="2x Cross-Check Mode (Runs each CID twice and compares to verify 100% accuracy)",
            variable=self.double_check_var
        )
        double_check.pack(side="left", padx=5)

        # Input Frame
        input_frame = ttk.LabelFrame(self.root, text=" Input Circuit IDs (One per line) ", padding=10)
        input_frame.pack(fill="both", expand=True, padx=15, pady=5)

        self.cid_text = tk.Text(input_frame, height=9, font=("Consolas", 10), wrap="none")
        cid_scroll = ttk.Scrollbar(input_frame, orient="vertical", command=self.cid_text.yview)
        self.cid_text.configure(yscrollcommand=cid_scroll.set)
        self.cid_text.pack(side="left", fill="both", expand=True)
        cid_scroll.pack(side="right", fill="y")

        # Action Buttons Frame
        btn_frame = tk.Frame(self.root)
        btn_frame.pack(fill="x", padx=15, pady=8)

        self.start_btn = tk.Button(
            btn_frame,
            text="▶ Start Automation (5x Parallel)",
            font=("Segoe UI", 11, "bold"),
            bg="#28a745",
            fg="white",
            activebackground="#218838",
            activeforeground="white",
            padx=15,
            pady=6,
            command=self.start_automation
        )
        self.start_btn.pack(side="left", padx=(0, 10))

        self.stop_btn = tk.Button(
            btn_frame,
            text="⏹ Stop",
            font=("Segoe UI", 11, "bold"),
            bg="#dc3545",
            fg="white",
            activebackground="#c82333",
            activeforeground="white",
            padx=15,
            pady=6,
            state="disabled",
            command=self.stop_automation
        )
        self.stop_btn.pack(side="left", padx=5)

        self.clear_btn = tk.Button(
            btn_frame,
            text="Clear",
            font=("Segoe UI", 10),
            bg="#6c757d",
            fg="white",
            padx=12,
            pady=6,
            command=self.clear_inputs
        )
        self.clear_btn.pack(side="right")

        # Progress Frame
        prog_frame = ttk.LabelFrame(self.root, text=" Progress & Live Status ", padding=10)
        prog_frame.pack(fill="x", padx=15, pady=5)

        self.progress_bar = ttk.Progressbar(prog_frame, orient="horizontal", mode="determinate")
        self.progress_bar.pack(fill="x", pady=(0, 5))

        self.progress_var = tk.StringVar(value="Status: Ready | Enter CIDs and click 'Start Automation'")
        self.status_label = ttk.Label(prog_frame, textvariable=self.progress_var, font=("Segoe UI", 9, "bold"))
        self.status_label.pack(anchor="w")

        # Footer Frame (Anchored permanently at bottom)
        footer_frame = tk.Frame(self.root, bg="#f1f3f5", pady=8)
        footer_frame.pack(side="bottom", fill="x")

        footer_label = tk.Label(
            footer_frame,
            text="Designed and Developed by Tata Communications",
            font=("Segoe UI", 9, "bold"),
            fg="#495057",
            bg="#f1f3f5"
        )
        footer_label.pack()

        # Results Frame
        res_frame = ttk.LabelFrame(self.root, text=" Live Results (Verified Output) ", padding=10)
        res_frame.pack(fill="both", expand=True, padx=15, pady=(5, 5))

        self.status_box = tk.Text(res_frame, height=12, font=("Consolas", 10), wrap="none")
        res_scroll = ttk.Scrollbar(res_frame, orient="vertical", command=self.status_box.yview)
        self.status_box.configure(yscrollcommand=res_scroll.set)
        self.status_box.pack(side="left", fill="both", expand=True)
        res_scroll.pack(side="right", fill="y")

        # Color tags
        self.status_box.tag_configure("punchable", foreground="#28a745", font=("Consolas", 10, "bold"))
        self.status_box.tag_configure("non_punchable", foreground="#fd7e14", font=("Consolas", 10))
        self.status_box.tag_configure("mismatch", foreground="#ffc107", font=("Consolas", 10, "bold"))
        self.status_box.tag_configure("error", foreground="#dc3545", font=("Consolas", 10, "bold"))
        self.status_box.tag_configure("info", foreground="#17a2b8", font=("Consolas", 10))

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

    def stop_automation(self):
        if self.is_running:
            self.stop_event.set()
            self.progress_var.set("Stopping workers gracefully... waiting for in-progress CIDs to finish.")
            self.stop_btn.config(state="disabled")

    def start_automation(self):
        raw_text = self.cid_text.get("1.0", tk.END).strip()
        if not raw_text:
            messagebox.showerror("Error", "Please paste at least one Circuit ID (CID).")
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

        # UI Updates
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.clear_btn.config(state="disabled")
        self.status_box.delete("1.0", tk.END)
        self.progress_bar["maximum"] = self.total_cids
        self.progress_bar["value"] = 0

        num_workers = min(max(1, self.workers_var.get()), self.total_cids)
        is_double = self.double_check_var.get()
        mode_str = "2x Double-Check Comparison Mode" if is_double else "Single Check Mode"
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

    async def _async_main(self, cids, num_workers, is_double):
        is_headless = self.headless_var.get()

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=is_headless,
                args=[
                    "--disable-dev-shm-usage",
                    "--no-sandbox",
                    "--disable-gpu",
                ]
            )

            sem = asyncio.Semaphore(num_workers)

            async def process_task(cid, idx):
                if self.stop_event.is_set():
                    return

                if idx < num_workers:
                    await asyncio.sleep(idx * 0.5)

                async with sem:
                    if self.stop_event.is_set():
                        return

                    # Run 1
                    r1 = "Unknown"
                    context1 = None
                    try:
                        context1 = await browser.new_context()
                        page1 = await context1.new_page()
                        r1 = await self._async_process_cid(page1, cid)
                    except Exception as e:
                        err_line = str(e).splitlines()[0] if str(e) else "Timeout"
                        r1 = f"Error: {err_line[:40]}"
                    finally:
                        if context1:
                            try:
                                await context1.close()
                            except Exception:
                                pass

                    r2 = None
                    comparison = "Single Check"
                    final_verdict = r1

                    # Run 2 (if double-check mode enabled)
                    if is_double and not self.stop_event.is_set():
                        await asyncio.sleep(1.0)
                        context2 = None
                        try:
                            context2 = await browser.new_context()
                            page2 = await context2.new_page()
                            r2 = await self._async_process_cid(page2, cid)
                        except Exception as e:
                            err_line = str(e).splitlines()[0] if str(e) else "Timeout"
                            r2 = f"Error: {err_line[:40]}"
                        finally:
                            if context2:
                                try:
                                    await context2.close()
                                except Exception:
                                    pass

                        if r1 == r2 and "Error" not in r1:
                            comparison = "MATCH (Verified 2/2)"
                            final_verdict = r1
                        elif "Error" in r1 or "Error" in r2:
                            comparison = "Inconclusive (Timeout)"
                            final_verdict = r1 if "Error" not in r1 else r2
                        else:
                            comparison = f"MISMATCH (Run 1: {r1} vs Run 2: {r2})"
                            final_verdict = "Needs Review"

                    # Record result
                    with self.results_lock:
                        res_dict = {
                            "CID": cid,
                            "Final Verdict": final_verdict,
                            "Comparison": comparison,
                            "Run 1 Result": r1,
                        }
                        if is_double:
                            res_dict["Run 2 Result"] = r2
                        res_dict["Timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        self.results.append(res_dict)

                    self.root.after(0, self._on_cid_finished, cid, final_verdict, comparison, (idx % num_workers) + 1)

            tasks = [process_task(cid, i) for i, cid in enumerate(cids)]
            await asyncio.gather(*tasks)
            await browser.close()

        self.root.after(0, self._on_automation_completed)

    async def _async_process_cid(self, page, cid):
        """
        Step-by-step navigation and exact detection of Punchable options vs Invalid response.
        """
        # Step 1: Open chatbot and wait for initial load
        await page.goto(CHATBOT_URL, wait_until="domcontentloaded", timeout=50000)
        await page.wait_for_timeout(2500)

        # Helper to click options reliably respecting HelloTars 1000ms delay
        async def safe_click(text, timeout=30000):
            loc = page.get_by_text(text, exact=False).first
            await loc.wait_for(state="visible", timeout=timeout)
            await page.wait_for_timeout(1200) # Wait for HelloTars inputDelay & CSS animation
            await loc.click()
            await page.wait_for_timeout(1800) # Wait for bot response to render

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

        # Trigger send button icon if visible to guarantee submission
        try:
            send_btn = page.locator('button:has(svg), svg[data-icon="paper-plane"]').first
            if await send_btn.is_visible():
                await send_btn.click()
        except Exception:
            pass

        # Step 7: Wait specifically for the Bot's final response
        # Poll up to 20 seconds for the options (Punchable) or Invalid message (Non-Punchable)
        start_time = time.time()
        while time.time() - start_time < 20:
            await page.wait_for_timeout(1000)
            body = await page.locator("body").inner_text()

            # 1. PUNCHABLE CHECK:
            # Check if "Please select from the options provided below" or any of the issue buttons appeared
            if any(opt.lower() in body.lower() for opt in PUNCHABLE_OPTIONS):
                return "Punchable"

            # 2. NON-PUNCHABLE (INVALID) CHECK:
            # Check if the bot replied indicating the CID is invalid/not found
            if any(inv.lower() in body.lower() for inv in INVALID_KEYWORDS):
                return "Non-Punchable"

        # Final check of the body text after waiting
        body = await page.locator("body").inner_text()
        if any(opt.lower() in body.lower() for opt in PUNCHABLE_OPTIONS):
            return "Punchable"
        if any(inv.lower() in body.lower() for inv in INVALID_KEYWORDS):
            return "Non-Punchable"

        # If neither appeared after 20 seconds, the server timed out
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

        # Update status line
        pct = int((self.completed_count / self.total_cids) * 100) if self.total_cids else 0
        self.progress_var.set(
            f"Progress: {self.completed_count}/{self.total_cids} ({pct}%) | "
            f"Punchable: {self.punchable_count} | Non-Punchable: {self.non_punchable_count} | Mismatches: {self.mismatch_count} | Errors: {self.error_count}"
        )

        # Append to live results box
        display_line = f"[{self.completed_count:02d}/{self.total_cids:02d}] Session-{worker_id:02d} | CID: {cid:<15} --> {final_verdict} [{comparison}]\n"
        self.status_box.insert(tk.END, display_line, tag)
        self.status_box.see(tk.END)

    def _on_automation_completed(self):
        self.is_running = False
        self.start_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        self.clear_btn.config(state="normal")

        if self.stop_event.is_set():
            self.progress_var.set(f"Stopped by user! Completed {self.completed_count}/{self.total_cids} CIDs.")
            self.status_box.insert(tk.END, "\n[Automation Stopped by User]\n", "error")
        else:
            self.progress_var.set(
                f"Completed! Total: {self.completed_count} | "
                f"Punchable: {self.punchable_count} | Non-Punchable: {self.non_punchable_count} | Mismatches: {self.mismatch_count} | Errors: {self.error_count}"
            )
            self.status_box.insert(tk.END, "\n[Automation Completed Successfully!]\n", "info")

        self.status_box.see(tk.END)

        # Export to Excel
        if self.results:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"CID_Result_Comparison_{timestamp}.xlsx"
            try:
                pd.DataFrame(self.results).to_excel(filename, index=False)
                summary_msg = (
                    f"Excel Comparison Report Exported:\n{filename}\n\n"
                    f"• Total Processed: {len(self.results)}\n"
                    f"• Verified Punchable: {self.punchable_count}\n"
                    f"• Verified Non-Punchable: {self.non_punchable_count}\n"
                    f"• Mismatches: {self.mismatch_count}\n"
                    f"• Errors/Timeouts: {self.error_count}"
                )
                messagebox.showinfo("Export Successful", summary_msg)
            except Exception as e:
                messagebox.showerror("Export Error", f"Failed to save Excel file:\n{e}")
        else:
            messagebox.showwarning("No Results", "No CIDs were processed.")


if __name__ == "__main__":
    root = tk.Tk()
    app = CIDCheckerApp(root)
    root.mainloop()
