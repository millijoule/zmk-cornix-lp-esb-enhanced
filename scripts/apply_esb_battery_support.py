#!/usr/bin/env python3
"""Patch the pinned ZMK/englmaxi display modules for Kumbung ESB battery reports.

The upstream ZMK split-central count is BLE/Wired-only, while this firmware
uses ESB. The upstream dongle-display widget also sizes its peripheral battery
array from the BLE-only count and only compiles it when a local battery sensor
is enabled. This patch adapts those paths to the ESB peripheral count and the
remote battery-event feature.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys


def replace_once(path: Path, old: str, new: str, *, expected: int = 1) -> None:
    text = path.read_text(encoding="utf-8")
    if new in text:
        return
    found = text.count(old)
    if found != expected:
        raise RuntimeError(
            f"{path}: expected {expected} occurrence(s) of patch anchor; found {found}"
        )
    path.write_text(text.replace(old, new), encoding="utf-8")


def patch_zmk_central_count(zmk_root: Path) -> None:
    path = zmk_root / "include" / "zmk" / "split" / "central.h"
    old = """#if IS_ENABLED(CONFIG_ZMK_SPLIT_BLE)

#include <zmk/ble.h>
#define BLE_PERIPHERAL_COUNT ZMK_SPLIT_BLE_PERIPHERAL_COUNT

#else

#define BLE_PERIPHERAL_COUNT 0

#endif
"""
    new = """#if IS_ENABLED(CONFIG_ZMK_SPLIT_BLE)

#include <zmk/ble.h>
#define BLE_PERIPHERAL_COUNT ZMK_SPLIT_BLE_PERIPHERAL_COUNT

#elif IS_ENABLED(CONFIG_ZMK_SPLIT_ESB)

/* The Kumbung ESB transport reuses this config value for its peripheral count. */
#define BLE_PERIPHERAL_COUNT CONFIG_ZMK_SPLIT_BLE_CENTRAL_PERIPHERALS

#else

#define BLE_PERIPHERAL_COUNT 0

#endif
"""
    replace_once(path, old, new)


def patch_zmk_battery_event_cmake(zmk_root: Path) -> None:
    path = zmk_root / "CMakeLists.txt"
    old = "target_sources_ifdef(CONFIG_ZMK_BATTERY_REPORTING app PRIVATE src/events/battery_state_changed.c)"
    new = """if(CONFIG_ZMK_BATTERY_REPORTING OR CONFIG_ZMK_SPLIT_BLE_CENTRAL_BATTERY_LEVEL_FETCHING)
    target_sources(app PRIVATE src/events/battery_state_changed.c)
endif()"""
    replace_once(path, old, new)


def patch_display_module(display_root: Path) -> None:
    shield = display_root / "boards" / "shields" / "dongle_display"

    # Build the battery widget when remote battery events are enabled, even if
    # the USB dongle itself has no battery sensor.
    replace_once(
        shield / "CMakeLists.txt",
        "    if (CONFIG_ZMK_BATTERY)\n        zephyr_library_sources(widgets/battery_status.c)\n    endif()",
        "    if (CONFIG_ZMK_BATTERY OR CONFIG_ZMK_SPLIT_BLE_CENTRAL_BATTERY_LEVEL_FETCHING)\n"
        "        zephyr_library_sources(widgets/battery_status.c)\n"
        "    endif()",
    )

    # Instantiate the widget on the USB central for remote battery events.
    replace_once(
        shield / "custom_status_screen.c",
        "#if IS_ENABLED(CONFIG_ZMK_BATTERY)",
        "#if IS_ENABLED(CONFIG_ZMK_BATTERY) || "
        "IS_ENABLED(CONFIG_ZMK_SPLIT_BLE_CENTRAL_BATTERY_LEVEL_FETCHING)",
        expected=2,
    )

    battery_file = shield / "widgets" / "battery_status.c"
    replace_once(
        battery_file,
        "#include <zmk/ble.h>",
        "#include <zmk/split/central.h>",
    )
    replace_once(
        battery_file,
        """#ifndef ZMK_SPLIT_BLE_PERIPHERAL_COUNT
#  define ZMK_SPLIT_BLE_PERIPHERAL_COUNT 0
#endif

""",
        "",
    )

    text = battery_file.read_text(encoding="utf-8")
    if "ZMK_SPLIT_BLE_PERIPHERAL_COUNT" in text:
        text = text.replace(
            "ZMK_SPLIT_BLE_PERIPHERAL_COUNT",
            "ZMK_SPLIT_CENTRAL_PERIPHERAL_COUNT",
        )
        battery_file.write_text(text, encoding="utf-8")

    # In a BLE-disabled ESB dongle, the local dongle-battery option is off.
    # Keep the local-battery accessor out of the build to avoid referencing
    # zmk_battery_state_of_charge() when local battery reporting is disabled.
    old_getters = """static struct battery_state central_battery_status_get_state(const zmk_event_t *eh) {
    const struct zmk_battery_state_changed *ev = as_zmk_battery_state_changed(eh);
    return (struct battery_state) {
        .source = 0,
        .level = (ev != NULL) ? ev->state_of_charge : zmk_battery_state_of_charge(),
#if IS_ENABLED(CONFIG_USB_DEVICE_STACK)
        .usb_present = zmk_usb_is_powered(),
#endif /* IS_ENABLED(CONFIG_USB_DEVICE_STACK) */
    };
}

static struct battery_state battery_status_get_state(const zmk_event_t *eh) { 
    if (as_zmk_peripheral_battery_state_changed(eh) != NULL) {
        return peripheral_battery_status_get_state(eh);
    } else {
        return central_battery_status_get_state(eh);
    }
}
"""
    new_getters = """#if IS_ENABLED(CONFIG_ZMK_DONGLE_DISPLAY_DONGLE_BATTERY)
static struct battery_state central_battery_status_get_state(const zmk_event_t *eh) {
    const struct zmk_battery_state_changed *ev = as_zmk_battery_state_changed(eh);
    return (struct battery_state) {
        .source = 0,
        .level = (ev != NULL) ? ev->state_of_charge : zmk_battery_state_of_charge(),
#if IS_ENABLED(CONFIG_USB_DEVICE_STACK)
        .usb_present = zmk_usb_is_powered(),
#endif /* CONFIG_USB_DEVICE_STACK */
    };
}
#endif /* CONFIG_ZMK_DONGLE_DISPLAY_DONGLE_BATTERY */

static struct battery_state battery_status_get_state(const zmk_event_t *eh) {
    if (as_zmk_peripheral_battery_state_changed(eh) != NULL) {
        return peripheral_battery_status_get_state(eh);
    }
#if IS_ENABLED(CONFIG_ZMK_DONGLE_DISPLAY_DONGLE_BATTERY)
    return central_battery_status_get_state(eh);
#else
    return (struct battery_state){0};
#endif
}
"""
    replace_once(battery_file, old_getters, new_getters)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("zmk_root", type=Path, help="West checkout path of ZMK")
    parser.add_argument(
        "display_module_root", type=Path, help="West checkout path of zmk-dongle-display"
    )
    args = parser.parse_args()

    required = [
        args.zmk_root / "include" / "zmk" / "split" / "central.h",
        args.zmk_root / "CMakeLists.txt",
        args.display_module_root / "boards" / "shields" / "dongle_display" / "CMakeLists.txt",
        args.display_module_root
        / "boards"
        / "shields"
        / "dongle_display"
        / "custom_status_screen.c",
        args.display_module_root
        / "boards"
        / "shields"
        / "dongle_display"
        / "widgets"
        / "battery_status.c",
    ]
    for path in required:
        if not path.is_file():
            print(f"error: expected source file not found: {path}", file=sys.stderr)
            return 2

    try:
        patch_zmk_central_count(args.zmk_root)
        patch_zmk_battery_event_cmake(args.zmk_root)
        patch_display_module(args.display_module_root)
    except (OSError, RuntimeError) as exc:
        print(f"error: failed to apply ESB battery/display patch: {exc}", file=sys.stderr)
        return 1

    print("Applied/verified ESB battery event and dongle-display compatibility patch.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
