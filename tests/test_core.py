"""Deterministic tests of actual generated documents (not mock PDF writes)."""

import os
from pathlib import Path

import pytest
from PIL import Image
from pypdf import PdfReader, PdfWriter

from pdf_tools.core import (
    OperationCancelled, ProcessingError, convert_images, image_output_directory,
    inspect_jpeg, merge_pdfs, natural_sort_key, scan_pdfs, validate_images,
)


def jpg(path, dimensions=(60, 40), color=(220, 40, 30), orientation=None):
    image = Image.new("RGB", dimensions, color)
    kwargs = {"quality": 94, "subsampling": 0}
    if orientation:
        exif = Image.Exif()
        exif[274] = orientation
        kwargs["exif"] = exif
    image.save(path, **kwargs)
    return path


def pdf(path, texts, sizes=None):
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    for index, text in enumerate(texts):
        w, h = (sizes[index] if sizes else (200, 300))
        page = writer.add_blank_page(width=w, height=h)
        # Text via standard PDF Helvetica font and direct, searchable content.
        from pypdf.generic import DictionaryObject, NumberObject
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({
                NameObject("/F1"): DictionaryObject({
                    NameObject("/Type"): NameObject("/Font"),
                    NameObject("/Subtype"): NameObject("/Type1"),
                    NameObject("/BaseFont"): NameObject("/Helvetica"),
                })
            })
        })
        content = DecodedStreamObject()
        content.set_data(f"BT /F1 12 Tf 10 10 Td ({text}) Tj ET".encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(content)
    with open(path, "wb") as stream:
        writer.write(stream)
    writer.close()
    return path


def test_one_image_preserves_encoded_bytes_and_dimensions(tmp_path):
    image = jpg(tmp_path / "one.jpg", (120, 60))
    saved, count = convert_images([image])
    assert saved == tmp_path / "converted_images.pdf" and count == 1
    result = PdfReader(saved)
    assert len(result.pages) == 1
    page = result.pages[0]
    assert float(page.mediabox.width) == pytest.approx(90)  # 96 dpi default
    assert float(page.mediabox.height) == pytest.approx(45)
    xobject = page["/Resources"]["/XObject"]["/Im0"].get_object()
    assert xobject["/Filter"] == "/DCTDecode"
    assert xobject._data == image.read_bytes()


def test_multiple_order_size_and_different_pages(tmp_path):
    a = jpg(tmp_path / "a.jpg", (100, 40), (255, 0, 0))
    b = jpg(tmp_path / "b.jpeg", (40, 100), (0, 255, 0))
    c = jpg(tmp_path / "c.jpg", (55, 55), (0, 0, 255))
    saved, count = convert_images([b, a, c])
    assert count == 3
    pages = PdfReader(saved).pages
    assert [p["/Resources"]["/XObject"]["/Im0"].get_object()._data for p in pages] == [b.read_bytes(), a.read_bytes(), c.read_bytes()]
    assert [float(p.mediabox.width) for p in pages] == pytest.approx([30, 75, 41.25])
    assert [float(p.mediabox.height) for p in pages] == pytest.approx([75, 30, 41.25])


@pytest.mark.parametrize("orientation,transform", [
    (1, (1, 0, 0, 1, 0, 0)), (2, (-1, 0, 0, 1, 1, 0)),
    (3, (-1, 0, 0, -1, 1, 1)), (4, (1, 0, 0, -1, 0, 1)),
    (5, (0, -1, -1, 0, 1, 1)), (6, (0, -1, 1, 0, 0, 1)),
    (7, (0, 1, 1, 0, 0, 0)), (8, (0, 1, -1, 0, 1, 0)),
])
def test_exif_orientations_preserve_source_without_double_rotation(tmp_path, orientation, transform):
    path = jpg(tmp_path / "image.jpg", (120, 60), orientation=orientation)
    output, _ = convert_images([path])
    page = PdfReader(output).pages[0]
    wide = orientation in (1, 2, 3, 4)
    w, h = float(page.mediabox.width), float(page.mediabox.height)
    assert (w > h) == wide
    contents = page.get_contents().get_data().decode("ascii")
    assert contents.count("/Im0 Do") == 1
    # Assert *each* page transform; swapping EXIF 5 and 7 used to render
    # transposed images upside down, despite correct page dimensions.
    actual = [float(x) for x in contents.splitlines()[1].split()[:6]]
    a, b, c, d, e, f = transform
    assert actual == pytest.approx([a*w, b*h, c*w, d*h, e*w, f*h], abs=1e-5)
    assert page["/Resources"]["/XObject"]["/Im0"].get_object()._data == path.read_bytes()


def test_invalid_and_non_jpeg_are_rejected(tmp_path):
    (tmp_path / "bad.jpg").write_bytes(b"not a jpeg")
    (tmp_path / "bad.txt").write_text("hello")
    good = jpg(tmp_path / "good.jpg")
    accepted, rejected = validate_images([good, good, tmp_path / "bad.jpg", tmp_path / "bad.txt", tmp_path])
    assert [i.path for i in accepted] == [good] and len(rejected) == 4
    with pytest.raises(ProcessingError):
        convert_images([tmp_path / "bad.jpg"])


def test_invalid_truncated_jpeg(tmp_path):
    path = jpg(tmp_path / "truncated.jpg")
    path.write_bytes(path.read_bytes()[:-2])
    with pytest.raises(ProcessingError, match="truncated"):
        inspect_jpeg(path)


def test_collision_and_persian_path_and_input_unmodified(tmp_path):
    directory = tmp_path / "پوشه با فاصله"
    directory.mkdir()
    path = jpg(directory / "تصویر شماره ۱.jpg")
    original = path.read_bytes()
    (directory / "converted_images.pdf").write_bytes(b"existing user data")
    result, _ = convert_images([path])
    assert result == directory / "converted_images_1.pdf"
    assert (directory / "converted_images.pdf").read_bytes() == b"existing user data"
    assert path.read_bytes() == original
    assert len(PdfReader(result).pages) == 1


def test_cancel_and_atomic_error_cleans_temps(tmp_path, monkeypatch):
    a = jpg(tmp_path / "a.jpg")
    with pytest.raises(OperationCancelled):
        convert_images([a], cancel=lambda: True)
    assert not list(tmp_path.glob("*.pdf"))
    assert not list(tmp_path.glob("*.part"))
    import pdf_tools.core as core
    def fail(*args, **kwargs):
        raise PermissionError("simulated destination permission denied")
    monkeypatch.setattr(core, "_publish_unique", fail)
    with pytest.raises(ProcessingError, match="permission denied"):
        convert_images([a])
    assert not list(tmp_path.glob("*.part"))


def test_multiple_drive_root_or_common_output_selection(tmp_path):
    left = tmp_path / "left"
    right = tmp_path / "right"
    left.mkdir()
    right.mkdir()
    assert image_output_directory([left / "1.jpg", right / "2.jpg"]) == tmp_path
    assert image_output_directory([]) is None


def test_merge_two_preserves_text_and_page_sizes(tmp_path):
    a = pdf(tmp_path / "a.pdf", ["a1", "a2"], [(200, 300), (300, 200)])
    b = pdf(tmp_path / "b.pdf", ["b1"], [(500, 100)])
    original_a, original_b = a.read_bytes(), b.read_bytes()
    out, file_count, total = merge_pdfs([b, a], tmp_path)
    assert (out, file_count, total) == (tmp_path / "merged_document.pdf", 2, 3)
    pages = PdfReader(out).pages
    assert [page.extract_text() for page in pages] == ["b1", "a1", "a2"]
    assert [(int(page.mediabox.width), int(page.mediabox.height)) for page in pages] == [
        (500, 100), (200, 300), (300, 200)]
    assert (a.read_bytes(), b.read_bytes()) == (original_a, original_b)


def test_merge_many_varied_pages_order(tmp_path):
    paths = [pdf(tmp_path / f"file{i}.pdf", [f"{i}-p{j}" for j in range(i)]) for i in (1, 2, 3)]
    output, count, pages = merge_pdfs(paths, tmp_path)
    assert (count, pages) == (3, 6)
    assert [p.extract_text() for p in PdfReader(output).pages] == [
        "1-p0", "2-p0", "2-p1", "3-p0", "3-p1", "3-p2"]


def test_natural_sort_and_ignore_output_hidden_and_subfolders(tmp_path):
    pdf(tmp_path / "file10.PDF", ["10"])
    pdf(tmp_path / "file2.pdf", ["2"])
    pdf(tmp_path / "file1.pdf", ["1"])
    pdf(tmp_path / "merged_document.pdf", ["older output"])
    pdf(tmp_path / "merged_document_1.PDF", ["another output"])
    pdf(tmp_path / ".hidden.pdf", ["hidden"])
    subdir = tmp_path / "subfolder"
    subdir.mkdir()
    pdf(subdir / "nested.pdf", ["nested"])
    (tmp_path / ".pdf-tools-thing.part").write_bytes(b"incomplete")
    paths, rejected = scan_pdfs(tmp_path)
    assert [p.name for p in paths] == ["file1.pdf", "file2.pdf", "file10.PDF"]
    assert not rejected


def test_merge_corrupt_encrypted_empty_and_one(tmp_path):
    (tmp_path / "damaged.pdf").write_bytes(b"%PDF-1.7\nnot valid")
    enc = PdfWriter()
    enc.add_blank_page(width=100, height=100)
    enc.encrypt("secret")
    with (tmp_path / "secret.pdf").open("wb") as out:
        enc.write(out)
    enc.close()
    one = pdf(tmp_path / "normal.pdf", ["one"])
    files, rejected = scan_pdfs(tmp_path)
    assert files == [one] and len(rejected) == 2
    with pytest.raises(ProcessingError, match="at least two"):
        merge_pdfs(files, tmp_path)
    with pytest.raises(ProcessingError):
        merge_pdfs([one, tmp_path / "damaged.pdf"], tmp_path)


def test_no_pdf(tmp_path):
    assert scan_pdfs(tmp_path) == ([], [])


def test_merge_output_cannot_be_input_and_unique_name(tmp_path):
    a = pdf(tmp_path / "a.pdf", ["A"])
    b = pdf(tmp_path / "b.pdf", ["B"])
    first, _, _ = merge_pdfs([a, b], tmp_path)
    second, _, _ = merge_pdfs([a, b], tmp_path)
    assert first.name == "merged_document.pdf"
    assert second.name == "merged_document_1.pdf"
    assert len(PdfReader(first).pages) == 2
    assert len(PdfReader(second).pages) == 2
    assert scan_pdfs(tmp_path)[0] == [a, b]
    with pytest.raises(ProcessingError, match="Previous merged"):
        merge_pdfs([a, first], tmp_path)


def test_merge_cancellation_and_permission_failure(tmp_path, monkeypatch):
    a = pdf(tmp_path / "a.pdf", ["A"])
    b = pdf(tmp_path / "b.pdf", ["B"])
    with pytest.raises(OperationCancelled):
        merge_pdfs([a, b], tmp_path, cancel=lambda: True)
    assert not list(tmp_path.glob("*.part"))
    import pdf_tools.core as core
    def fail(*args, **kwargs):
        raise OSError("simulated disk full")
    monkeypatch.setattr(core, "_publish_unique", fail)
    with pytest.raises(ProcessingError, match="disk full"):
        merge_pdfs([a, b], tmp_path)
    assert not list(tmp_path.glob("*.part"))


def test_pdf_name_extensions_input_duplicate(tmp_path):
    a = pdf(tmp_path / "a.pdf", ["a"])
    with pytest.raises(ProcessingError, match="more than once"):
        merge_pdfs([a, a], tmp_path)


def test_output_folder_explicit_and_progress(tmp_path):
    images = tmp_path / "images"
    destination = tmp_path / "output"
    images.mkdir()
    destination.mkdir()
    a = jpg(images / "a.jpg")
    b = jpg(images / "b.jpg")
    calls = []
    output, count = convert_images([a, b], destination, progress=lambda done, total: calls.append((done, total)))
    assert output.parent == destination
    assert count == 2 and calls == [(1, 2), (2, 2)]
    assert len(PdfReader(output).pages) == 2


def test_cancel_after_first_page_never_publishes_partial(tmp_path):
    a = jpg(tmp_path / "a.jpg")
    b = jpg(tmp_path / "b.jpg")
    state = {"stop": False}
    with pytest.raises(OperationCancelled):
        convert_images([a, b], progress=lambda done, total: state.__setitem__("stop", True),
                       cancel=lambda: state["stop"])
    assert not list(tmp_path.glob("*.pdf"))
    assert not list(tmp_path.glob("*.part"))


def test_empty_pdf_skipped_in_folder(tmp_path):
    path = tmp_path / "empty.pdf"
    writer = PdfWriter()
    with path.open("wb") as stream:
        writer.write(stream)
    writer.close()
    paths, errors = scan_pdfs(tmp_path)
    assert not paths and len(errors) == 1 and "no pages" in errors[0]


def test_exclusive_copy_fallback_without_hardlink(tmp_path, monkeypatch):
    # Some Windows/network filesystems disallow hardlinks; exclusive copy
    # must still work without overwriting an existing PDF.
    import errno
    import pdf_tools.core as core
    def no_hardlink(*args, **kwargs):
        raise OSError(errno.EOPNOTSUPP, "Hardlinks not supported")
    monkeypatch.setattr(core.os, "link", no_hardlink)
    a = jpg(tmp_path / "a.jpg")
    (tmp_path / "converted_images.pdf").write_bytes(b"original")
    output, count = convert_images([a])
    assert output.name == "converted_images_1.pdf" and count == 1
    assert len(PdfReader(output).pages) == 1
    assert (tmp_path / "converted_images.pdf").read_bytes() == b"original"


def test_input_moved_or_deleted_after_selection(tmp_path):
    image = jpg(tmp_path / "missing.jpg")
    good, warnings = validate_images([image])
    assert len(good) == 1 and not warnings
    image.unlink()
    with pytest.raises(ProcessingError, match="Invalid images"):
        convert_images([image])
    first = pdf(tmp_path / "first.pdf", ["first"])
    second = pdf(tmp_path / "second.pdf", ["second"])
    second.unlink()
    with pytest.raises(ProcessingError, match="Cannot merge"):
        merge_pdfs([first, second], tmp_path)
    assert not list(tmp_path.glob("*.part"))


def test_output_folder_missing_and_invalid_scan_folder(tmp_path):
    jpeg = jpg(tmp_path / "a.jpg")
    with pytest.raises(ProcessingError, match="Output folder not found"):
        convert_images([jpeg], tmp_path / "no-such-folder")
    with pytest.raises(ProcessingError, match="Folder does not exist"):
        scan_pdfs(tmp_path / "no-such-folder")


def test_passwordless_pdf_encryption_can_merge_when_decryptable(tmp_path):
    a = pdf(tmp_path / "a.pdf", ["hello"])
    writer = PdfWriter()
    writer.add_blank_page(width=150, height=220)
    writer.encrypt("", owner_password="owner-password")
    b = tmp_path / "passwordless.pdf"
    with b.open("wb") as dst:
        writer.write(dst)
    writer.close()
    found, warnings = scan_pdfs(tmp_path)
    assert found == [a, b] and warnings == []
    result, n, pages = merge_pdfs(found, tmp_path)
    assert n == 2 and pages == 2
    assert len(PdfReader(result).pages) == 2
