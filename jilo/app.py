# -*- coding: utf-8 -*-
"""Jilo — окно программы (pywebview). Если окно не открывается — интерфейс в браузере."""
import json
import os
import sys
import threading
import time
import webbrowser

from . import server

VIDEO_TYPES = ("Видео (*.mp4;*.mov;*.mxf;*.avi;*.mkv;*.m4v;*.mts;*.m2ts;*.MP4;*.MOV;*.MXF)", "Все файлы (*.*)")


class Api:
    """Методы, доступные из интерфейса: window.pywebview.api.*"""

    # ВАЖНО: окно храним в "приватном" поле (с подчёркиванием) — pywebview не обходит такие поля.
    # Если положить окно в обычный атрибут, pywebview пытается выставить его в JS целиком и окно зависает.
    def __init__(self):
        self._window = None

    def open_file(self):
        import webview
        dlg = getattr(webview, "FileDialog", None)
        kind = dlg.OPEN if dlg else webview.OPEN_DIALOG
        try:
            r = self._window.create_file_dialog(kind, allow_multiple=False, file_types=VIDEO_TYPES)
        except Exception:
            r = self._window.create_file_dialog(kind, allow_multiple=False)
        if not r:
            return None
        return r[0] if isinstance(r, (list, tuple)) else r


def _browser_mode(url):
    print("Jilo работает в браузере: " + url, flush=True)
    webbrowser.open(url)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass


def _bind_drop(window):
    """Перетаскивание файла в окно: pywebview отдаёт полный путь."""
    try:
        from webview.dom import DOMEventHandler
    except Exception:
        return

    def on_drop(e):
        try:
            files = (e.get("dataTransfer") or {}).get("files") or []
            for f in files:
                p = f.get("pywebviewFullPath")
                if p:
                    window.evaluate_js("window.Jilo && Jilo.loadPath(%s)" % json.dumps(p))
                    break
        except Exception:
            pass

    def noop(e):
        pass

    try:
        doc = window.dom.document
        doc.events.dragenter += DOMEventHandler(noop, True, True)
        doc.events.dragstart += DOMEventHandler(noop, True, True)
        doc.events.dragover += DOMEventHandler(noop, True, True, debounce=500)
        doc.events.drop += DOMEventHandler(on_drop, True, True)
    except Exception:
        pass


def main():
    port = int(os.environ.get("JILO_PORT", "0") or 0) or None
    httpd, url = server.start(port)
    if "--browser" in sys.argv:
        return _browser_mode(url)
    try:
        import webview
    except Exception:
        return _browser_mode(url)
    api = Api()
    try:
        w, h = 1440, 900
        try:
            sc = webview.screens[0]
            w, h = min(w, int(sc.width) - 40), min(h, int(sc.height) - 80)
        except Exception:
            pass
        window = webview.create_window("Jilo", url, js_api=api, width=max(w, 960), height=max(h, 620),
                                       min_size=(960, 620), background_color="#0d0d10")
        api._window = window
        webview.start(_bind_drop, window)
    except Exception as e:
        print("Окно не открылось (%s), открываю в браузере" % e)
        _browser_mode(url)
    finally:
        # окно закрыли во время обработки — останавливаем её и убираем временный файл
        if server.STATE.export.get("state") == "running":
            server.STATE.cancel_export = True
            for _ in range(30):
                if server.STATE.export.get("state") != "running":
                    break
                time.sleep(0.1)
        try:
            httpd.shutdown()
        except Exception:
            pass
        os._exit(0)


if __name__ == "__main__":
    main()
