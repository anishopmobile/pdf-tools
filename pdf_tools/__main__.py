"""Application entry point; also works as a PyInstaller script entry point."""

from pdf_tools.gui import main

if __name__ == "__main__":
    raise SystemExit(main())
