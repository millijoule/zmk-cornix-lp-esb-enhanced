#!/usr/bin/env python3
"""Rotate the Cornix dongle SH1106 OLED image 180 degrees for inverted mounting."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys


OLD = """        // comment to rotate 180
        segment-remap;
        com-invdir;
"""

NEW = """        // The dongle is mounted upside down. Disable both scan-direction
        // options so the SH1106 renders the image rotated by 180 degrees.
        // segment-remap;
        // com-invdir;
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("cornix_root", type=Path)
    args = parser.parse_args()

    overlay = (
        args.cornix_root
        / "boards"
        / "shields"
        / "cornix_dongle_eyelash"
        / "cornix_dongle_eyelash.overlay"
    )

    if not overlay.is_file():
        print(f"error: missing expected Cornix OLED overlay: {overlay}", file=sys.stderr)
        return 2

    try:
        content = overlay.read_text(encoding="utf-8")
        if NEW in content:
            print("Cornix dongle OLED is already configured for 180-degree rotation.")
            return 0

        occurrences = content.count(OLD)
        if occurrences != 1:
            raise RuntimeError(
                f"{overlay}: expected one original orientation block, found {occurrences}"
            )

        overlay.write_text(content.replace(OLD, NEW, 1), encoding="utf-8")
    except (OSError, RuntimeError) as exc:
        print(f"error: failed to rotate Cornix dongle OLED: {exc}", file=sys.stderr)
        return 1

    print("Configured Cornix dongle SH1106 OLED for 180-degree rotation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
