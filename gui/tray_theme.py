"""仅在 Win10+ 读取系统深浅主题，失败和旧系统均返回未知。"""

import sys


def supports_system_theme():
    if sys.platform != 'win32':
        return False
    try:
        return sys.getwindowsversion().major >= 10
    except (AttributeError, OSError):
        return False


def taskbar_dark():
    """True=深色，False=浅色，None=不可判断；不使用应用主题。"""
    if not supports_system_theme():
        return None
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r'Software\Microsoft\Windows\CurrentVersion\Themes\Personalize') as key:
            value, kind = winreg.QueryValueEx(key, 'SystemUsesLightTheme')
        if kind == winreg.REG_DWORD and value in (0, 1):
            return value == 0
    except (ImportError, OSError, ValueError):
        pass
    return None
