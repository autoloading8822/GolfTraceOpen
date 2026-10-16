# Windows release build

Build with a Windows x64 Python virtual environment in `.venv`. The initial
environment was created using the Codex bundled Python 3.12 x64, without changing
the global Python installation or PATH. `requirements-lock.txt` records the exact
installed versions. Recreate it with `python -m venv .venv`, then install the lock
file using `.venv\Scripts\python.exe -m pip install -r requirements-lock.txt`.

Run `powershell -ExecutionPolicy Bypass -File scripts\setup_windows_tools.ps1`
once to download the official Inno Setup 7.1.0 x64 installer. The script verifies
Authenticode publisher `Pyrsys B.V.` and compares SHA-256 against the official
`.issig`, records the evidence under `tools/`, and installs Inno in project-local
portable mode. It does not change global PATH or create file associations.

After the source and tests are complete, run:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1
```

The script runs tests, collects third-party notices, creates the PyInstaller
one-directory application, compiles a per-user x64 installer, and writes:

- `release/GolfTraceOpen-Setup-0.1.0-win-x64.exe`
- `release/GolfTraceOpen-0.1.0-win-x64.zip`
- `release/SHA256SUMS.txt`

The zip preserves the executable beside its `_internal` shared-library directory.
Always keep the full directory together. The installer defaults to the current
user's local application directory and does not require administrator rights.
Build completion does not prove that installation or the GUI has been tested.

Official references checked on 2026-10-16:

- https://pyinstaller.org/en/stable/usage.html
- https://jrsoftware.org/isdl.php
- https://jrsoftware.org/isdl-verify.php
- https://jrsoftware.org/ishelp/topic_technotes.htm
- https://jrsoftware.org/ishelp/topic_setupcmdline.htm
- https://jrsoftware.org/ishelp/topic_compilercmdline.htm
- https://jrsoftware.org/ishelp/topic_isppcc.htm
- https://jrsoftware.org/ishelp/topic_setup_architecturesallowed.htm
- https://jrsoftware.org/ishelp/topic_setup_privilegesrequired.htm
