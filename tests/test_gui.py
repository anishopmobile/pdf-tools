"""Qt integration tests: actual widgets, worker signals and local drag/drop events.

Windows CI installs PySide6, so these tests execute there. On a machine
without PySide6 they are explicitly skipped rather than counted as passing.
"""

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6", reason="PySide6 GUI toolkit not installed")
from PIL import Image
from PySide6.QtCore import QPointF, QMimeData, Qt, QUrl
from PySide6.QtGui import QDropEvent
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox
from pypdf import PdfReader, PdfWriter

from pdf_tools.gui import MainWindow


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    yield instance


@pytest.fixture
def window(app, monkeypatch):
    # Capture real calls to QMessageBox; avoid blocking a test runner.
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "information", lambda *args: QMessageBox.StandardButton.Ok)
    w = MainWindow()
    w.show()
    yield w
    if w.worker:
        wait(app, lambda: w.worker is None)
    w.close()


def wait(app, predicate, max_iterations=350):
    for _ in range(max_iterations):
        app.processEvents()
        if predicate():
            return
        from PySide6.QtTest import QTest
        QTest.qWait(20)
    pytest.fail("GUI worker operation did not finish")


def jpg(path):
    Image.new("RGB", (45, 30), (100, 140, 190)).save(path)
    return path


def pdf(path):
    writer = PdfWriter()
    writer.add_blank_page(width=250, height=300)
    with path.open("wb") as stream:
        writer.write(stream)
    writer.close()
    return path


def test_home_cards_and_navigation(window):
    assert window.windowTitle().startswith("PDF Tools")
    assert window.stack.currentWidget() is window.home
    window.stack.setCurrentWidget(window.images)
    window.images.go_home.emit()
    assert window.stack.currentWidget() is window.home
    window.stack.setCurrentWidget(window.pdfs)
    window.pdfs.go_home.emit()
    assert window.stack.currentWidget() is window.home


def test_image_dialog_drag_drop_reorder_remove_and_convert(window, app, tmp_path, monkeypatch):
    one = jpg(tmp_path / "first.jpg")
    two = jpg(tmp_path / "second.jpeg")
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", lambda *args: ([str(one)], ""))
    window.stack.setCurrentWidget(window.images)
    window.choose_images()
    wait(app, lambda: window.worker is None)
    assert window.images.paths() == [one]
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(two)), QUrl.fromLocalFile(str(one))])
    drop = QDropEvent(QPointF(16, 16), Qt.DropAction.CopyAction, mime,
                      Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(window.images.drop_zone, drop)
    wait(app, lambda: window.worker is None)
    assert window.images.paths() == [one, two]  # duplicate rejected
    item = window.images.file_list.takeItem(1)
    window.images.file_list.insertItem(0, item)
    window.images.update_count()
    assert window.images.paths() == [two, one]
    assert window.images.count_label.text().startswith("2 files")
    window.convert()
    assert not window.images.run_button.isEnabled()  # prevents double submit
    wait(app, lambda: window.worker is None)
    assert window.images.last_output == tmp_path / "converted_images.pdf"
    assert len(PdfReader(window.images.last_output).pages) == 2
    assert window.images.progress.value() == 100
    assert window.images.open_button.isEnabled()
    window.images.file_list.item(0).setSelected(True)
    window.images.remove_selected()
    assert window.images.paths() == [one]
    window.images.reset()
    assert window.images.paths() == []
    assert window.images.last_output is None


def test_folder_dialog_merge_and_new_operation(window, app, tmp_path, monkeypatch):
    pdf(tmp_path / "file10.pdf")
    pdf(tmp_path / "file2.pdf")
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *args: str(tmp_path))
    window.stack.setCurrentWidget(window.pdfs)
    window.choose_folder()
    wait(app, lambda: window.worker is None)
    assert [p.name for p in window.pdfs.paths()] == ["file2.pdf", "file10.pdf"]
    window.merge()
    wait(app, lambda: window.worker is None)
    assert window.pdfs.last_output == tmp_path / "merged_document.pdf"
    assert len(PdfReader(window.pdfs.last_output).pages) == 2
    assert window.pdfs.folder_button.isEnabled()
    window.choose_folder()
    wait(app, lambda: window.worker is None)
    assert [p.name for p in window.pdfs.paths()] == ["file2.pdf", "file10.pdf"]
    window.merge()
    wait(app, lambda: window.worker is None)
    assert window.pdfs.last_output.name == "merged_document_1.pdf"
    window.pdfs.reset()
    assert not window.pdfs.run_button.isEnabled()
