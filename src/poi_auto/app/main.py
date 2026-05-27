from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from poi_auto.gui.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.resize(1180, 760)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

