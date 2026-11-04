#!/usr/bin/env python3
"""Apply an idempotent TDMA pipe-count separation to the pinned Kumbung SDK.

The upstream ESB address-prefix table has eight entries. Keep
CONFIG_ESB_PIPE_COUNT at 8 for that physical address capacity, while allowing
the central TDMA scheduler to rotate across only the active split peripherals.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys


KCONFIG_SYMBOL = "config ESB_TDMA_PIPE_COUNT"
KCONFIG_ANCHOR = "choice ESB_POLLING_RATE_SEL"
KCONFIG_BLOCK = """config ESB_TDMA_PIPE_COUNT
\tint "Number of active TDMA scheduling pipes"
\trange 1 8
\tdefault 8
\thelp
\t  Number of active peripheral slots used by the TDMA scheduler.
\t  This is separate from ESB_PIPE_COUNT, which sizes the physical
\t  ESB address-prefix table and per-pipe storage.

"""

SOURCE_OLD = "\tctx.pipes = ESB_PIPE_COUNT;"
SOURCE_NEW = (
    "\t/* Keep physical ESB pipe capacity separate from active TDMA slots. */\n"
    "\tctx.pipes = CONFIG_ESB_TDMA_PIPE_COUNT;"
)


def patch_kconfig(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    if KCONFIG_SYMBOL in text:
        if KCONFIG_BLOCK.strip() not in text:
            raise RuntimeError(f"{path}: {KCONFIG_SYMBOL} exists but does not match expected patch")
        return
    if text.count(KCONFIG_ANCHOR) != 1:
        raise RuntimeError(f"{path}: expected exactly one {KCONFIG_ANCHOR!r} anchor")
    path.write_text(text.replace(KCONFIG_ANCHOR, KCONFIG_BLOCK + KCONFIG_ANCHOR, 1), encoding="utf-8")


def patch_source(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    if SOURCE_NEW in text:
        return
    if text.count(SOURCE_OLD) != 1:
        raise RuntimeError(f"{path}: expected exactly one TDMA pipe-count assignment")
    path.write_text(text.replace(SOURCE_OLD, SOURCE_NEW, 1), encoding="utf-8")


ZEPHYR_KCONFIG_SYMBOL = "config PSA_WANT_KEY_TYPE_ECC_KEY_PAIR_BASIC"
ZEPHYR_KCONFIG_OLD = """config PSA_WANT_KEY_TYPE_ECC_KEY_PAIR_BASIC
\\tbool
"""
ZEPHYR_KCONFIG_NEW = """config PSA_WANT_KEY_TYPE_ECC_KEY_PAIR_BASIC
\\tbool
\\tselect PSA_WANT_KEY_TYPE_ECC_PUBLIC_KEY
"""


def patch_zephyr_kconfig(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    if ZEPHYR_KCONFIG_SYMBOL not in text:
        raise RuntimeError(f"{path}: expected {ZEPHYR_KCONFIG_SYMBOL!r} symbol")
    if "select PSA_WANT_KEY_TYPE_ECC_PUBLIC_KEY" in text:
        return
    if text.count(ZEPHYR_KCONFIG_OLD) != 1:
        raise RuntimeError(f"{path}: expected exactly one ECC key-pair Kconfig block")
    path.write_text(text.replace(ZEPHYR_KCONFIG_OLD, ZEPHYR_KCONFIG_NEW, 1), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("sdk_nrf_root", type=Path, help="West checkout path of sdk-nrf")
    parser.add_argument("zephyr_root", type=Path, help="West checkout path of Zephyr")
    args = parser.parse_args()

    root = args.sdk_nrf_root
    zephyr_root = args.zephyr_root
    kconfig = root / "subsys" / "esb" / "Kconfig"
    source = root / "subsys" / "esb" / "esb.c"
    psa_kconfig = zephyr_root / "modules" / "mbedtls" / "Kconfig.psa.logic"
    for path in (kconfig, source, psa_kconfig):
        if not path.is_file():
            print(f"error: expected Kumbung SDK source file not found: {path}", file=sys.stderr)
            return 2

    try:
        patch_kconfig(kconfig)
        patch_source(source)
        patch_zephyr_kconfig(psa_kconfig)
    except (OSError, RuntimeError) as exc:
        print(f"error: failed to apply Kumbung TDMA pipe-count patch: {exc}", file=sys.stderr)
        return 1

    print("Applied/verified Kumbung TDMA pipe-count separation:")
    print(f"  {kconfig}")
    print(f"  {source}")
    print(f"  {psa_kconfig}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
