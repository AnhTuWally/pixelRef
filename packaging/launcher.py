"""Entry point of the standalone executables (pixelRef.exe and pixelref-cli.exe)."""

import multiprocessing

from pixelref.__main__ import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
