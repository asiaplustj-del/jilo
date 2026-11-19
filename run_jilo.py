# Точка входа для сборки (PyInstaller) и запуска без установки.
import os
import sys
import traceback


def _log_path():
    return os.path.join(os.path.expanduser("~"), "Jilo.log")


if __name__ == "__main__":
    # без консоли (pythonw / собранная программа) ошибки пишем в ~/Jilo.log
    if sys.stderr is None or sys.stdout is None:
        try:
            f = open(_log_path(), "a", encoding="utf-8", buffering=1)
            sys.stdout = sys.stdout or f
            sys.stderr = sys.stderr or f
        except Exception:
            pass
    try:
        from jilo.app import main
        main()
    except SystemExit:
        raise
    except Exception:
        try:
            with open(_log_path(), "a", encoding="utf-8") as f:
                f.write(traceback.format_exc() + "\n")
        except Exception:
            pass
        raise
