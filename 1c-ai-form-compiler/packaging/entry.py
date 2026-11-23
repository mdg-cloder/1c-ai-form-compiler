"""PyInstaller entry point for the one-c-form-compiler executable."""

import multiprocessing
import sys

from one_c_form_compiler.cli import main

if __name__ == "__main__":
    # v8unpack uses a multiprocessing pool; worker processes re-run this executable.
    multiprocessing.freeze_support()
    sys.exit(main())
