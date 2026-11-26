"""Allow running `python3 -m curfewd.cli`."""

from curfewd.cli.main import main

if __name__ == "__main__":
    raise SystemExit(main())
