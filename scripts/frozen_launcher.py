"""Keep package-relative imports intact when started by PyInstaller."""

from golftrace.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
