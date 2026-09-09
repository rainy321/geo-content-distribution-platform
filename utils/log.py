import sys
from pathlib import Path
from loguru import logger

from conf import LOGS_DIRECTORY


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def log_formatter(record: dict) -> str:
    """
    Formatter for log records.
    :param dict record: Log object containing log metadata & message.
    :returns: str
    """
    colors = {
        "TRACE": "#cfe2f3",
        "INFO": "#9cbfdd",
        "DEBUG": "#8598ea",
        "WARNING": "#dcad5a",
        "SUCCESS": "#3dd08d",
        "ERROR": "#ae2c2c"
    }
    color = colors.get(record["level"].name, "#b3cfe7")
    return f"<fg #70acde>{{time:YYYY-MM-DD HH:mm:ss}}</fg #70acde> | <fg {color}>{{level}}</fg {color}>: <light-white>{{message}}</light-white>\n"


def create_logger(log_name: str, file_path: str):
    """
    Create custom logger for different business modules.
    :param str log_name: name of log
    :param str file_path: Optional path to log file
    :returns: Configured logger
    """
    def filter_record(record):
        return record["extra"].get("business_name") == log_name

    destination = LOGS_DIRECTORY / Path(file_path).name
    destination.parent.mkdir(parents=True, exist_ok=True)
    logger.add(
        destination,
        filter=filter_record,
        level="INFO",
        rotation="10 MB",
        retention="10 days",
        backtrace=True,
        # Publisher/login frames may hold account labels, cookie paths or
        # article text.  Keep the traceback, but never render local variable
        # values into production log files.
        diagnose=False,
        encoding="utf-8",
    )
    return logger.bind(business_name=log_name)


# Remove all existing handlers
logger.remove()
# A PyInstaller windowed executable intentionally has no stdout.  File sinks
# remain available, while source/console runs keep the colored sink.
if sys.stdout is not None and hasattr(sys.stdout, "write"):
    logger.add(sys.stdout, colorize=True, format=log_formatter)

douyin_logger = create_logger('douyin', 'logs/douyin.log')
tencent_logger = create_logger('tencent', 'logs/tencent.log')
xhs_logger = create_logger('xhs', 'logs/xhs.log')
tiktok_logger = create_logger('tiktok', 'logs/tiktok.log')
bilibili_logger = create_logger('bilibili', 'logs/bilibili.log')
kuaishou_logger = create_logger('kuaishou', 'logs/kuaishou.log')
baijiahao_logger = create_logger('baijiahao', 'logs/baijiahao.log')
xiaohongshu_logger = create_logger('xiaohongshu', 'logs/xiaohongshu.log')
toutiao_logger = create_logger('toutiao', 'logs/toutiao.log')
sohu_logger = create_logger('sohu', 'logs/sohu.log')
zhihu_logger = create_logger('zhihu', 'logs/zhihu.log')
login_logger = create_logger('login', 'logs/login.log')
