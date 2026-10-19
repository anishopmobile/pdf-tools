"""Qt Widgets desktop UI. Processing is always dispatched off the GUI thread."""

from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QThread, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QDragEnterEvent, QDropEvent, QIcon
from PySide6.QtWidgets import (
    QApplication, QAbstractItemView, QFileDialog, QFrame, QHBoxLayout,
    QLabel, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
    QProgressBar, QPushButton, QStackedWidget, QVBoxLayout, QWidget,
)

from pdf_tools import __version__
from pdf_tools.core import (
    ProcessingError, convert_images, image_output_directory, merge_pdfs,
    scan_pdfs, validate_images,
)


STYLE = """
QMainWindow, QWidget { background: #F5F7FC; color: #17263B; font-family: 'Segoe UI'; font-size: 11pt; }
QLabel#heading { font-size: 24pt; font-weight: 750; }
QLabel#subheading { font-size: 12pt; color: #596A7C; }
QLabel#status { color: #42627C; }
QFrame#card { border: 1px solid #DCE4EF; border-radius: 18px; background: #FFFFFF; }
QFrame#dropzone { border: 2px dashed #A4B8D8; border-radius: 14px; background: #EDF4FF; }
QFrame#dropzone[active="true"] { border-color: #2671CF; background: #DCEBFF; }
QPushButton { border: 1px solid #C7D5E7; border-radius: 9px; background: #FFFFFF; padding: 10px 14px; font-weight: 600; }
QPushButton:hover { background: #EAF1FF; border-color: #427FD5; }
QPushButton:disabled { background: #F0F1F3; color: #9AA5B2; border-color: #DADFE5; }
QPushButton#primary { background: #2169BE; color: white; border-color: #2169BE; }
QPushButton#primary:hover { background: #1355A5; }
QListWidget { background: #FFFFFF; border: 1px solid #DCE4EF; border-radius: 12px; padding: 8px; }
QListWidget::item { border-bottom: 1px solid #EDF0F5; padding: 11px 7px; }
QListWidget::item:selected { background: #DCEBFF; color: #142F53; }
QProgressBar { border: 1px solid #DCE4EF; border-radius: 7px; height: 15px; background: #E6EBF3; text-align: center; }
QProgressBar::chunk { background: #357AD2; border-radius: 6px; }
"""


class WorkThread(QThread):
    progress_changed = Signal(int, int)
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, work: Callable, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.work = work
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def cancelled(self) -> bool:
        return self._cancelled

    def run(self) -> None:
        try:
            result = self.work(self.progress_changed.emit, self.cancelled)
        except (ProcessingError, OSError, MemoryError) as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            traceback.print_exc()  # diagnostic only; never report false success
            self.failed.emit(f"Operation failed unexpectedly: {type(exc).__name__}: {exc}")
        else:
            self.succeeded.emit(result)


class DropZone(QFrame):
    paths_dropped = Signal(list)
    invalid_dropped = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("dropzone")
        self.setProperty("active", False)
        self.setAcceptDrops(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 30, 28, 30)
        self.hint = QLabel("Drop JPG / JPEG images here")
        self.hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hint.setStyleSheet("background: transparent; font-weight: 600; font-size: 14pt")
        layout.addWidget(self.hint)
        sub = QLabel("Or click Add Images below · multiple files supported")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setStyleSheet("color: #536B84; background: transparent")
        layout.addWidget(sub)

    def _highlight(self, enabled: bool) -> None:
        self.setProperty("active", enabled)
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls() and all(url.isLocalFile() for url in event.mimeData().urls()):
            event.acceptProposedAction()
            self._highlight(True)
        else:
            event.ignore()

    def dragLeaveEvent(self, event) -> None:
        self._highlight(False)
        event.accept()

    def dropEvent(self, event: QDropEvent) -> None:
        self._highlight(False)
        if not event.mimeData().hasUrls():
            self.invalid_dropped.emit("Drop image files from File Explorer.")
            event.ignore()
            return
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if not paths:
            self.invalid_dropped.emit("Only local JPG/JPEG files are supported.")
            event.ignore()
            return
        self.paths_dropped.emit(paths)
        event.acceptProposedAction()


class ToolPage(QWidget):
    go_home = Signal()

    def __init__(self, title: str, description: str, *, jpeg: bool) -> None:
        super().__init__()
        self.jpeg = jpeg
        self.selected_folder: Path | None = None
        self.last_output: Path | None = None
        self.busy = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(35, 25, 35, 30)
        outer.setSpacing(12)
        top = QHBoxLayout()
        home = QPushButton("← Home")
        home.clicked.connect(self.go_home.emit)
        self.home_button = home
        top.addWidget(home)
        top.addStretch()
        outer.addLayout(top)
        title_label = QLabel(title)
        title_label.setObjectName("heading")
        outer.addWidget(title_label)
        hint = QLabel(description)
        hint.setObjectName("subheading")
        hint.setWordWrap(True)
        outer.addWidget(hint)
        if jpeg:
            self.drop_zone = DropZone()
            outer.addWidget(self.drop_zone)
        else:
            self.drop_zone = None
        controls = QHBoxLayout()
        self.add_button = QPushButton("Add Images" if jpeg else "Select Folder")
        self.add_button.setObjectName("primary")
        controls.addWidget(self.add_button)
        self.remove_button = QPushButton("Remove Selected")
        controls.addWidget(self.remove_button)
        self.clear_button = QPushButton("Clear List")
        controls.addWidget(self.clear_button)
        controls.addStretch()
        outer.addLayout(controls)
        self.count_label = QLabel("0 files · Drag items to change their order")
        self.count_label.setObjectName("status")
        outer.addWidget(self.count_label)
        self.file_list = QListWidget()
        self.file_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.file_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.file_list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.file_list.setAlternatingRowColors(False)
        outer.addWidget(self.file_list, 1)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        outer.addWidget(self.progress)
        self.status = QLabel("Ready")
        self.status.setObjectName("status")
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        outer.addWidget(self.status)
        actions = QHBoxLayout()
        self.run_button = QPushButton("Convert to PDF" if jpeg else "Merge PDFs")
        self.run_button.setObjectName("primary")
        actions.addWidget(self.run_button)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setEnabled(False)
        actions.addWidget(self.cancel_button)
        self.new_button = QPushButton("New Operation")
        actions.addWidget(self.new_button)
        actions.addStretch()
        outer.addLayout(actions)
        output_actions = QHBoxLayout()
        self.open_button = QPushButton("Open PDF")
        self.open_button.setEnabled(False)
        output_actions.addWidget(self.open_button)
        self.folder_button = QPushButton("Open Output Folder")
        self.folder_button.setEnabled(False)
        output_actions.addWidget(self.folder_button)
        output_actions.addStretch()
        outer.addLayout(output_actions)
        self.remove_button.clicked.connect(self.remove_selected)
        self.clear_button.clicked.connect(self.clear_list)
        self.new_button.clicked.connect(self.reset)
        self.file_list.model().rowsMoved.connect(self.update_count)
        self.file_list.model().rowsInserted.connect(self.update_count)
        self.file_list.model().rowsRemoved.connect(self.update_count)
        self.open_button.clicked.connect(self.open_output)
        self.folder_button.clicked.connect(self.open_folder)
        self.update_count()

    def paths(self) -> list[Path]:
        return [Path(self.file_list.item(i).data(Qt.ItemDataRole.UserRole))
                for i in range(self.file_list.count())]

    def add_items(self, paths: list[Path]) -> None:
        for path in paths:
            item = QListWidgetItem(f"{self.file_list.count() + 1:02}   {path.name}\n      {path}")
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            item.setToolTip(str(path))
            self.file_list.addItem(item)
        self.update_count()

    def update_count(self, *args) -> None:
        self.count_label.setText(f"{self.file_list.count()} files · Drag items to change their order (top → bottom)")
        for i in range(self.file_list.count()):
            item = self.file_list.item(i)
            path = Path(item.data(Qt.ItemDataRole.UserRole))
            item.setText(f"{i + 1:02}   {path.name}\n      {path}")
        self.run_button.setEnabled(not self.busy and self.file_list.count() >= (1 if self.jpeg else 2))

    def remove_selected(self) -> None:
        for item in list(self.file_list.selectedItems()):
            self.file_list.takeItem(self.file_list.row(item))
        self.update_count()

    def clear_list(self) -> None:
        self.file_list.clear()
        self.update_count()

    def reset(self) -> None:
        if self.busy:
            return
        self.clear_list()
        self.selected_folder = None
        self.last_output = None
        self.status.setText("Ready")
        self.progress.setValue(0)
        self.open_button.setEnabled(False)
        self.folder_button.setEnabled(False)

    def set_busy(self, busy: bool) -> None:
        self.busy = busy
        self.cancel_button.setEnabled(busy)
        for widget in (self.add_button, self.remove_button, self.clear_button,
                       self.home_button, self.new_button, self.file_list):
            widget.setEnabled(not busy)
        if self.drop_zone:
            self.drop_zone.setAcceptDrops(not busy)
        self.update_count()

    def set_progress(self, done: int, total: int) -> None:
        self.progress.setValue(round(done * 100 / total) if total else 0)
        self.status.setText(f"Processing {done} of {total} files…")

    def finished(self, output: Path, summary: str) -> None:
        self.last_output = output
        self.progress.setValue(100)
        self.status.setText(f"{summary}\nSaved: {output}")
        self.open_button.setEnabled(True)
        self.folder_button.setEnabled(True)

    def open_output(self) -> None:
        if self.last_output and self.last_output.is_file():
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.last_output))):
                QMessageBox.warning(self, "Open PDF", "Windows could not open the PDF. Check your default PDF viewer.")

    def open_folder(self) -> None:
        if self.last_output:
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.last_output.parent))):
                QMessageBox.warning(self, "Open Folder", "Cannot open the output folder.")


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("PDF Tools")
        self.setMinimumSize(690, 580)
        self.resize(900, 750)
        self.setStyleSheet(STYLE)
        self.worker: WorkThread | None = None
        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)
        self.home = QWidget()
        self.stack.addWidget(self.home)
        self.images = ToolPage("JPG to PDF", "Combine JPG / JPEG images into a single PDF without recompressing the original JPEG data.", jpeg=True)
        self.pdfs = ToolPage("Merge PDF", "Select a folder, arrange the PDF files, then combine their pages in order.", jpeg=False)
        self.stack.addWidget(self.images)
        self.stack.addWidget(self.pdfs)
        self._create_home()
        self.images.go_home.connect(lambda: self.stack.setCurrentWidget(self.home))
        self.pdfs.go_home.connect(lambda: self.stack.setCurrentWidget(self.home))
        self.images.add_button.clicked.connect(self.choose_images)
        self.images.drop_zone.paths_dropped.connect(self.add_images)
        self.images.drop_zone.invalid_dropped.connect(lambda reason: self._error(self.images, reason))
        self.pdfs.add_button.clicked.connect(self.choose_folder)
        self.images.run_button.clicked.connect(self.convert)
        self.pdfs.run_button.clicked.connect(self.merge)

    def _create_home(self) -> None:
        layout = QVBoxLayout(self.home)
        layout.setContentsMargins(54, 50, 54, 55)
        layout.setSpacing(18)
        layout.addStretch()
        title = QLabel("PDF Tools")
        title.setObjectName("heading")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        subtitle = QLabel("Private, offline PDF utilities for Windows")
        subtitle.setObjectName("subheading")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(subtitle)
        layout.addSpacing(25)
        for label, description, target in (
            ("JPG to PDF  →", "Turn one or more images into a single PDF", self.images),
            ("Merge PDF  →", "Combine PDF documents in the order you choose", self.pdfs),
        ):
            card = QFrame()
            card.setObjectName("card")
            block = QVBoxLayout(card)
            block.setContentsMargins(24, 24, 24, 24)
            button = QPushButton(label)
            button.setObjectName("primary")
            button.setMinimumHeight(66)
            button.setStyleSheet("font-size: 16pt; text-align: left; padding-left: 25px")
            button.clicked.connect(lambda checked=False, t=target: self.stack.setCurrentWidget(t))
            block.addWidget(button)
            sub = QLabel(description)
            sub.setObjectName("subheading")
            block.addWidget(sub)
            layout.addWidget(card)
        layout.addStretch(2)
        footer = QLabel(f"PDF Tools v{__version__} · No uploads · Works offline")
        footer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        footer.setObjectName("subheading")
        layout.addWidget(footer)

    def _error(self, page: ToolPage, message: str) -> None:
        page.status.setText(message)
        QMessageBox.warning(self, "PDF Tools", message)

    def _start(self, page: ToolPage, work: Callable, on_success: Callable, *, progress: bool) -> None:
        if self.worker is not None:
            return
        page.set_busy(True)
        page.status.setText("Working…")
        page.progress.setValue(0)
        if not progress:
            page.progress.setRange(0, 0)  # Indeterminate scan/validation animation
        thread = WorkThread(work, self)
        self.worker = thread
        page.cancel_button.clicked.connect(thread.cancel)
        thread.progress_changed.connect(page.set_progress)

        def success(result) -> None:
            page.progress.setRange(0, 100)
            on_success(result)

        def failed(reason: str) -> None:
            page.progress.setRange(0, 100)
            page.progress.setValue(0)
            self._error(page, reason)

        def cleanup() -> None:
            try:
                page.cancel_button.clicked.disconnect(thread.cancel)
            except (RuntimeError, TypeError):
                pass
            page.set_busy(False)
            self.worker = None
            thread.deleteLater()

        thread.succeeded.connect(success)
        thread.failed.connect(failed)
        thread.finished.connect(cleanup)
        thread.start()

    def choose_images(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Select JPG Images", "", "JPEG Images (*.jpg *.jpeg)")
        if paths:
            self.add_images(paths)

    def add_images(self, paths: list[str]) -> None:
        if self.worker:
            return
        existing = list(self.images.paths())

        def work(progress, cancelled):
            return validate_images(paths, existing, progress, cancelled)

        def done(result) -> None:
            good, bad = result
            self.images.add_items([info.path for info in good])
            self.images.status.setText(f"Added {len(good)} image(s)." + (f" Skipped {len(bad)}." if bad else ""))
            if bad:
                self._error(self.images, "Some files were not added:\n" + "\n".join(bad[:12]))

        self._start(self.images, work, done, progress=False)

    def choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose PDF Folder")
        if not folder:
            return
        selected = Path(folder)

        def work(progress, cancelled):
            return scan_pdfs(selected, progress, cancelled)

        def done(result) -> None:
            paths, rejected = result
            self.pdfs.clear_list()
            self.pdfs.last_output = None
            self.pdfs.open_button.setEnabled(False)
            self.pdfs.folder_button.setEnabled(False)
            self.pdfs.selected_folder = selected
            self.pdfs.add_items(paths)
            self.pdfs.status.setText(f"Found {len(paths)} valid PDF(s) in {selected}.")
            if rejected:
                self._error(self.pdfs, "Skipped unreadable PDF files:\n" + "\n".join(rejected[:12]))
            elif not paths:
                self._error(self.pdfs, "No valid PDF files were found in this folder.")

        self._start(self.pdfs, work, done, progress=False)

    def convert(self) -> None:
        paths = self.images.paths()
        if not paths:
            return
        folder = image_output_directory(paths)
        if folder is None:
            chosen = QFileDialog.getExistingDirectory(self, "Choose Output Folder")
            if not chosen:
                self.images.status.setText("Output selection cancelled.")
                return
            folder = Path(chosen)

        def work(progress, cancelled):
            return convert_images(paths, folder, progress, cancelled)

        def done(result) -> None:
            dest, n = result
            self.images.finished(dest, f"Success: converted {n} image(s) into {n} PDF page(s).")

        self._start(self.images, work, done, progress=True)

    def merge(self) -> None:
        paths = self.pdfs.paths()
        if len(paths) < 2 or self.pdfs.selected_folder is None:
            self._error(self.pdfs, "Select a folder with at least two valid PDFs.")
            return
        folder = self.pdfs.selected_folder

        def work(progress, cancelled):
            return merge_pdfs(paths, folder, progress, cancelled)

        def done(result) -> None:
            dest, n, pages = result
            self.pdfs.finished(dest, f"Success: merged {n} PDF(s), {pages} pages total.")

        self._start(self.pdfs, work, done, progress=True)

    def closeEvent(self, event) -> None:
        if self.worker and self.worker.isRunning():
            QMessageBox.information(self, "Operation running", "Please allow the current operation to finish before closing.")
            event.ignore()
        else:
            event.accept()


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("PDF Tools")
    app.setApplicationVersion(__version__)
    window = MainWindow()
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    ico = base / "assets" / "pdf-tools.ico"
    if ico.is_file():
        window.setWindowIcon(QIcon(str(ico)))
    window.show()
    return app.exec()
