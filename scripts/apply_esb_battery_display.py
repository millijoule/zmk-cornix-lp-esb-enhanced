#!/usr/bin/env python3
"""Enable safe ESB peripheral battery accounting and show L/R levels on the dongle OLED."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if new in text:
        return
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one patch anchor, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def patch_central_peripheral_count(zmk_root: Path) -> None:
    path = zmk_root / "app" / "include" / "zmk" / "split" / "central.h"
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

/* Kumbung ESB reuses this BLE-named setting as its peripheral count. */
#define BLE_PERIPHERAL_COUNT CONFIG_ZMK_SPLIT_BLE_CENTRAL_PERIPHERALS

#else

#define BLE_PERIPHERAL_COUNT 0

#endif
"""
    replace_once(path, old, new)


BATTERY_WIDGET_SOURCE = r'''/*
 * Copyright (c) 2020 The ZMK Contributors
 *
 * SPDX-License-Identifier: MIT
 */

#include <zephyr/kernel.h>

#include <zephyr/logging/log.h>
LOG_MODULE_DECLARE(zmk, CONFIG_ZMK_LOG_LEVEL);

#include <zmk/battery.h>
#include <zmk/display.h>
#include <zmk/display/widgets/battery_status.h>
#include <zmk/usb.h>
#include <zmk/events/usb_conn_state_changed.h>
#include <zmk/event_manager.h>
#include <zmk/events/battery_state_changed.h>

static sys_slist_t widgets = SYS_SLIST_STATIC_INIT(&widgets);

#if IS_ENABLED(CONFIG_ZMK_SPLIT_BLE_CENTRAL_BATTERY_LEVEL_FETCHING)
#define REMOTE_BATTERY_COUNT CONFIG_ZMK_SPLIT_BLE_CENTRAL_PERIPHERALS
struct battery_status_state {
    uint8_t levels[REMOTE_BATTERY_COUNT];
    bool valid[REMOTE_BATTERY_COUNT];
};
static uint8_t remote_levels[REMOTE_BATTERY_COUNT];
static bool remote_levels_valid[REMOTE_BATTERY_COUNT];
#else
struct battery_status_state {
    uint8_t level;
#if IS_ENABLED(CONFIG_USB_DEVICE_STACK)
    bool usb_present;
#endif
};
#endif

static void set_battery_symbol(lv_obj_t *label, struct battery_status_state state) {
#if IS_ENABLED(CONFIG_ZMK_SPLIT_BLE_CENTRAL_BATTERY_LEVEL_FETCHING)
    char left[5] = "--";
    char right[5] = "--";

    if (REMOTE_BATTERY_COUNT > 0 && state.valid[0]) {
        snprintf(left, sizeof(left), "%u%%", state.levels[0]);
    }
    if (REMOTE_BATTERY_COUNT > 1 && state.valid[1]) {
        snprintf(right, sizeof(right), "%u%%", state.levels[1]);
    }

    lv_label_set_text_fmt(label, "L:%s R:%s", left, right);
#else
    char text[9] = {};
    uint8_t level = state.level;

#if IS_ENABLED(CONFIG_USB_DEVICE_STACK)
    if (state.usb_present) {
        strcpy(text, LV_SYMBOL_CHARGE " ");
    }
#endif

#if IS_ENABLED(CONFIG_ZMK_WIDGET_BATTERY_STATUS_SHOW_PERCENTAGE)
    char perc[5] = {};
    snprintf(perc, sizeof(perc), "%3u%%", level);
    strcat(text, perc);
#else
    if (level > 95) {
        strcat(text, LV_SYMBOL_BATTERY_FULL);
    } else if (level > 65) {
        strcat(text, LV_SYMBOL_BATTERY_3);
    } else if (level > 35) {
        strcat(text, LV_SYMBOL_BATTERY_2);
    } else if (level > 5) {
        strcat(text, LV_SYMBOL_BATTERY_1);
    } else {
        strcat(text, LV_SYMBOL_BATTERY_EMPTY);
    }
#endif
    lv_label_set_text(label, text);
#endif
}

void battery_status_update_cb(struct battery_status_state state) {
    struct zmk_widget_battery_status *widget;
    SYS_SLIST_FOR_EACH_CONTAINER(&widgets, widget, node) {
        set_battery_symbol(widget->obj, state);
    }
}

static struct battery_status_state battery_status_get_state(const zmk_event_t *eh) {
#if IS_ENABLED(CONFIG_ZMK_SPLIT_BLE_CENTRAL_BATTERY_LEVEL_FETCHING)
    const struct zmk_peripheral_battery_state_changed *ev =
        as_zmk_peripheral_battery_state_changed(eh);

    if (ev != NULL && ev->source < REMOTE_BATTERY_COUNT) {
        remote_levels[ev->source] = ev->state_of_charge;
        remote_levels_valid[ev->source] = true;
    }

    struct battery_status_state state = {0};
    memcpy(state.levels, remote_levels, sizeof(remote_levels));
    memcpy(state.valid, remote_levels_valid, sizeof(remote_levels_valid));
    return state;
#else
    const struct zmk_battery_state_changed *ev = as_zmk_battery_state_changed(eh);

    return (struct battery_status_state){
        .level = (ev != NULL) ? ev->state_of_charge : zmk_battery_state_of_charge(),
#if IS_ENABLED(CONFIG_USB_DEVICE_STACK)
        .usb_present = zmk_usb_is_powered(),
#endif
    };
#endif
}

ZMK_DISPLAY_WIDGET_LISTENER(widget_battery_status, struct battery_status_state,
                            battery_status_update_cb, battery_status_get_state);

#if IS_ENABLED(CONFIG_ZMK_SPLIT_BLE_CENTRAL_BATTERY_LEVEL_FETCHING)
ZMK_SUBSCRIPTION(widget_battery_status, zmk_peripheral_battery_state_changed);
#else
ZMK_SUBSCRIPTION(widget_battery_status, zmk_battery_state_changed);
#if IS_ENABLED(CONFIG_USB_DEVICE_STACK)
ZMK_SUBSCRIPTION(widget_battery_status, zmk_usb_conn_state_changed);
#endif
#endif

int zmk_widget_battery_status_init(struct zmk_widget_battery_status *widget, lv_obj_t *parent) {
    widget->obj = lv_label_create(parent);
    sys_slist_append(&widgets, &widget->node);
    widget_battery_status_init();
    return 0;
}

lv_obj_t *zmk_widget_battery_status_obj(struct zmk_widget_battery_status *widget) {
    return widget->obj;
}
'''


def patch_battery_widget(zmk_root: Path) -> None:
    path = zmk_root / "app" / "src" / "display" / "widgets" / "battery_status.c"
    text = path.read_text(encoding="utf-8")
    if "REMOTE_BATTERY_COUNT" in text and "zmk_peripheral_battery_state_changed" in text:
        return
    if "ZMK_DISPLAY_WIDGET_LISTENER(widget_battery_status" not in text:
        raise RuntimeError(f"{path}: unexpected upstream battery widget source")
    path.write_text(BATTERY_WIDGET_SOURCE, encoding="utf-8")


def patch_esb_peripheral_reports(esb_root: Path) -> None:
    path = esb_root / "src" / "split" / "esb" / "peripheral.c"
    text = path.read_text(encoding="utf-8")
    if "esb_battery_state_changed_listener" in text:
        return

    replace_once(
        path,
        '#include <zmk/events/split_central_status_changed.h>\n',
        '#include <zmk/events/split_central_status_changed.h>\n'
        '#include <zmk/events/battery_state_changed.h>\n',
    )
    anchor = "bool is_central_connected = false;\n"
    insert = """bool is_central_connected = false;

static int esb_battery_state_changed_listener(const zmk_event_t *eh) {
    if (as_zmk_battery_state_changed(eh) == NULL) {
        return ZMK_EV_EVENT_BUBBLE;
    }

    /* Forward changed battery percentages while the ESB central is connected. */
    if (is_central_connected) {
        k_work_reschedule(&battery_report_work, K_NO_WAIT);
    }

    return ZMK_EV_EVENT_BUBBLE;
}

ZMK_LISTENER(esb_battery_state_changed, esb_battery_state_changed_listener);
ZMK_SUBSCRIPTION(esb_battery_state_changed, zmk_battery_state_changed);
"""
    replace_once(path, anchor, insert)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("zmk_root", type=Path)
    parser.add_argument("esb_root", type=Path)
    args = parser.parse_args()

    required = [
        args.zmk_root / "app/include/zmk/split/central.h",
        args.zmk_root / "app/src/display/widgets/battery_status.c",
        args.esb_root / "src/split/esb/peripheral.c",
    ]
    for path in required:
        if not path.is_file():
            print(f"error: missing expected source file: {path}", file=sys.stderr)
            return 2

    try:
        patch_central_peripheral_count(args.zmk_root)
        patch_battery_widget(args.zmk_root)
        patch_esb_peripheral_reports(args.esb_root)
    except (OSError, RuntimeError) as exc:
        print(f"error: failed to apply ESB battery display patch: {exc}", file=sys.stderr)
        return 1

    print("Applied ESB remote battery count, L/R OLED widget, and battery-change forwarding.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
