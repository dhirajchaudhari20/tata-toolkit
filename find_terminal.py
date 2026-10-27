import ctypes

user32 = ctypes.windll.user32

def enum_windows_callback(hwnd, extra):
    length = user32.GetWindowTextLengthW(hwnd)
    if length > 0:
        buff = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buff, length + 1)
        title = buff.value.strip()
        if title:
            print(f"HWND={hwnd} | Visible={bool(user32.IsWindowVisible(hwnd))} | Title='{title}'")
    return True

WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)
user32.EnumWindows(WNDENUMPROC(enum_windows_callback), 0)
