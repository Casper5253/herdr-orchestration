There is duplicated CSV-ish parsing logic in `importer.py` and `exporter.py`: each module defines its own `parse_line` and its own quoted-field splitter. The behaviour is identical.

Please refactor: pull the shared logic into a single helper module (e.g. `parser.py`) inside this directory, and have `importer.py` and `exporter.py` import from it. Keep `parse_line`'s public signature (single string -> list[str]) and keep the existing public functions on each module working — `parse_text` on `importer` and `parse_log` on `exporter` should still parse line-by-line and skip blanks, but the actual row parsing should happen in the shared helper.

Requirements:
- No behaviour change. `parse_text` and `parse_log` should return the same rows for the same input as before.
- The duplication should be gone: only one place in the directory should hold the canonical quoted-splitting code, and the other module should not redefine it.
- Keep the import surface stable — `from importer import parse_line` and `from exporter import parse_line` must still work.
