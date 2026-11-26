"""Allow running `python3 -m curfewd.tray`."""

from curfewd.tray.app import main

if __name__ == "__main__":
    raise SystemExit(main())
