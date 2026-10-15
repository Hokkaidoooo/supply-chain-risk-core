"""命令行的 UTF-8 输出约定；导入本模块不改变宿主进程。"""
from io import TextIOWrapper
import sys


def configure_utf8_output():
    """仅配置带字节编码的标准流；调用方提供的内存文本流无需编码。"""
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, TextIOWrapper):
            stream.reconfigure(encoding='utf-8', errors='strict')
