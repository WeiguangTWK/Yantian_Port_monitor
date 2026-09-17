"""源码读取本地 HEAD；冻结程序读取构建时保存的 commit 标识。"""

import pathlib
import re
import subprocess
import sys

VERSION_FILE = 'gui-version.txt'


def repo_commit(root):
    root = pathlib.Path(root)
    # 不在源码目录中时，不误取某个父目录仓库的版本。
    if not (root / '.git').exists():
        return None
    try:
        result = subprocess.run(
            ['git', '-C', str(root), 'rev-parse', '--verify', 'HEAD'],
            capture_output=True, timeout=5,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except (OSError, subprocess.TimeoutExpired):
        return None
    value = result.stdout.decode('ascii', 'replace').strip()
    if result.returncode == 0 and re.fullmatch(r'[0-9a-fA-F]{40,64}', value):
        return value[:8].lower()
    return None


def display_version():
    if getattr(sys, 'frozen', False):
        try:
            value = (pathlib.Path(sys._MEIPASS) / VERSION_FILE).read_text('ascii').strip()
        except (OSError, UnicodeError):
            return '版本信息不可用'
        return value if re.fullmatch(r'[0-9a-f]{8}', value) else '版本信息不可用'
    return repo_commit(pathlib.Path(__file__).resolve().parent.parent) or '版本信息不可用'
