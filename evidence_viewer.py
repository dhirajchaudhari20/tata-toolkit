# -*- coding: utf-8 -*-
"""
Evidence Screenshot Viewer for Versa SD-WAN Router Health Auditor.
Provides an interactive in-app modal gallery to view diagnostic captures
(Interfaces, BGP, VRRP, SD-WAN NAT, and Rapid Pings) with zoom, full preview,
and navigation without having to open Windows File Explorer.
"""

import os
import glob
import tkinter as tk
from tkinter import ttk, messagebox
from PIL import Image, ImageTk

class EvidenceViewerDialog:
    def __init__(self, parent, router_name=None, screenshot_dir=None, initial_image=None):
        self.parent = parent
        self.router_name = router_name or "Unknown Router"
        self.screenshot_dir = screenshot_dir or os.path.join(os.getcwd(), "screenshots", self.router_name)
        self.initial_image = initial_image
        
        self.image_files = []
        self._load_image_files()
        
        self.current_idx = 0
        if self.initial_image and self.initial_image in self.image_files:
            self.current_idx = self.image_files.index(self.initial_image)

        self.zoom_factor = 1.0
        self.original_image = None
        self.tk_image = None

        self._build_ui()
        if self.image_files:
            self._display_image(self.current_idx)

    def _load_image_files(self):
        if os.path.isdir(self.screenshot_dir):
            patterns = ["*.png", "*.jpg", "*.jpeg"]
            files = []
            for pat in patterns:
                files.extend(glob.glob(os.path.join(self.screenshot_dir, pat)))
            self.image_files = sorted(files)
        elif self.initial_image and os.path.isfile(self.initial_image):
            self.image_files = [self.initial_image]
            self.screenshot_dir = os.path.dirname(self.initial_image)
        else:
            self.image_files = []

    def _build_ui(self):
        self.win = tk.Toplevel(self.parent)
        self.win.title(f"📷 Evidence Captures - {self.router_name}")
        self.win.geometry("1100x750")
        self.win.minsize(850, 550)
        self.win.transient(self.parent)
        self.win.grab_set()

        # Center window
        try:
            self.parent.update_idletasks()
            x = self.parent.winfo_x() + (self.parent.winfo_width() - 1100) // 2
            y = self.parent.winfo_y() + (self.parent.winfo_height() - 750) // 2
            self.win.geometry(f"+{max(0, x)}+{max(0, y)}")
        except Exception:
            pass

        # Top Bar
        top_bar = ttk.Frame(self.win, padding=(14, 10))
        top_bar.pack(fill="x", side="top")

        title_lbl = ttk.Label(
            top_bar,
            text=f"Diagnostic Evidence Captures: {self.router_name}",
            font=("Segoe UI", 12, "bold")
        )
        title_lbl.pack(side="left")

        count_text = f"({len(self.image_files)} screenshots captured)" if self.image_files else "(No screenshots found)"
        count_lbl = ttk.Label(top_bar, text=count_text, font=("Segoe UI", 9))
        count_lbl.pack(side="left", padx=(10, 0))

        open_folder_btn = ttk.Button(
            top_bar,
            text="📂 Open Folder",
            command=self._open_folder
        )
        open_folder_btn.pack(side="right", padx=(6, 0))

        # Main Paned Window: Left Thumbnails/List, Right Full Preview
        paned = ttk.PanedWindow(self.win, orient="horizontal")
        paned.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        # Left Panel (List of Captures)
        left_frame = ttk.Frame(paned, width=320, padding=6)
        paned.add(left_frame, weight=1)

        ttk.Label(left_frame, text="Diagnostic Steps", font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(0, 4))

        list_container = ttk.Frame(left_frame)
        list_container.pack(fill="both", expand=True)

        self.listbox = tk.Listbox(
            list_container,
            font=("Segoe UI", 9),
            selectmode="browse",
            activestyle="none",
            highlightthickness=1,
            bd=0
        )
        sb_y = ttk.Scrollbar(list_container, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=sb_y.set)
        self.listbox.pack(side="left", fill="both", expand=True)
        sb_y.pack(side="right", fill="y")

        self.listbox.bind("<<ListboxSelect>>", self._on_list_select)

        # Populate Listbox
        for img_p in self.image_files:
            fname = os.path.basename(img_p)
            clean_name = fname.replace(".png", "").replace(".jpg", "")
            # Format nicely
            self.listbox.insert("end", f"  {clean_name}")

        if self.image_files:
            self.listbox.select_set(self.current_idx)

        # Right Panel (Image Preview & Controls)
        right_frame = ttk.Frame(paned, padding=6)
        paned.add(right_frame, weight=4)

        # Viewer Control Toolbar
        ctrl_bar = ttk.Frame(right_frame)
        ctrl_bar.pack(fill="x", pady=(0, 6))

        self.prev_btn = ttk.Button(ctrl_bar, text="◀ Previous", command=self._prev_image)
        self.prev_btn.pack(side="left", padx=(0, 4))

        self.next_btn = ttk.Button(ctrl_bar, text="Next ▶", command=self._next_image)
        self.next_btn.pack(side="left", padx=(0, 14))

        self.info_lbl = ttk.Label(ctrl_bar, text="", font=("Segoe UI", 9, "bold"))
        self.info_lbl.pack(side="left", padx=4)

        zoom_out_btn = ttk.Button(ctrl_bar, text="🔍- Zoom Out", command=lambda: self._adjust_zoom(0.85))
        zoom_out_btn.pack(side="right", padx=2)

        zoom_in_btn = ttk.Button(ctrl_bar, text="🔍+ Zoom In", command=lambda: self._adjust_zoom(1.15))
        zoom_in_btn.pack(side="right", padx=2)

        fit_btn = ttk.Button(ctrl_bar, text="Fit View", command=self._fit_to_window)
        fit_btn.pack(side="right", padx=2)

        # Canvas with scrollbars for image view
        canvas_frame = ttk.Frame(right_frame)
        canvas_frame.pack(fill="both", expand=True)

        self.canvas = tk.Canvas(canvas_frame, bg="#1e293b", highlightthickness=0)
        c_scroll_y = ttk.Scrollbar(canvas_frame, orient="vertical", command=self.canvas.yview)
        c_scroll_x = ttk.Scrollbar(canvas_frame, orient="horizontal", command=self.canvas.xview)

        self.canvas.configure(xscrollcommand=c_scroll_x.set, yscrollcommand=c_scroll_y.set)

        self.canvas.grid(row=0, column=0, sticky="nsew")
        c_scroll_y.grid(row=0, column=1, sticky="ns")
        c_scroll_x.grid(row=1, column=0, sticky="ew")

        canvas_frame.grid_rowconfigure(0, weight=1)
        canvas_frame.grid_columnconfigure(0, weight=1)

        # Bind resize & keyboard shortcuts
        self.canvas.bind("<Configure>", self._on_canvas_resize)
        self.win.bind("<Left>", lambda e: self._prev_image())
        self.win.bind("<Right>", lambda e: self._next_image())
        self.win.bind("<Escape>", lambda e: self.win.destroy())

        if not self.image_files:
            self.canvas.create_text(
                350, 200,
                text=f"No screenshots found in directory:\n{self.screenshot_dir}\n\nRun the audit with terminal captures enabled.",
                fill="#94a3b8",
                font=("Segoe UI", 11),
                justify="center"
            )

    def _open_folder(self):
        if os.path.isdir(self.screenshot_dir):
            try:
                os.startfile(self.screenshot_dir)
            except Exception as e:
                messagebox.showerror("Error", f"Could not open directory: {e}")
        else:
            messagebox.showinfo("Not Found", f"Directory does not exist yet:\n{self.screenshot_dir}")

    def _on_list_select(self, event):
        sel = self.listbox.curselection()
        if sel:
            self._display_image(sel[0])

    def _on_canvas_resize(self, event):
        if self.original_image and self.zoom_factor == 1.0:
            self._render_current_image()

    def _prev_image(self):
        if not self.image_files:
            return
        if self.current_idx > 0:
            self._display_image(self.current_idx - 1)

    def _next_image(self):
        if not self.image_files:
            return
        if self.current_idx < len(self.image_files) - 1:
            self._display_image(self.current_idx + 1)

    def _adjust_zoom(self, multiplier):
        if not self.original_image:
            return
        new_factor = self.zoom_factor * multiplier
        if 0.2 <= new_factor <= 4.0:
            self.zoom_factor = new_factor
            self._render_current_image()

    def _fit_to_window(self):
        self.zoom_factor = 1.0
        self._render_current_image()

    def _display_image(self, index):
        if 0 <= index < len(self.image_files):
            self.current_idx = index
            self.listbox.selection_clear(0, "end")
            self.listbox.selection_set(index)
            self.listbox.see(index)

            # Update buttons
            self.prev_btn.state(["!disabled"] if index > 0 else ["disabled"])
            self.next_btn.state(["!disabled"] if index < len(self.image_files) - 1 else ["disabled"])

            img_path = self.image_files[index]
            fname = os.path.basename(img_path)
            self.info_lbl.config(text=f"[{index + 1}/{len(self.image_files)}] {fname}")

            try:
                self.original_image = Image.open(img_path)
                self.zoom_factor = 1.0
                self._render_current_image()
            except Exception as e:
                self.canvas.delete("all")
                self.canvas.create_text(
                    250, 150,
                    text=f"Failed to load image: {fname}\n{e}",
                    fill="#ef4444",
                    font=("Segoe UI", 10)
                )

    def _render_current_image(self):
        if not self.original_image:
            return

        c_w = self.canvas.winfo_width()
        c_h = self.canvas.winfo_height()
        if c_w < 50:
            c_w = 700
        if c_h < 50:
            c_h = 500

        orig_w, orig_h = self.original_image.size

        # If fit-to-window (zoom_factor == 1.0), calculate scale to fit canvas
        if self.zoom_factor == 1.0:
            scale_w = (c_w - 20) / orig_w
            scale_h = (c_h - 20) / orig_h
            scale = min(scale_w, scale_h, 1.0)
            target_w = max(10, int(orig_w * scale))
            target_h = max(10, int(orig_h * scale))
        else:
            target_w = max(10, int(orig_w * self.zoom_factor))
            target_h = max(10, int(orig_h * self.zoom_factor))

        resized = self.original_image.resize((target_w, target_h), Image.Resampling.LANCZOS)
        self.tk_image = ImageTk.PhotoImage(resized)

        self.canvas.delete("all")
        # Center image on canvas if smaller than canvas
        x = max(c_w // 2, target_w // 2)
        y = max(c_h // 2, target_h // 2)

        self.canvas.create_image(x, y, image=self.tk_image, anchor="center")
        self.canvas.configure(scrollregion=(0, 0, max(c_w, target_w), max(c_h, target_h)))


def open_evidence_viewer(parent, router_name=None, screenshot_path=None):
    """Entry point to launch the evidence viewer."""
    return EvidenceViewerDialog(parent, router_name=router_name, initial_image=screenshot_path)
