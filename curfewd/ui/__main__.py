"""Module entrypoint for `python3 -m curfewd.ui`."""

import os
import sys

from curfewd.ui.app import main

if __name__ == "__main__":
    rc = int(main())
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(rc)
