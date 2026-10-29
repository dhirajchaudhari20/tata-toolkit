"""
====================================================================================================
                        TATA COMMUNICATIONS - NETWORK OPERATIONS CENTER
====================================================================================================
Module: sleep_preventer.py
Purpose: Prevents Windows system sleep, screen timeout, and corporate GPO lock during audits.

Technique:
1. Win32 API SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED)
   - Prevents OS sleep, hibernate, and display blanking.
2. Background Heartbeat Thread (every 25 seconds)
   - Continuously refreshes the execution state to override aggressive laptop battery timers.
3. Micro-Jiggle Input Pulse
   - Sends a net-zero mouse event (dx=+1, then dx=-1) to reset Windows GetLastInputInfo().
   - Defeats corporate GPO inactivity locks and screensavers that ignore SetThreadExecutionState.
====================================================================================================
"""

import sys
import time
import threading
import ctypes

# Windows Execution State Flags
ES_CONTINUOUS        = 0x80000000
ES_SYSTEM_REQUIRED   = 0x00000001
ES_DISPLAY_REQUIRED  = 0x00000002
ES_AWAYMODE_REQUIRED = 0x00000040

# Windows Mouse Event Flags
MOUSEEVENTF_MOVE = 0x0001


class SleepPreventer:
    """
    Manages Windows system execution state and keep-awake pulses.
    Thread-safe singleton.
    """
    _instance = None
    _lock = threading.Lock()

    def __init__(self):
        self._active = False
        self._thread = None
        self._stop_event = threading.Event()
        self._callbacks = []

    @classmethod
    def get_instance(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def is_active(self):
        return self._active

    def register_callback(self, cb):
        """Register a callback function cb(is_active: bool) to update UI controls."""
        if cb not in self._callbacks:
            self._callbacks.append(cb)

    def _notify(self, state):
        for cb in list(self._callbacks):
            try:
                cb(state)
            except Exception:
                pass

    def start(self, reason="Network Audit in progress"):
        """Activate sleep prevention and start background heartbeat."""
        with self._lock:
            if self._active:
                return
            self._active = True
            self._stop_event.clear()

            # Primary execution state call
            self._set_state(ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED)

            # Start heartbeat loop
            self._thread = threading.Thread(target=self._heartbeat_loop, name="SleepPreventerHeartbeat", daemon=True)
            self._thread.start()

            self._notify(True)

    def stop(self):
        """Deactivate sleep prevention and restore default system power settings."""
        with self._lock:
            if not self._active:
                return
            self._active = False
            self._stop_event.set()

            # Restore normal execution state
            self._set_state(ES_CONTINUOUS)

            self._notify(False)

    def toggle(self):
        """Toggle active state manually."""
        if self._active:
            self.stop()
        else:
            self.start("Manual toggle")
        return self._active

    def _set_state(self, flags):
        if sys.platform != "win32":
            return
        try:
            ctypes.windll.kernel32.SetThreadExecutionState(flags)
        except Exception:
            pass

    def _jiggle_mouse(self):
        """Imperceptible 1-pixel jiggle to reset GPO screensaver/lock timer."""
        if sys.platform != "win32":
            return
        try:
            # Move +1 then -1: net cursor position does NOT change
            ctypes.windll.user32.mouse_event(MOUSEEVENTF_MOVE, 1, 0, 0, 0)
            ctypes.windll.user32.mouse_event(MOUSEEVENTF_MOVE, -1, 0, 0, 0)
        except Exception:
            pass

    def _heartbeat_loop(self):
        """Runs in background while active, refreshing power states every 25 seconds."""
        while not self._stop_event.is_set():
            # Refresh Windows execution state
            self._set_state(ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED)

            # Reset Windows idle timer for corporate GPO
            self._jiggle_mouse()

            # Sleep in short increments to allow rapid shutdown
            if self._stop_event.wait(timeout=25.0):
                break


# Global singleton instance
sleep_preventer = SleepPreventer.get_instance()
