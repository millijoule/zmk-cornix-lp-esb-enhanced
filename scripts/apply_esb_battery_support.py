#!/usr/bin/env python3
"""Patch pinned ZMK and dongle-display sources for Kumbung ESB battery reports.

The dongle uses a deliberately small horizontal status screen: USB HID status
on the left and the two remote keyboard battery levels on the right.
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

    # Compile the remote battery widget even though the USB dongle has no battery.
    replace_once(
        shield / "CMakeLists.txt",
        "    if (CONFIG_ZMK_BATTERY)\n        zephyr_library_sources(widgets/battery_status.c)\n    endif()",
        "    if (CONFIG_ZMK_BATTERY OR CONFIG_ZMK_SPLIT_BLE_CENTRAL_BATTERY_LEVEL_FETCHING)\n"
        "        zephyr_library_sources(widgets/battery_status.c)\n"
        "    endif()",
    )

    battery_file = shield / "widgets" / "battery_status.c"
    replace_once(battery_file, "#include <zmk/ble.h>", "#include <zmk/split/central.h>")
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

    # Label source 0 as LEFT and source 1 as RIGHT.
    replace_once(
        battery_file,
        'lv_label_set_text_fmt(label, "%4u%% ", state.level);',
        'lv_label_set_text_fmt(label, "%c %u%%", state.source == 0 ? \'L\' : \'R\', state.level);',
    )

    # The dongle has no local battery sensor; guard the local-battery accessor.
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

    # Replace the stock dashboard with a minimal horizontal USB + two-battery view.
    screen = shield / "custom_status_screen.c"
    minimal_screen = r'''/*
 * Minimal horizontal status screen for the Cornix ESB USB dongle.
 * Left: USB HID readiness. Right: left/right peripheral battery levels.
 */
#include "custom_status_screen.h"
#include "widgets/output_status.h"
#include "widgets/battery_status.h"

#include <zephyr/logging/log.h>
LOG_MODULE_DECLARE(zmk, CONFIG_ZMK_LOG_LEVEL);

static struct zmk_widget_output_status output_status_widget;
#if IS_ENABLED(CONFIG_ZMK_SPLIT_BLE_CENTRAL_BATTERY_LEVEL_FETCHING)
static struct zmk_widget_dongle_battery_status battery_status_widget;
#endif

lv_style_t global_style;

lv_obj_t *zmk_display_status_screen(void) {
    lv_obj_t *screen = lv_obj_create(NULL);

    lv_style_init(&global_style);
    lv_style_set_bg_color(&global_style, lv_color_white());
    lv_style_set_bg_opa(&global_style, LV_OPA_COVER);
    lv_style_set_text_color(&global_style, lv_color_black());
    lv_style_set_text_font(&global_style, &lv_font_unscii_8);
    lv_style_set_text_letter_space(&global_style, 1);
    lv_style_set_text_line_space(&global_style, 1);
    lv_obj_add_style(screen, &global_style, LV_PART_MAIN);

    zmk_widget_output_status_init(&output_status_widget, screen);
    lv_obj_set_pos(zmk_widget_output_status_obj(&output_status_widget), 2, 8);

    /* Keep the USB icon and HID-ready indicator; remove all Bluetooth artwork. */
    lv_obj_add_flag(lv_obj_get_child(zmk_widget_output_status_obj(&output_status_widget), 2),
                    LV_OBJ_FLAG_HIDDEN);
    lv_obj_add_flag(lv_obj_get_child(zmk_widget_output_status_obj(&output_status_widget), 3),
                    LV_OBJ_FLAG_HIDDEN);
    lv_obj_add_flag(lv_obj_get_child(zmk_widget_output_status_obj(&output_status_widget), 4),
                    LV_OBJ_FLAG_HIDDEN);
    lv_obj_add_flag(lv_obj_get_child(zmk_widget_output_status_obj(&output_status_widget), 5),
                    LV_OBJ_FLAG_HIDDEN);

#if IS_ENABLED(CONFIG_ZMK_SPLIT_BLE_CENTRAL_BATTERY_LEVEL_FETCHING)
    zmk_widget_dongle_battery_status_init(&battery_status_widget, screen);
    lv_obj_set_pos(zmk_widget_dongle_battery_status_obj(&battery_status_widget), 42, 5);
#endif

    return screen;
}
'''
    current = screen.read_text(encoding="utf-8")
    if current != minimal_screen:
        screen.write_text(minimal_screen, encoding="utf-8")


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
        args.display_module_root / "boards" / "shields" / "dongle_display" / "custom_status_screen.c",
        args.display_module_root / "boards" / "shields" / "dongle_display" / "widgets" / "battery_status.c",
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

    print("Applied ESB battery support and minimal horizontal USB/battery display.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
