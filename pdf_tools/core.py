"""Pure-Python, offline document processing; no Qt or network dependency.

JPEG data is copied byte-for-byte into PDF DCTDecode image streams.  We never
rasterize source PDFs.  Files are staged in the destination directory and are
published with an exclusive name, without replacing existing files.
"""

from __future__ import annotations

import errno
import os
import re
import shutil
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from PIL import Image, UnidentifiedImageError
from pypdf import PdfReader, PdfWriter

Progress = Callable[[int, int], None]
Cancelled = Callable[[], bool]


class ProcessingError(Exception):
    """Actionable, safe-to-display error."""


class OperationCancelled(ProcessingError):
    pass


@dataclass(frozen=True)
class ImageInfo:
    path: Path
    width: int
    height: int
    orientation: int
    color_space: str
    dpi: float
    size: int
    mtime_ns: int


def _check_cancel(cancel: Cancelled | None) -> None:
    if cancel and cancel():
        raise OperationCancelled("Operation cancelled. No output was saved.")


def _real_key(p: Path) -> str:
    return os.path.normcase(os.path.abspath(p)).casefold()


def inspect_jpeg(path: str | Path, *, decode_preview: bool = True) -> ImageInfo:
    p = Path(path)
    if p.suffix.lower() not in {".jpg", ".jpeg"}:
        raise ProcessingError(f"Not a JPG/JPEG file: {p.name}")
    try:
        if not p.is_file():
            raise ProcessingError(f"File missing or not a regular file: {p}")
        before = p.stat()
        with Image.open(p) as im:
            if im.format != "JPEG":
                raise ProcessingError(f"Not actually a JPEG image: {p.name}")
            if im.mode not in {"RGB", "L"}:
                raise ProcessingError(
                    f"Unsupported JPEG color mode ({im.mode}): {p.name}. "
                    "Convert this image to RGB JPEG first."
                )
            width, height = im.size
            if width < 1 or height < 1:
                raise ProcessingError(f"Image has invalid dimensions: {p.name}")
            orientation = int(im.getexif().get(274, 1))
            if orientation not in range(1, 9):
                orientation = 1
            dpi_pair = im.info.get("dpi", (96, 96))
            try:
                dpi_x, dpi_y = (float(dpi_pair[0]), float(dpi_pair[1]))
                dpi = dpi_x if 36 <= dpi_x <= 1200 and 36 <= dpi_y <= 1200 else 96.0
            except (ValueError, TypeError, IndexError):
                dpi = 96.0
            color_space = "/DeviceGray" if im.mode == "L" else "/DeviceRGB"
            im.verify()
        if decode_preview:
            # JPEG draft decodes at a reduced factor (when possible), detecting
            # broken entropy streams while keeping preview RAM bounded.  It
            # never modifies or re-encodes the source JPEG used in the PDF.
            with Image.open(p) as preview:
                preview.draft(preview.mode, (256, 256))
                preview.load()
        # Reject truncated JPEG files with missing end-of-image markers.
        with p.open("rb") as stream:
            stream.seek(-min(before.st_size, 16), os.SEEK_END)
            if b"\xff\xd9" not in stream.read().rstrip(b"\x00\r\n \t")[-4:]:
                raise ProcessingError(f"JPEG appears truncated: {p.name}")
        after = p.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ProcessingError(f"Image changed while being checked: {p.name}")
        return ImageInfo(p, width, height, orientation, color_space, dpi, before.st_size, before.st_mtime_ns)
    except ProcessingError:
        raise
    except (OSError, ValueError, UnidentifiedImageError, MemoryError) as exc:
        raise ProcessingError(f"Cannot read JPEG '{p.name}': {exc}") from exc


def validate_images(paths: Iterable[str | Path], existing: Iterable[str | Path] = (),
                    progress: Progress | None = None,
                    cancel: Cancelled | None = None) -> tuple[list[ImageInfo], list[str]]:
    seen = {_real_key(Path(p)) for p in existing}
    accepted: list[ImageInfo] = []
    rejected: list[str] = []
    batch = list(paths)
    for i, raw in enumerate(batch):
        _check_cancel(cancel)
        path = Path(raw)
        key = _real_key(path)
        if key in seen:
            rejected.append(f"Duplicate ignored: {path.name}")
        elif path.is_dir():
            rejected.append(f"Folders are not supported: {path.name}. Drop JPG/JPEG files instead.")
        else:
            try:
                info = inspect_jpeg(path)
            except ProcessingError as exc:
                rejected.append(str(exc))
            else:
                seen.add(key)
                accepted.append(info)
        if progress:
            progress(i + 1, len(batch))
    _check_cancel(cancel)
    return accepted, rejected


def image_output_directory(paths: Iterable[str | Path]) -> Path | None:
    parents = [str(Path(p).absolute().parent) for p in paths]
    if not parents:
        return None
    try:
        common = Path(os.path.commonpath(parents))
    except ValueError:  # Windows: different drives
        return None
    if len(set(map(os.path.normcase, parents))) > 1 and common == Path(common.anchor):
        return None  # drive root is not a useful common destination
    return common if common.is_dir() and os.access(common, os.W_OK) else None


def natural_sort_key(path: str | Path) -> tuple:
    return tuple(int(part) if part.isdigit() else part.casefold()
                 for part in re.split(r"(\d+)", Path(path).name))


def _pdf_count(path: Path) -> int:
    try:
        with path.open("rb") as stream:
            reader = PdfReader(stream, strict=False)
            if reader.is_encrypted and not reader.decrypt(""):
                raise ProcessingError(f"Password-protected PDF: {path.name}")
            count = len(reader.pages)
            if not count:
                raise ProcessingError(f"PDF has no pages: {path.name}")
            return count
    except ProcessingError:
        raise
    except Exception as exc:
        raise ProcessingError(f"Cannot read PDF '{path.name}': {exc}") from exc


def scan_pdfs(folder: str | Path, progress: Progress | None = None,
              cancel: Cancelled | None = None) -> tuple[list[Path], list[str]]:
    root = Path(folder)
    if not root.is_dir():
        raise ProcessingError(f"Folder does not exist: {root}")
    found: list[Path] = []
    rejected: list[str] = []
    try:
        with os.scandir(root) as entries:
            for entry in entries:
                _check_cancel(cancel)
                if entry.name.startswith(".") or not entry.name.lower().endswith(".pdf"):
                    continue
                if re.fullmatch(r"merged_document(?:_\d+)?\.pdf", entry.name, re.I):
                    continue  # previous outputs are never re-imported
                try:
                    if not entry.is_file(follow_symlinks=False):
                        continue
                    attributes = getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0)
                    if attributes & (getattr(stat, "FILE_ATTRIBUTE_HIDDEN", 2) |
                                     getattr(stat, "FILE_ATTRIBUTE_SYSTEM", 4)):
                        continue
                    p = Path(entry.path)
                    _pdf_count(p)
                    found.append(p)
                except (OSError, ProcessingError) as exc:
                    rejected.append(f"Skipped {entry.name}: {exc}")
    except OSError as exc:
        raise ProcessingError(f"Cannot list PDF folder '{root}': {exc}") from exc
    _check_cancel(cancel)
    found.sort(key=natural_sort_key)
    return found, rejected


def _cleanup_temp(path: Path) -> None:
    # Cleanup is best effort: a locked file must not mask the primary error.
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _temp_path(folder: Path) -> Path:
    try:
        fd, name = tempfile.mkstemp(prefix=".pdf-tools-", suffix=".part", dir=folder)
        os.close(fd)
        return Path(name)
    except OSError as exc:
        raise ProcessingError(f"Cannot write temporary file in '{folder}': {exc}") from exc


def _publish_unique(tmp: Path, folder: Path, stem: str) -> Path:
    """Publish completed temp file, never overwrite (also under name races)."""
    for index in range(10000):
        name = f"{stem}{'_' + str(index) if index else ''}.pdf"
        dest = folder / name
        try:
            # A same-volume hard link makes final publication atomic and exclusive.
            os.link(tmp, dest)
            return dest
        except FileExistsError:
            continue
        except OSError as exc:
            if exc.errno not in {errno.EPERM, errno.EOPNOTSUPP, errno.ENOTSUP, errno.ENOSYS, errno.EXDEV}:
                raise ProcessingError(f"Cannot save output in '{folder}': {exc}") from exc
            # Fallback for filesystems without hardlinks; exclusive creation is
            # still enforced. On sudden power loss, this fallback can leave a
            # partial output; documented in README.
            try:
                with dest.open("xb") as output, tmp.open("rb") as source:
                    try:
                        shutil.copyfileobj(source, output, length=1024 * 1024)
                        output.flush()
                        os.fsync(output.fileno())
                    except BaseException:
                        output.close()
                        dest.unlink(missing_ok=True)
                        raise
                return dest
            except FileExistsError:
                continue
            except OSError as write_exc:
                raise ProcessingError(f"Cannot save output in '{folder}': {write_exc}") from write_exc
    raise ProcessingError("Too many existing output names in this folder.")


def _jpeg_dimensions(info: ImageInfo) -> tuple[float, float]:
    w, h = (info.width * 72 / info.dpi, info.height * 72 / info.dpi)
    if info.orientation in (5, 6, 7, 8):
        w, h = h, w
    scale = min(1, 14400 / max(w, h))  # PDF page size limit; no pixel resampling
    return w * scale, h * scale


def _matrix(orientation: int, w: float, h: float) -> str:
    transforms = {
        1: (1, 0, 0, 1, 0, 0),
        2: (-1, 0, 0, 1, 1, 0),
        3: (-1, 0, 0, -1, 1, 1),
        4: (1, 0, 0, -1, 0, 1),
        5: (0, -1, -1, 0, 1, 1),
        6: (0, -1, 1, 0, 0, 1),
        7: (0, 1, 1, 0, 0, 0),
        8: (0, 1, -1, 0, 1, 0),
    }
    a, b, c, d, e, f = transforms[orientation]
    return f"{a*w:.6f} {b*h:.6f} {c*w:.6f} {d*h:.6f} {e*w:.6f} {f*h:.6f} cm"


def _stream_pdf_jpegs(infos: list[ImageInfo], output: Path, progress: Progress | None, cancel: Cancelled | None) -> None:
    offsets: list[int] = [0]
    count = len(infos)
    with output.open("wb") as dst:
        dst.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")

        def obj(number: int, body: bytes) -> None:
            assert number == len(offsets)
            offsets.append(dst.tell())
            dst.write(f"{number} 0 obj\n".encode("ascii"))
            dst.write(body)
            dst.write(b"\nendobj\n")

        obj(1, b"<< /Type /Catalog /Pages 2 0 R >>")
        kids = " ".join(f"{3 + i*3} 0 R" for i in range(count))
        obj(2, f"<< /Type /Pages /Kids [{kids}] /Count {count} >>".encode("ascii"))
        for i, info in enumerate(infos):
            _check_cancel(cancel)
            current = inspect_jpeg(info.path, decode_preview=False)
            if (current.size, current.mtime_ns, current.width, current.height, current.orientation) != (
                info.size, info.mtime_ns, info.width, info.height, info.orientation
            ):
                raise ProcessingError(f"Image changed before conversion: {info.path.name}")
            page, image_obj, content_obj = 3 + i*3, 4 + i*3, 5 + i*3
            w, h = _jpeg_dimensions(info)
            obj(page, (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {w:.6f} {h:.6f}] "
                       f"/Resources << /XObject << /Im0 {image_obj} 0 R >> >> "
                       f"/Contents {content_obj} 0 R >>").encode("ascii"))
            offsets.append(dst.tell())
            dst.write(f"{image_obj} 0 obj\n".encode("ascii"))
            dst.write((f"<< /Type /XObject /Subtype /Image /Width {info.width} /Height {info.height} "
                       f"/ColorSpace {info.color_space} /BitsPerComponent 8 /Filter /DCTDecode "
                       f"/Length {info.size} >>\nstream\n").encode("ascii"))
            with info.path.open("rb") as source:
                # Copy original entropy-coded JPEG bytes; never decode or re-encode.
                remaining = info.size
                while remaining:
                    _check_cancel(cancel)
                    block = source.read(min(1024 * 1024, remaining))
                    if not block:
                        raise ProcessingError(f"JPEG changed during conversion: {info.path.name}")
                    dst.write(block)
                    remaining -= len(block)
            if (info.path.stat().st_size, info.path.stat().st_mtime_ns) != (info.size, info.mtime_ns):
                raise ProcessingError(f"JPEG changed during conversion: {info.path.name}")
            dst.write(b"\nendstream\nendobj\n")
            matrix = _matrix(info.orientation, w, h)
            content = f"q\n{matrix}\n/Im0 Do\nQ\n".encode("ascii")
            obj(content_obj, f"<< /Length {len(content)} >>\nstream\n".encode("ascii") + content + b"endstream")
            if progress:
                progress(i + 1, count)
        _check_cancel(cancel)
        xref = dst.tell()
        dst.write(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode("ascii"))
        for value in offsets[1:]:
            dst.write(f"{value:010d} 00000 n \n".encode("ascii"))
        dst.write((f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\n"
                   f"startxref\n{xref}\n%%EOF\n").encode("ascii"))
        dst.flush()
        os.fsync(dst.fileno())


def convert_images(paths: Iterable[str | Path], folder: str | Path | None = None,
                   progress: Progress | None = None, cancel: Cancelled | None = None) -> tuple[Path, int]:
    inputs = list(paths)
    if not inputs:
        raise ProcessingError("Add at least one JPEG image.")
    infos, rejected = validate_images(inputs, cancel=cancel)
    if rejected:
        raise ProcessingError("Invalid images: " + "; ".join(rejected))
    root = Path(folder) if folder else image_output_directory(inputs)
    if root is None:
        raise ProcessingError("Images are on different drives or no usable common folder exists. Choose an output folder.")
    if not root.is_dir():
        raise ProcessingError(f"Output folder not found: {root}")
    _check_cancel(cancel)
    temp = _temp_path(root)
    try:
        _stream_pdf_jpegs(infos, temp, progress, cancel)
        if _pdf_count(temp) != len(infos):
            raise ProcessingError("Output PDF verification failed: page count mismatch.")
        _check_cancel(cancel)
        return _publish_unique(temp, root, "converted_images"), len(infos)
    except OSError as exc:
        raise ProcessingError(f"JPEG-to-PDF conversion failed: {exc}") from exc
    finally:
        _cleanup_temp(temp)


def merge_pdfs(paths: Iterable[str | Path], folder: str | Path,
               progress: Progress | None = None, cancel: Cancelled | None = None) -> tuple[Path, int, int]:
    inputs = [Path(p) for p in paths]
    if len(inputs) < 2:
        raise ProcessingError("Choose at least two valid PDF files.")
    if len({_real_key(p) for p in inputs}) != len(inputs):
        raise ProcessingError("A PDF is listed more than once.")
    root = Path(folder)
    if not root.is_dir():
        raise ProcessingError(f"Output folder not found: {root}")
    if any(p.parent.resolve() != root.resolve() for p in inputs):
        raise ProcessingError("All PDFs must belong to the selected folder.")
    if any(re.fullmatch(r"merged_document(?:_\d+)?\.pdf", p.name, re.I) for p in inputs):
        raise ProcessingError("Previous merged outputs cannot be used as inputs.")
    _check_cancel(cancel)
    temp = _temp_path(root)
    writer = PdfWriter()
    pages = 0
    try:
        for index, path in enumerate(inputs):
            _check_cancel(cancel)
            try:
                with path.open("rb") as stream:
                    reader = PdfReader(stream, strict=False)
                    if reader.is_encrypted and not reader.decrypt(""):
                        raise ProcessingError(f"Password-protected PDF: {path.name}")
                    count = len(reader.pages)
                    if not count:
                        raise ProcessingError(f"PDF has no pages: {path.name}")
                    writer.append(reader, import_outline=False)
                    pages += count
            except ProcessingError:
                raise
            except Exception as exc:
                raise ProcessingError(f"Cannot merge PDF '{path.name}': {exc}") from exc
            if progress:
                progress(index + 1, len(inputs))
        _check_cancel(cancel)
        with temp.open("wb") as dst:
            writer.write(dst)
            dst.flush()
            os.fsync(dst.fileno())
        if _pdf_count(temp) != pages:
            raise ProcessingError("Merged PDF verification failed: page count mismatch.")
        _check_cancel(cancel)
        return _publish_unique(temp, root, "merged_document"), len(inputs), pages
    except OSError as exc:
        raise ProcessingError(f"PDF merge failed: {exc}") from exc
    finally:
        writer.close()
        temp.unlink(missing_ok=True)
