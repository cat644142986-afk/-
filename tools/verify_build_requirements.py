"""Verify the exact Windows release-tool versions without shell quoting.

This is a file-based entry point because passing a multiline Python program to
``python -c`` through Windows PowerShell can strip the JSON-style quotes before
Python receives them.
"""

from importlib.metadata import version


EXPECTED = {
    "pyinstaller": "6.22.2",
    "pyinstaller-hooks-contrib": "2026.7",
}


def main() -> int:
    actual = {name: version(name) for name in EXPECTED}
    mismatches = [
        f"{name}={actual[name]} (expected {wanted})"
        for name, wanted in EXPECTED.items()
        if actual[name] != wanted
    ]
    if mismatches:
        print("; ".join(mismatches))
        return 1
    print(", ".join(f"{name}={actual[name]}" for name in EXPECTED))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
