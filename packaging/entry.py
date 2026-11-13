"""Точка входа для сборки в один файл (PyInstaller).

Отдельный модуль нужен потому, что `cli.py` использует относительные импорты
и не может быть запущен как самостоятельный скрипт.
"""

import sys

from polytopia_analyzer.cli import main

if __name__ == "__main__":
    sys.exit(main())