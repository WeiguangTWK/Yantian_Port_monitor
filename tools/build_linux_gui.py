"""在 Linux 目标机上构建 PySide6 GUI onedir；不影响 Win7 打包路线。"""

from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import os
import pathlib
import platform
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENTRY = ROOT / 'gui' / 'app.py'
ASSETS = ('home.svg', 'ship.svg', 'monitor.svg', 'notify.svg',
          'about.svg', 'newguilun_logo.jpg')
METADATA = ('PySide6', 'PySide6-Fluent-Widgets')
CONTENTS = '_internal'


def check_environment() -> list[str]:
    problems = []
    if not sys.platform.startswith('linux'):
        problems.append('此脚本只能在 Linux 目标机上运行；Windows 请使用 build_gui.py。')
    for module in ('PyInstaller', 'PySide6.QtCore', 'PySide6.QtSvg',
                   'qfluentwidgets', 'aiohttp', 'requests'):
        try:
            importlib.import_module(module)
        except (ImportError, OSError) as error:
            problems.append('%s 无法导入：%s' % (module, error))
    for package in METADATA:
        try:
            importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            problems.append('缺少发行包元数据：%s' % package)
    for command in ('ldd', 'objdump', 'objcopy'):
        if not shutil.which(command):
            problems.append('缺少构建工具：%s' % command)
    for relative in ('gui/app.py', 'LICENSE', 'licenses/LGPL-3.0.txt'):
        if not (ROOT / relative).is_file():
            problems.append('缺少文件：%s' % relative)
    for name in ASSETS:
        if not (ROOT / 'gui' / 'assets' / name).is_file():
            problems.append('缺少图标资源：%s' % name)
    return problems


def prepare_version_file() -> pathlib.Path:
    sys.path.insert(0, str(ROOT))
    from ytmon.version import repo_commit

    commit = repo_commit(ROOT)
    if commit is None:
        raise RuntimeError('无法读取仓库 HEAD；请在含 Git 历史的源码仓库中构建。')
    path = ROOT / '.toolchain' / 'gui-version.txt'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(commit + '\n', encoding='ascii')
    return path


def build_command(version_file: pathlib.Path, dist_root: pathlib.Path,
                  name: str = 'ytmon-gui') -> list[str]:
    work = ROOT / '.toolchain' / 'pyi-linux-build'
    spec = ROOT / '.toolchain' / 'pyi-linux-spec'
    command = [
        sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
        '--onedir', '--console', '--name', name,
        '--contents-directory', CONTENTS,
        '--paths', str(ROOT), '--paths', str(ROOT / 'gui'),
        '--workpath', str(work), '--specpath', str(spec),
        '--distpath', str(dist_root),
    ]
    for source, destination in (
            (ROOT / 'gui' / 'assets', 'gui/assets'),
            (ROOT / 'LICENSE', '.'),
            (ROOT / 'licenses', 'licenses'),
            (version_file, '.')):
        command += ['--add-data', str(source) + os.pathsep + destination]
    for package in METADATA:
        command += ['--copy-metadata', package]
    for module in ('PySide2', 'PyQt5', 'PyQt6'):
        command += ['--exclude-module', module]
    command += ['--collect-all', 'qfluentwidgets',
                '--collect-submodules', 'ytmon',
                '--hidden-import', 'PySide6.QtSvg', str(ENTRY)]
    return command


def verify_output(output: pathlib.Path) -> None:
    runtime = output / CONTENTS
    required = [runtime / 'gui-version.txt', runtime / 'LICENSE',
                runtime / 'licenses' / 'LGPL-3.0.txt']
    required += [runtime / 'gui' / 'assets' / name for name in ASSETS]
    missing = [str(path.relative_to(output)) for path in required if not path.is_file()]
    for package in METADATA:
        prefix = package.lower().replace('-', '_') + '_'
        if not any(path.name.lower().replace('-', '_').startswith(prefix)
                   for path in runtime.glob('*.dist-info')):
            missing.append('metadata: ' + package)
    for plugin in ('libqsvgicon.so', 'libqjpeg.so'):
        if not any(runtime.rglob(plugin)):
            missing.append('Qt plugin: ' + plugin)
    if not any(runtime.rglob('libqxcb.so')) and not any(runtime.rglob('libqwayland*.so')):
        missing.append('Qt platform plugin: xcb/wayland')
    if missing:
        raise RuntimeError('Linux GUI 产物缺少：' + '；'.join(missing))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='在 Linux 本机打包 PySide6 GUI')
    parser.add_argument('--name', default='ytmon-gui')
    parser.add_argument('--dry-run', action='store_true', help='仅打印构建命令，不写文件')
    args = parser.parse_args(argv)
    problems = check_environment()
    if problems:
        for problem in problems:
            print('[build_linux_gui] ' + problem, file=sys.stderr)
        return 2

    dist_root = ROOT / 'dist' / 'linux'
    version_file = ROOT / '.toolchain' / 'gui-version.txt'
    if not args.dry_run:
        version_file = prepare_version_file()
        (ROOT / '.toolchain' / 'pyi-linux-spec').mkdir(parents=True, exist_ok=True)
    command = build_command(version_file, dist_root, args.name)
    if args.dry_run:
        import shlex
        print(shlex.join(command))
        return 0
    print('[build_linux_gui] %s / %s' % (platform.system(), platform.machine()))
    result = subprocess.run(command, cwd=str(ROOT))
    if result.returncode:
        return result.returncode
    output = dist_root / args.name
    verify_output(output)
    template = ROOT / 'watchlist.example.json'
    if template.is_file():
        shutil.copy2(str(template), str(output / template.name))
    print('[build_linux_gui] 完成：%s' % output)
    print('[build_linux_gui] 请复制整个目录，并在本机验证 GUI、浏览器和通知。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
