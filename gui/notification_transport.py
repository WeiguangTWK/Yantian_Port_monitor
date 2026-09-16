"""后台线程只投递浮窗消息，控件由 GUI 主线程创建。"""

from ytmon.notify import send_windows


def dispatch_windows(channel, message, emit):
    if channel.persistent:
        emit(message.title, message.text)
    else:
        send_windows(channel, message)
