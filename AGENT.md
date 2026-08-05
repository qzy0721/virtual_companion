# Windows execution rules

This project runs on native Windows using PowerShell.

## Shell requirements

- Use PowerShell syntax only.
- Do not use Bash syntax unless explicitly invoking WSL.
- Do not use `&&`; execute commands separately or use PowerShell-compatible control flow.
- Do not use `export`; use `$env:NAME = "value"`.
- Do not use `rm -rf`; use `Remove-Item -Recurse -Force`.
- Do not use `cp`, `mv`, `touch`, `grep`, `sed`, `awk`, or `which`.
- Use PowerShell cmdlets or cross-platform program commands instead.
- Quote Windows paths with spaces.
- Prefer `Join-Path` instead of manually concatenating paths.
- Before running a complex command, verify that it is valid PowerShell.
- For commands longer than one line, create a temporary `.ps1` script instead of constructing deeply nested inline quoting.
- After a command fails, read the complete error before trying a different command.
- Do not repeatedly retry the same command with minor quoting changes.

## Preferred equivalents

- `which tool` -> `Get-Command tool`
- `export NAME=value` -> `$env:NAME = "value"`
- `rm -rf path` -> `Remove-Item -LiteralPath path -Recurse -Force`
- `cp -r src dst` -> `Copy-Item src dst -Recurse`
- `mv src dst` -> `Move-Item src dst`
- `cat file` -> `Get-Content file`
- `grep text file` -> `Select-String -Pattern "text" -Path file`
- `mkdir -p path` -> `New-Item -ItemType Directory -Force -Path path`
- `/dev/null` -> `$null`
- `command > file 2>&1` -> `command *> file`

## Python interpreter (critical)

- **Direct `python` is not available.** Always use the explicit interpreter path:  
  `D:\Anaconda\envs\indextts\python.exe`
- Call it with the call operator `&` and double-quote the path to handle spaces:
  ```powershell
  & "D:\Anaconda\envs\indextts\python.exe" script.py
  ```
- For installing packages, use the interpreter to run `pip` as a module:
  ```powershell
  & "D:\Anaconda\envs\indextts\python.exe" -m pip install <package>
  ```
- **Do not activate the conda environment** (`conda activate indextts`) – the absolute path is sufficient and avoids initialization issues in non-interactive shells.

## Path and quoting rules (Windows-specific)

- Any path containing spaces **must** be double-quoted.
- Use single quotes for literal strings that contain backslashes to avoid escape interpretation (e.g., `'C:\Program Files\SomeApp'`).
- When passing paths as arguments to Python, prefer using double-quotes around the entire argument or use `--arg="value"` style.
- Avoid manual string concatenation; use `Join-Path` for constructing file system paths.

## Execution policy

- If you need to run a `.ps1` script and get an execution policy error, use:
  ```powershell
  powershell -ExecutionPolicy Bypass -File script.ps1
  ```
  (Do not change the system-wide policy.)

## Error handling and encoding

- Check `$LASTEXITCODE` after running external commands; non‑zero indicates failure.
- If you encounter garbled output (especially with Chinese characters), set the console code page before running Python:
  ```powershell
  chcp 65001
  ```

## Temporary script guidance

- When a command becomes too long or requires multiple lines (e.g., nested loops, conditionals), write the logic into a temporary `.ps1` file and execute it with `& .\temp.ps1`.
- Always clean up temporary scripts after successful execution unless they are part of the project source.