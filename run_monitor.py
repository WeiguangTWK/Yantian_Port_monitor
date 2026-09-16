#!/usr/bin/env python
"""船期监控入口。

    python run_monitor.py                 # 跑一轮
    python run_monitor.py --watch 600     # 每 10 分钟一轮
退出码：0=无变更 10=有变更 1=错误 3=查询失败
"""

import os
import sys

from ytmon.cli import EXIT_ERROR, main
from ytmon.errors import YtmonError


def _fail(msg: str, code: int) -> int:
    print(f"\n[致命] {msg}", file=sys.stderr)
    return code


if __name__ == "__main__":
    try:
        sys.exit(main())
    except YtmonError as e:
        sys.exit(_fail(str(e), EXIT_ERROR))
    except KeyboardInterrupt:
        sys.exit(0)
    except Exception as e:                                    # noqa: BLE001
        # 不让用户面对一个裸 traceback；设 YTMON_TRACEBACK=1 看完整栈
        if os.environ.get("YTMON_TRACEBACK"):
            raise
        sys.exit(_fail(
            f"{type(e).__name__}: {e}\n"
            f"        设 YTMON_TRACEBACK=1 可查看完整调用栈", EXIT_ERROR))
