# PDF Tools — JPG to PDF & Merge PDF (offline)

A small Windows desktop application built using **Python 3.12, PySide6, Pillow and pypdf**. All file processing is performed **on your computer**, without accounts, cloud uploads, telemetry, advertisements or a network connection at runtime. UI language: **English**.

> **Release status (2026-10-20):** Source and automated backend tests were prepared in a Linux environment. No Windows EXE/Setup has been built or tested here. The GitHub repository `anishopmobile/pdf-tools` was **empty** when inspected. This source is delivered separately for Windows verification; no commit/push or production release is claimed. After copying these files into the repository, the included Windows GitHub Actions workflow will execute on its first push.

## Features

**JPG to PDF:** Drag/drop or multi-select `.jpg` / `.jpeg` files, see their full paths and count, remove or reorder entries (drag rows), then create a single multi-page PDF. Original JPEG byte streams are embedded using `/DCTDecode` **without recompression or downscaling**. EXIF orientations 1–8 are implemented as page content transforms, without modifying images. Page sizes follow image dimensions and DPI (96 DPI fallback). Save beside source images, or at the closest useful shared parent; if none exists, pick a folder. Existing `converted_images.pdf` is not overwritten: suffixes `_1`, `_2`, etc. are generated.

**Merge PDF:** Pick a folder (no recursive scan), inspect valid PDF inputs, reorder them (default: natural filename order, e.g. `file2.pdf` before `file10.pdf`), and merge their pages without rasterization. Text usually remains searchable; original page sizes are preserved. Encrypted PDFs requiring passwords, unreadable PDFs, hidden/system files and generated output names are skipped. The result is `merged_document.pdf` or a unique suffixed name. A minimum of two PDFs is required.

Both operations use worker threads (no processing on the UI thread), a progress indicator, cancellation, error messages and Open PDF/Open Output Folder buttons. Outputs are staged in a temporary file in the destination directory, structurally validated with pypdf, then published under a **new exclusive filename**. Original inputs are never modified.

## Windows requirements and installation

- **For the built app / installer:** 64-bit Windows 10/11; no separate Python installation and no internet connection required to run PDF Tools. A PDF viewer is useful for the Open PDF action.
- **To build from source:** 64-bit Windows 10/11, [Python 3.12](https://www.python.org/downloads/windows/) with `py` launcher, internet connection for initial dependency installation, and [Inno Setup 6](https://jrsoftware.org/isinfo.php) for the Setup installer. PowerShell is included with Windows.
- The application is distributed as a **one-folder** PyInstaller package because Qt native plugins and DLLs are easier to diagnose and validate in this configuration. `PDFTools.exe` is not standalone when removed from its `dist/PDFTools/` folder. The installer bundles the entire folder and provides convenient installation/uninstallation.

Open **PowerShell** in the project root (the folder with `requirements.txt`) and run:

```powershell
.\scripts\build_windows.ps1
```

This creates `.venv`, installs pinned build dependencies, **runs automated tests (including Qt tests)**, compiles the sources, and builds `dist\PDFTools\PDFTools.exe`. If tests fail, the script refuses to build. To start the executable:

```powershell
.\dist\PDFTools\PDFTools.exe
```

Once the executable was checked on Windows and Inno Setup 6 was installed:

```powershell
.\scripts\build_installer.ps1
```

Expected installer output: `dist\installer\PDFTools-Setup.exe`. The installer uses a per-user default path (`%LOCALAPPDATA%\Programs\PDFTools`), creates a Start Menu entry, offers an optional desktop shortcut, and registers a normal Windows uninstaller under Settings > Apps > Installed apps. It does not require Administrator privileges in the normal per-user scenario. **These steps are instructions, not claims that a Windows build was already completed.**

## Testing (from source)

Run from the project root, inside a virtual environment with `requirements-build.txt` installed:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m compileall -q pdf_tools tests
```

On Windows, GitHub Actions additionally runs PyInstaller, a bundled EXE process launch smoke test, Inno Setup, a silent install → installed EXE launch → uninstall test, and publishes three artifacts: `PDFTools-Windows-portable`, `PDFTools-Windows-Setup`, and `PDFTools-Windows-Test-Results`. **Windows CI has not yet executed, since the repository contained no commits.**

On the authoring Linux environment, `30` core tests passed and GUI tests were **skipped because PySide6 was not installed**. No Windows executable/installer tests were possible there. These results do not establish Windows compatibility. Eight EXIF variants were additionally rendered and compared to Pillow's EXIF transforms in a Linux-only validation; all eight had low pixel differences.

## Quick use

1. Start **PDF Tools**. Select either **JPG to PDF** or **Merge PDF**.
2. For images: use **Add Images** or drag local JPEG files into the drop area. Drag list rows to adjust page order; use **Remove Selected** or **Clear List** if needed. Click **Convert to PDF**. A completed result shows the full output path, page count and **Open PDF**.
3. For PDFs: click **Select Folder**, review the naturally sorted list, drag to reorder, then **Merge PDFs**. The result shows both merged file count and page count. Select **New Operation** for a new batch.
4. **Cancel** asks the background processing thread to stop at its next cancellation checkpoint. For large PDF files, an active pypdf write may finish before cancellation can take effect; output is not published after a cancellation is acknowledged.

## Project layout

| Path | Purpose |
| --- | --- |
| `pdf_tools/core.py` | JPEG validation and lossless stream embedding; PDF validation, scanning, merging; atomic/exclusive output publishing |
| `pdf_tools/gui.py` | PySide6 screens, drag and drop, file dialogs, list reordering, workers, progress, errors |
| `pdf_tools/__main__.py` | Application entry point |
| `pdf_tools/__init__.py` | Version `1.0.0` |
| `tests/test_core.py` | PDF content, JPEG bytes/EXIF, filenames, failures, cancellations, encryption, ordering |
| `tests/test_gui.py` | Real Qt widget/event/worker integration tests (require PySide6) |
| `requirements.txt` | Pinned runtime dependencies |
| `requirements-build.txt` | Pinned PyInstaller and test dependencies |
| `assets/pdf-tools.ico` | App and installer icon |
| `packaging/version_info.txt` | Version/resource metadata for Windows EXE |
| `scripts/build_windows.ps1` | Test and build the PyInstaller one-folder EXE |
| `scripts/build_installer.ps1` | Build installer with Inno Setup 6 |
| `installer/PDFTools.iss` | Setup configuration and uninstall shortcuts |
| `.github/workflows/windows.yml` | Windows tests, EXE, smoke tests, installer and artifact uploads |
| `docs/WINDOWS_GUIDE_FA.md` | Comprehensive Persian walkthrough |
| `docs/MANUAL_TESTS_FA.md` | Windows manual acceptance checklist (not executed yet) |

## Known constraints

- Only **RGB** or **grayscale** JPEG input is supported; CMYK/YCCK JPEGs are rejected rather than silently altering colors. Other formats (`PNG`, `WEBP`) are not accepted. Input pixels and JPEG bytes are not resampled or recompressed. Unusual ICC/color-management profiles can display differently in various PDF viewers because embedded JPEG uses DeviceRGB/DeviceGray rather than a custom PDF ICCBased color space.
- JPEG corruption detection includes Pillow header checks, a reduced-resolution validation decode, and end-of-image checks, but **not an exhaustive full-resolution pixel-level analysis**; some nonstandard/damaged JPEG data may still be embedded. Test/view outputs as needed.
- PDF page geometry/content and text are preserved as pypdf is able to clone them. **Signatures become invalid after merging**; interactive forms, field names, outlines/bookmarks, links, tagged-PDF data, encrypted-file settings, advanced annotations and other advanced PDF features are not guaranteed to survive unchanged. `import_outline=False` intentionally excludes input outlines. Never treat a new merged file as a replacement for signed originals.
- The merge step is not fully streaming: large or complex PDFs may consume substantial RAM. JPEG-to-PDF uses 1 MiB streaming file copies, but the source needs to be readable and Pillow can still require temporary memory for parsing image metadata.
- For unusually large images, PDF page dimensions are uniformly limited to 14,400 points (no JPEG pixel resampling). DPI metadata is used when within 36–1,200; otherwise 96 DPI is assumed.
- Publication uses atomic, exclusive hard links when supported by the output filesystem. On filesystems without hard links, exclusive-create/copy is used; a crash/power loss during fallback copying can leave a partial new file (never an overwritten previous file). Temp files are normally removed on errors/cancellation; crashes/power loss may leave hidden `.pdf-tools-*.part` files.
- Windows Defender SmartScreen can warn on **unsigned** Setup files. Verify their origin and SHA-256 before running; do not bypass a warning blindly.

## Dependencies and licenses

- [PySide6](https://www.qt.io/qt-for-python): LGPLv3/GPLv3 or commercial license, subject to applicable Qt distribution obligations (including Qt DLL redistribution compliance).
- [Pillow](https://pillow.readthedocs.io/): HPND historical permission license.
- [pypdf](https://pypdf.readthedocs.io/): BSD-3-Clause.
- Build/test only: [PyInstaller](https://pyinstaller.org/) under GPL with an exception for bundling, [pytest](https://pytest.org/) under MIT, [Inno Setup](https://jrsoftware.org/) under its own license.

Review upstream license texts/redistribution conditions for any commercial distribution; the included source does not assert that code-signing or external license audits have been completed.

## Report a problem

Use the [repository issue tracker](https://github.com/anishopmobile/pdf-tools/issues), describing Windows version, application version, steps to reproduce, error text and whether source/EXE/Setup was used. **Do not attach private documents or personal images.** The source is open; no telemetry is sent by the program.

For more detailed Windows installation/build/troubleshooting, see **[docs/WINDOWS_GUIDE_FA.md](docs/WINDOWS_GUIDE_FA.md)**.
