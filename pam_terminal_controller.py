"""
====================================================================================================
                        TATA COMMUNICATIONS - NETWORK OPERATIONS CENTER
                    ARCON PAM TERMINAL CONTROLLER & AUTOMATION ENGINE
====================================================================================================
Designed and Developed by Tata Communications
Clean keystroke and clipboard automation without any stray characters.
Thread-safe: multiple PuTTY sessions can run in parallel without collisions.
====================================================================================================
"""

import time
import re
import sys
import os
import threading
import pyperclip

try:
    import win32gui
    import win32con
    import win32api
    import win32process
    import ctypes
    import pyautogui
    pyautogui.FAILSAFE = False
    pyautogui.PAUSE = 0.04
    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False


class PamTerminalController:
    """
    Finds and automates the open ARCON PAM Terminal / PuTTY session.

    PARALLEL-SAFE DESIGN:
    ─────────────────────
    Each PamTerminalController instance gets its OWN instance-level lock
    (self._instance_lock) so that parallel workers on DIFFERENT PuTTY windows
    never block each other.

    Commands are sent directly via WM_CHAR messages to the target window's
    message queue — no clipboard, no focus stealing, no cross-worker interference.
    This means Worker-1 and Worker-2 can both type into their own PuTTY windows
    simultaneously without either one interfering with the other.

    The old class-level desktop_lock is kept only for operations that truly
    require exclusive desktop access (screenshots, window activation).
    """
    # Class-level lock ONLY for screenshot/activation operations that need
    # exclusive desktop focus (SetForegroundWindow). NOT used for send_command.
    desktop_lock = threading.Lock()

    # Broad keywords so any PuTTY window (PAM or direct) is found
    DEFAULT_KEYWORDS = ["mcpeuser1", "MCPESGW01", "100.76.198.218", "PuTTY", "putty"]

    def __init__(self, target_title_keywords=None, log_callback=None,
                 specific_hwnd=None, specific_title=None, worker_id=1, run_background=False):
        self.target_keywords = target_title_keywords or self.DEFAULT_KEYWORDS
        self.hwnd = specific_hwnd
        self.window_title = specific_title
        self.log_callback = log_callback
        self.worker_id = worker_id
        # run_background kept as arg for backward-compat but no longer used for hiding
        self.run_background = run_background
        # Per-instance lock: only serialises commands to THIS controller's own window.
        # Workers on different windows never block each other.
        self._instance_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def log(self, msg):
        if self.log_callback:
            self.log_callback(msg)
        else:
            print(msg)

    def _match_keywords(self, title):
        t = title.lower()
        return any(kw.lower() in t for kw in self.target_keywords)

    # ------------------------------------------------------------------
    # Window discovery
    # ------------------------------------------------------------------

    @classmethod
    def find_all_terminals(cls, target_keywords=None):
        """
        Returns a list of ALL (hwnd, title) matching terminal windows —
        visible, minimized, or off-screen — as long as they are real windows.
        """
        if not HAS_WIN32:
            return []
        keywords = target_keywords or cls.DEFAULT_KEYWORDS
        matching = []

        def enum_cb(hwnd, _):
            if not win32gui.IsWindow(hwnd):
                return True
            title = win32gui.GetWindowText(hwnd)
            if not title:
                return True
            tl = title.lower()
            if any(kw.lower() in tl for kw in keywords):
                matching.append((hwnd, title))
            return True

        win32gui.EnumWindows(enum_cb, None)
        # Deduplicate by hwnd
        seen = set()
        result = []
        for h, t in matching:
            if h not in seen:
                seen.add(h)
                result.append((h, t))
        return result

    def find_terminal_window(self):
        """
        Finds the window handle for ARCON PuTTY / SSH Terminal.
        Accepts visible, minimized, AND recently-moved windows.
        """
        if not HAS_WIN32:
            raise RuntimeError("pywin32 and pyautogui are required.")

        # If we already have a valid hwnd, return it directly
        if self.hwnd and win32gui.IsWindow(self.hwnd):
            return self.hwnd, self.window_title

        all_matches = self.find_all_terminals(self.target_keywords)
        if not all_matches:
            return None, None

        # Prefer a visible (non-minimized) window
        for h, t in all_matches:
            if win32gui.IsWindowVisible(h) and not win32gui.IsIconic(h):
                self.hwnd = h
                self.window_title = t
                return h, t

        # Accept minimized window
        for h, t in all_matches:
            if win32gui.IsWindow(h):
                self.hwnd = h
                self.window_title = t
                return h, t

        return None, None

    # ------------------------------------------------------------------
    # Window activation (robust, never raises)
    # ------------------------------------------------------------------

    def activate_window(self):
        """
        Brings the PuTTY window to focus so keystrokes are received.
        - If window is minimized: restores it first.
        - Uses multiple fallback techniques to set foreground focus.
        - NEVER raises RuntimeError — logs a warning and returns False on failure.
        """
        if not HAS_WIN32:
            return False

        # Try to refresh hwnd if it seems stale
        if not self.hwnd or not win32gui.IsWindow(self.hwnd):
            h, t = self.find_terminal_window()
            if not h:
                self.log("[!] WARNING: Cannot find PuTTY/PAM terminal window. "
                         "Is PuTTY open and logged into PAM?\n")
                return False
            self.hwnd = h
            self.window_title = t

        try:
            # Restore if minimized so keystrokes are received
            if win32gui.IsIconic(self.hwnd):
                win32gui.ShowWindow(self.hwnd, win32con.SW_RESTORE)
                time.sleep(0.15)

            # Attempt 1: standard SetForegroundWindow
            try:
                win32gui.SetForegroundWindow(self.hwnd)
                time.sleep(0.10)
                return True
            except Exception:
                pass

            # Attempt 2: BringWindowToTop + SetForegroundWindow
            try:
                win32gui.BringWindowToTop(self.hwnd)
                win32gui.SetForegroundWindow(self.hwnd)
                time.sleep(0.10)
                return True
            except Exception:
                pass

            # Attempt 3: Simulate Alt key press (Windows foreground lock bypass)
            try:
                win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
                time.sleep(0.05)
                win32gui.SetForegroundWindow(self.hwnd)
                win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
                time.sleep(0.10)
                return True
            except Exception:
                pass

            # Attempt 4: SendMessage WM_ACTIVATE as last resort
            try:
                win32gui.SendMessage(self.hwnd, win32con.WM_ACTIVATE,
                                     win32con.WA_CLICKACTIVE, 0)
                time.sleep(0.10)
                return True
            except Exception:
                pass

            self.log("[!] Warning: Could not bring PuTTY to foreground (Windows focus rules). "
                     "Keystrokes may still be delivered via WM_CHAR.\n")
            return False

        except Exception as e:
            self.log(f"[!] Warning in activate_window: {e}\n")
            return False

    # ------------------------------------------------------------------
    # Keyboard helpers
    # ------------------------------------------------------------------

    def clear_line(self):
        """Safe line clear: ensures window is active."""
        with PamTerminalController.desktop_lock:
            self.activate_window()
            time.sleep(0.10)

    def _send_enter_raw(self):
        """Legacy helper kept for compatibility — use send_enter() instead."""
        try:
            if HAS_WIN32 and self.hwnd and win32gui.IsWindow(self.hwnd):
                win32api.SendMessage(self.hwnd, 0x0102, ord('\r'), 0)
            else:
                pyautogui.press('enter')
        except Exception:
            try:
                pyautogui.press('enter')
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Main command sender
    # ------------------------------------------------------------------

    def send_command(self, cmd, wait_after=1.0, is_password=False):
        """
        Sends a command into THIS controller's PuTTY window via WM_CHAR messages.

        WHY WM_CHAR instead of clipboard + Shift+Insert:
        ──────────────────────────────────────────────────
        • Clipboard (pyperclip) is a GLOBAL resource — two parallel workers
          both copying different commands clobber each other, causing the wrong
          command to be pasted into the wrong terminal.
        • WM_CHAR posts characters directly into the target window's message
          queue identified by its HWND.  The window does not need to be in the
          foreground, no focus steal occurs, and two workers on different HWNDs
          run simultaneously without any interference.

        Uses self._instance_lock (per-instance) so only one send_command at a
        time runs against THIS window, but other workers' windows are unaffected.
        """
        with self._instance_lock:
            if not HAS_WIN32:
                return

            # Refresh hwnd if stale
            if not self.hwnd or not win32gui.IsWindow(self.hwnd):
                h, t = self.find_terminal_window()
                if not h:
                    self.log(f"[!] WARNING: Cannot find PuTTY window for worker {self.worker_id}.\n")
                    return

            cmd_clean = cmd.strip()

            # Post each character as a WM_CHAR message directly to the window
            WM_CHAR = 0x0102
            for ch in cmd_clean:
                try:
                    win32api.SendMessage(self.hwnd, WM_CHAR, ord(ch), 0)
                    time.sleep(0.004)   # 4ms between chars — fast but reliable
                except Exception:
                    pass

            # Send carriage return (Enter)
            try:
                win32api.SendMessage(self.hwnd, WM_CHAR, ord('\r'), 0)
            except Exception:
                pass

            if is_password:
                self.log("[PASSWORD ENTERED]\n")
            else:
                self.log(f">>> {cmd}\n")

        time.sleep(wait_after)

    def send_enter(self, wait_after=1.0):
        """Sends Enter directly via WM_CHAR — no focus, no clipboard."""
        with self._instance_lock:
            if HAS_WIN32 and self.hwnd and win32gui.IsWindow(self.hwnd):
                try:
                    win32api.SendMessage(self.hwnd, 0x0102, ord('\r'), 0)
                except Exception:
                    pass
        time.sleep(wait_after)

    def break_pager(self):
        """Sends Ctrl+C directly via WM_CHAR to break any pending pager output."""
        with self._instance_lock:
            if HAS_WIN32 and self.hwnd and win32gui.IsWindow(self.hwnd):
                try:
                    # Ctrl+C = ASCII 0x03
                    win32api.SendMessage(self.hwnd, 0x0102, 0x03, 0)
                    time.sleep(0.1)
                    win32api.SendMessage(self.hwnd, 0x0102, ord('\r'), 0)
                except Exception:
                    pass
        time.sleep(0.3)

    # ------------------------------------------------------------------
    # Screenshot
    # ------------------------------------------------------------------

    def capture_screenshot(self, filepath):
        """
        Captures a screenshot of the terminal window.
        - If visible: captures the actual screen region.
        - If minimized: renders a clean image from the terminal buffer (PIL).
        """
        with PamTerminalController.desktop_lock:
            try:
                import os as _os
                _os.makedirs(_os.path.dirname(_os.path.abspath(filepath)), exist_ok=True)

                if not self.hwnd or not win32gui.IsWindow(self.hwnd):
                    self.find_terminal_window()

                is_iconic = win32gui.IsIconic(self.hwnd) if self.hwnd else True
                if is_iconic:
                    # Render from buffer for minimized window
                    out = self.copy_terminal_output()
                    return self.render_terminal_screenshot(out, filepath)

                rect = win32gui.GetWindowRect(self.hwnd)
                x, y, w, h = rect[0], rect[1], rect[2] - rect[0], rect[3] - rect[1]

                if w > 100 and h > 100 and x >= -50 and y >= -50:
                    self.activate_window()
                    time.sleep(0.25)
                    im = pyautogui.screenshot(region=(max(0, x), max(0, y), w, h))
                    im.save(filepath)
                    self.log(f"[📸 Screenshot Saved]: {filepath}\n")
                    return filepath
                else:
                    out = self.copy_terminal_output()
                    return self.render_terminal_screenshot(out, filepath)

            except Exception:
                out = self.copy_terminal_output()
                return self.render_terminal_screenshot(out, filepath)

    def render_terminal_screenshot(self, terminal_text, filepath):
        """
        Renders a clean dark-theme PuTTY-style terminal image from buffer text.
        Used as fallback when the window is minimized or cannot be captured.
        """
        try:
            from PIL import Image, ImageDraw
            lines = (terminal_text or "").strip().splitlines()
            if len(lines) > 40:
                lines = lines[-40:]
            if not lines:
                lines = ["[PuTTY Terminal Buffer]"]

            char_w, line_h = 9, 18
            pad_x, pad_y = 14, 34
            max_len = max((len(l) for l in lines), default=40)
            img_w = max(860, pad_x * 2 + max_len * char_w)
            img_h = pad_y + len(lines) * line_h + 18

            img = Image.new("RGB", (img_w, img_h), color="#1e1e1e")
            draw = ImageDraw.Draw(img)

            # Title bar
            draw.rectangle([(0, 0), (img_w, 26)], fill="#2d2d2d")
            title_text = self.window_title or "PuTTY - ARCON PAM Terminal"
            draw.text((10, 5), title_text, fill="#cccccc")

            # Terminal text lines
            y_pos = pad_y
            for line in lines:
                if "-cli>" in line or "[ok]" in line:
                    col = "#22c55e"       # green prompt
                elif any(k in line for k in ["show ", "ping ", "ssh ", "set "]):
                    col = "#60a5fa"       # blue commands
                elif "loss" in line.lower() or "FAIL" in line:
                    col = "#f87171"       # red warnings
                elif "%" in line and "loss" in line.lower():
                    col = "#fb923c"       # orange packet loss
                else:
                    col = "#e2e8f0"       # default white/silver
                draw.text((pad_x, y_pos), line, fill=col)
                y_pos += line_h

            img.save(filepath)
            self.log(f"[📸 Screenshot Rendered]: {filepath}\n")
            return filepath
        except Exception as ex:
            self.log(f"[!] Warning rendering screenshot: {ex}\n")
            return None

    # ------------------------------------------------------------------
    # Terminal output reading
    # ------------------------------------------------------------------

    def copy_terminal_output(self):
        """
        Copies the PuTTY terminal buffer via IDM_COPYALL and reads the clipboard.

        PARALLEL NOTE: Reading the clipboard is a shared-resource operation.
        We use the class-level desktop_lock here so that two workers don't
        both trigger IDM_COPYALL and read each other's clipboard content.
        Each call is short (< 0.5 s) so this doesn't cause meaningful delays.
        """
        with PamTerminalController.desktop_lock:
            if not self.hwnd or not win32gui.IsWindow(self.hwnd):
                self.find_terminal_window()
            if not self.hwnd:
                return ""
            try:
                # PuTTY IDM_COPYALL: puts entire scroll-back buffer into clipboard
                win32gui.SendMessage(self.hwnd, win32con.WM_SYSCOMMAND, 0x0170, 0)
                time.sleep(0.22)
                text = pyperclip.paste()
                if text and len(text.strip()) > 10:
                    return text
            except Exception:
                pass

            # Fallback: Alt+Space → o
            try:
                self.activate_window()
                time.sleep(0.15)
                pyautogui.hotkey('alt', 'space')
                time.sleep(0.15)
                pyautogui.press('o')
                time.sleep(0.22)
                return pyperclip.paste() or ""
            except Exception as e:
                self.log(f"[!] Warning copying terminal buffer: {e}\n")
                return ""

    # ------------------------------------------------------------------
    # Compat aliases
    # ------------------------------------------------------------------

    def send_line(self, text, wait_after=1.0):
        self.send_command(text, wait_after=wait_after)

    def restore_onscreen(self):
        """No-op: kept for backward compatibility."""
        pass

    def hide_offscreen(self):
        """No-op: off-screen hiding removed. PuTTY runs normally on screen."""
        pass
