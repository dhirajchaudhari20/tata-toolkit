# -*- coding: utf-8 -*-
"""
Automated Webhook Notification Engine for Versa SD-WAN Auditor.
Supports dispatching audit completion summaries and SLA failure alerts to:
- Microsoft Teams (via Incoming Webhook / Adaptive Card / Office 365 Connector)
- Slack (via Incoming Webhook / Block Kit)
Uses standard library urllib.request for maximum corporate environment compatibility.
"""

import os
import json
import urllib.request
import urllib.error
import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime

CONFIG_FILE = os.path.join(os.getcwd(), "webhook_config.json")

DEFAULT_CONFIG = {
    "enabled": False,
    "webhook_url": "",
    "platform": "teams",  # "teams" or "slack"
    "notify_on_complete": True,
    "notify_only_failures": False,
    "failure_threshold": 1
}

def load_webhook_config():
    if os.path.isfile(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                res = DEFAULT_CONFIG.copy()
                res.update(cfg)
                return res
        except Exception:
            pass
    return DEFAULT_CONFIG.copy()

def save_webhook_config(cfg):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
        return True
    except Exception:
        return False

def dispatch_notification(results, stats=None, test_mode=False):
    """
    Sends audit summary card to configured Microsoft Teams or Slack webhook.
    Returns (success: bool, message: str)
    """
    cfg = load_webhook_config()
    webhook_url = cfg.get("webhook_url", "").strip()

    if not test_mode and not cfg.get("enabled", False):
        return False, "Webhooks are disabled in settings."

    if not webhook_url:
        return False, "No Webhook URL configured."

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # If test mode, construct dummy stats
    if test_mode:
        total = 10
        passed = 9
        failed = 1
        pct = 90.0
        audit_title = "TEST NOTIFICATION: Versa SD-WAN Auditor Webhook Test"
        failed_routers = ["1075-KRNTKBNKLTD-NAVMUM-R2 (MPLS2 100% loss)"]
    else:
        total = len(results) if results else 0
        passed = sum(1 for r in results if "PASS" in str(r.get("SLA Verdict", "")).upper())
        failed = total - passed
        pct = (passed / total * 100) if total > 0 else 0.0
        audit_title = "Versa SD-WAN Router Health & SLA Audit Finished"

        # Check conditions
        if cfg.get("notify_only_failures", False) and failed < cfg.get("failure_threshold", 1):
            return False, "Suppressed: No SLA failures exceeded threshold."

        failed_routers = []
        for r in results:
            if "FAIL" in str(r.get("SLA Verdict", "")).upper():
                failed_routers.append(f"{r.get('Source Router')} &rarr; {r.get('Destination Site')} ({r.get('Access Circuit')})")
        failed_routers = failed_routers[:6]  # Limit to 6 lines in card

    platform = cfg.get("platform", "teams").lower()
    color_hex = "22c55e" if failed == 0 else "dc2626"

    if platform == "slack":
        # Slack Payload
        text_summary = f"*Audit Run: {now_str}*\n• Total Tests: `{total}`\n• SLA Passed: `{passed}`\n• SLA Failed: `{failed}`\n• Pass Rate: *{pct:.1f}%*"
        if failed_routers:
            text_summary += f"\n\n*Violations:*\n" + "\n".join([f"• {fr}" for fr in failed_routers])
        payload = {
            "text": f"*{audit_title}*",
            "attachments": [
                {
                    "color": "#" + color_hex,
                    "title": "Tata Communications NOC Audit Summary",
                    "text": text_summary,
                    "footer": "Versa SD-WAN Automated Health Auditor",
                    "ts": int(datetime.now().timestamp())
                }
            ]
        }
    else:
        # Microsoft Teams MessageCard Payload
        facts = [
            {"name": "Audit Time:", "value": now_str},
            {"name": "Total Circuit Tests:", "value": str(total)},
            {"name": "Passed Links (SLA Met):", "value": f"{passed} ({(passed/total*100 if total else 0):.1f}%)"},
            {"name": "Failed Links (SLA Violated):", "value": str(failed)},
            {"name": "Compliance Rate:", "value": f"{pct:.1f}%"}
        ]
        if failed_routers:
            facts.append({
                "name": "Failed Circuits:",
                "value": "<br>".join([f"• {fr}" for fr in failed_routers])
            })

        payload = {
            "@type": "MessageCard",
            "@context": "http://schema.org/extensions",
            "themeColor": color_hex,
            "summary": audit_title,
            "sections": [
                {
                    "activityTitle": f"📶 {audit_title}",
                    "activitySubtitle": "Tata Communications • Advanced Network Operations",
                    "facts": facts,
                    "markdown": True
                }
            ]
        }

    # Dispatch HTTP POST
    try:
        data_bytes = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            webhook_url,
            data=data_bytes,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "Versa-SDWAN-Auditor/2.1.0"
            }
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            status = resp.status
            if status in [200, 204]:
                return True, "Notification sent successfully."
            else:
                return False, f"Server returned HTTP {status}"
    except urllib.error.HTTPError as e:
        return False, f"HTTP Error {e.code}: {e.reason}"
    except urllib.error.URLError as e:
        return False, f"Connection Failed: {e.reason}"
    except Exception as e:
        return False, f"Dispatch Error: {e}"


class WebhookConfigDialog:
    def __init__(self, parent):
        self.parent = parent
        self.cfg = load_webhook_config()

        self.win = tk.Toplevel(parent)
        self.win.title("Automated Webhook Notifications (Teams / Slack)")
        self.win.geometry("580x420")
        self.win.resizable(False, False)
        self.win.transient(parent)
        self.win.grab_set()

        try:
            self.parent.update_idletasks()
            x = self.parent.winfo_x() + (self.parent.winfo_width() - 580) // 2
            y = self.parent.winfo_y() + (self.parent.winfo_height() - 420) // 2
            self.win.geometry(f"+{max(0, x)}+{max(0, y)}")
        except Exception:
            pass

        self._build_ui()

    def _build_ui(self):
        body = ttk.Frame(self.win, padding=(20, 16))
        body.pack(fill="both", expand=True)

        ttk.Label(
            body,
            text="NOC Channel Webhook Integration",
            font=("Segoe UI", 12, "bold")
        ).pack(anchor="w")

        ttk.Label(
            body,
            text="Post automated SLA summary cards directly to your team's Teams or Slack channel upon audit completion.",
            font=("Segoe UI", 9),
            foreground="#64748b",
            wraplength=530
        ).pack(anchor="w", pady=(2, 14))

        # Enable Checkbox
        self.enabled_var = tk.BooleanVar(value=self.cfg.get("enabled", False))
        ttk.Checkbutton(
            body,
            text="Enable Webhook Notifications on Audit Completion",
            variable=self.enabled_var
        ).pack(anchor="w", pady=(0, 10))

        # Platform selector
        plat_frame = ttk.Frame(body)
        plat_frame.pack(fill="x", pady=(0, 8))
        ttk.Label(plat_frame, text="Destination Platform:", font=("Segoe UI", 9, "bold")).pack(side="left")

        self.platform_var = tk.StringVar(value=self.cfg.get("platform", "teams"))
        ttk.Radiobutton(plat_frame, text="Microsoft Teams", variable=self.platform_var, value="teams").pack(side="left", padx=(14, 8))
        ttk.Radiobutton(plat_frame, text="Slack", variable=self.platform_var, value="slack").pack(side="left")

        # Webhook URL Entry
        ttk.Label(body, text="Webhook URL (Paste Incoming Webhook Connector URL):", font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(6, 2))
        self.url_var = tk.StringVar(value=self.cfg.get("webhook_url", ""))
        self.url_entry = ttk.Entry(body, textvariable=self.url_var, font=("Consolas", 9), width=65)
        self.url_entry.pack(fill="x", pady=(0, 12))

        # Only on Failures Checkbox
        self.only_fail_var = tk.BooleanVar(value=self.cfg.get("notify_only_failures", False))
        ttk.Checkbutton(
            body,
            text="Only dispatch alerts when SLA Failures occur (Suppress 100% PASS)",
            variable=self.only_fail_var
        ).pack(anchor="w", pady=(0, 14))

        # Status / Feedback label
        self.status_lbl = ttk.Label(body, text="", font=("Segoe UI", 9))
        self.status_lbl.pack(anchor="w", pady=(0, 10))

        # Button Bar
        btn_bar = ttk.Frame(body)
        btn_bar.pack(fill="x", side="bottom")

        test_btn = ttk.Button(
            btn_bar,
            text="🚀 Send Test Notification",
            command=self._send_test
        )
        test_btn.pack(side="left")

        close_btn = ttk.Button(btn_bar, text="Cancel", command=self.win.destroy)
        close_btn.pack(side="right", padx=(6, 0))

        save_btn = ttk.Button(btn_bar, text="💾 Save Configuration", command=self._save_and_close)
        save_btn.pack(side="right")

    def _send_test(self):
        url = self.url_var.get().strip()
        if not url:
            messagebox.showwarning("Missing URL", "Please enter a valid Webhook URL first.")
            return

        # Temporarily save current entry
        self.cfg["webhook_url"] = url
        self.cfg["platform"] = self.platform_var.get()
        save_webhook_config(self.cfg)

        self.status_lbl.config(text="Sending test notification...", foreground="#0284c7")
        self.win.update_idletasks()

        ok, msg = dispatch_notification(results=None, test_mode=True)
        if ok:
            self.status_lbl.config(text="✔ Test card sent successfully! Check your channel.", foreground="#16a34a")
            messagebox.showinfo("Success", "Test notification posted to channel successfully!")
        else:
            self.status_lbl.config(text=f"✖ Failed: {msg}", foreground="#dc2626")
            messagebox.showerror("Dispatch Failed", f"Could not send notification:\n{msg}")

    def _save_and_close(self):
        self.cfg["enabled"] = self.enabled_var.get()
        self.cfg["webhook_url"] = self.url_var.get().strip()
        self.cfg["platform"] = self.platform_var.get()
        self.cfg["notify_only_failures"] = self.only_fail_var.get()

        if save_webhook_config(self.cfg):
            messagebox.showinfo("Saved", "Webhook notification preferences saved.")
            self.win.destroy()
        else:
            messagebox.showerror("Error", "Could not save configuration.")


def open_webhook_dialog(parent):
    return WebhookConfigDialog(parent)
