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
 * Top: USB HID readiness and left/right peripheral batteries.
 * Bottom: counters at each stage of the ESB -> ZMK HID -> USB path.
 */
#include "custom_status_screen.h"
#include "widgets/output_status.h"
#include "widgets/battery_status.h"

#include <app_esb.h>
#include <zephyr/logging/log.h>
LOG_MODULE_DECLARE(zmk, CONFIG_ZMK_LOG_LEVEL);

static struct zmk_widget_output_status output_status_widget;
#if IS_ENABLED(CONFIG_ZMK_SPLIT_BLE_CENTRAL_BATTERY_LEVEL_FETCHING)
static struct zmk_widget_dongle_battery_status battery_status_widget;
#endif

static lv_obj_t *diag_label;
lv_style_t global_style;

static void update_diag_label(lv_timer_t *timer) {
    if (diag_label == NULL) {
        return;
    }

    struct zmk_esb_diag_snapshot s = zmk_esb_diag_get_snapshot();
    lv_label_set_text_fmt(
        diag_label,
        "RF%02u RX%02u\nKP%02u EV%02u\nLA%02uQ%02uS%02uF%02u\nRA%02uQ%02uS%02uF%02u\nU%02u/%02u N%u E%ld",
        (unsigned int)(s.radio_rx % 100),
        (unsigned int)(s.rx_packets % 100),
        (unsigned int)(s.key_positions % 100),
        (unsigned int)(s.keycode_events % 100),
        (unsigned int)(s.remote[0].key_tx_attempts % 100),
        (unsigned int)(s.remote[0].key_tx_queue_errors % 100),
        (unsigned int)(s.remote[0].tx_successes % 100),
        (unsigned int)(s.remote[0].tx_failures % 100),
        (unsigned int)(s.remote[1].key_tx_attempts % 100),
        (unsigned int)(s.remote[1].key_tx_queue_errors % 100),
        (unsigned int)(s.remote[1].tx_successes % 100),
        (unsigned int)(s.remote[1].tx_failures % 100),
        (unsigned int)(s.usb_successes % 100),
        (unsigned int)(s.usb_attempts % 100),
        (unsigned int)(s.no_endpoint_reports % 10),
        (long)s.usb_last_error
    );
}

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

    diag_label = lv_label_create(screen);
    lv_obj_set_pos(diag_label, 2, 22);
    lv_obj_set_size(diag_label, 124, 42);
    lv_label_set_long_mode(diag_label, LV_LABEL_LONG_CLIP);
    lv_obj_add_style(diag_label, &global_style, LV_PART_MAIN);
    update_diag_label(NULL);
    lv_timer_create(update_diag_label, 250, NULL);

    return screen;
}
'''
    current = screen.read_text(encoding="utf-8")
    if current != minimal_screen:
        screen.write_text(minimal_screen, encoding="utf-8")



def patch_esb_diagnostics(zmk_root: Path, esb_root: Path) -> None:
    """Expose counters for each stage of the ESB -> HID -> USB input path."""
    header = esb_root / "include" / "app_esb.h"
    replace_once(
        header,
        "extern struct esb_context *esb_ctx;\n",
        """extern struct esb_context *esb_ctx;

struct zmk_esb_diag_snapshot {
    uint32_t radio_rx;
    uint32_t rx_packets;
    uint32_t key_packets;
    uint32_t key_positions;
    uint32_t keycode_events;
    uint32_t no_endpoint_reports;
    uint32_t usb_attempts;
    uint32_t usb_successes;
    uint32_t usb_errors;
    int32_t usb_last_error;
};

void zmk_esb_diag_note_radio_rx(void);
void zmk_esb_diag_note_rx_packet(void);
void zmk_esb_diag_note_key_packet(void);
void zmk_esb_diag_note_key_position(void);
void zmk_esb_diag_note_keycode_event(void);
void zmk_esb_diag_note_no_endpoint(void);
void zmk_esb_diag_note_usb_result(int err);
struct zmk_esb_diag_snapshot zmk_esb_diag_get_snapshot(void);
""",
    )

    app_esb = esb_root / "src" / "split" / "esb" / "app_esb.c"
    replace_once(
        app_esb,
        "struct esb_context *esb_ctx;\n",
        """struct esb_context *esb_ctx;

#include <zephyr/sys/atomic.h>

static atomic_t diag_radio_rx;
static atomic_t diag_rx_packets;
static atomic_t diag_key_packets;
static atomic_t diag_key_positions;
static atomic_t diag_keycode_events;
static atomic_t diag_no_endpoint_reports;
static atomic_t diag_usb_attempts;
static atomic_t diag_usb_successes;
static atomic_t diag_usb_errors;
static atomic_t diag_usb_last_error;

void zmk_esb_diag_note_radio_rx(void) { atomic_inc(&diag_radio_rx); }
void zmk_esb_diag_note_rx_packet(void) { atomic_inc(&diag_rx_packets); }
void zmk_esb_diag_note_key_packet(void) { atomic_inc(&diag_key_packets); }
void zmk_esb_diag_note_key_position(void) { atomic_inc(&diag_key_positions); }
void zmk_esb_diag_note_keycode_event(void) { atomic_inc(&diag_keycode_events); }
void zmk_esb_diag_note_no_endpoint(void) { atomic_inc(&diag_no_endpoint_reports); }

void zmk_esb_diag_note_usb_result(int err) {
    atomic_inc(&diag_usb_attempts);
    if (err == 0) {
        atomic_inc(&diag_usb_successes);
    } else {
        atomic_inc(&diag_usb_errors);
        atomic_set(&diag_usb_last_error, err);
    }
}

struct zmk_esb_diag_snapshot zmk_esb_diag_get_snapshot(void) {
    return (struct zmk_esb_diag_snapshot) {
        .radio_rx = (uint32_t)atomic_get(&diag_radio_rx),
        .rx_packets = (uint32_t)atomic_get(&diag_rx_packets),
        .key_packets = (uint32_t)atomic_get(&diag_key_packets),
        .key_positions = (uint32_t)atomic_get(&diag_key_positions),
        .keycode_events = (uint32_t)atomic_get(&diag_keycode_events),
        .no_endpoint_reports = (uint32_t)atomic_get(&diag_no_endpoint_reports),
        .usb_attempts = (uint32_t)atomic_get(&diag_usb_attempts),
        .usb_successes = (uint32_t)atomic_get(&diag_usb_successes),
        .usb_errors = (uint32_t)atomic_get(&diag_usb_errors),
        .usb_last_error = (int32_t)atomic_get(&diag_usb_last_error),
    };
}
""",
    )
    replace_once(
        app_esb,
        '    case ESB_EVENT_RX_RECEIVED:\n        LOG_DBG("RX SUCCESS");',
        '    case ESB_EVENT_RX_RECEIVED:\n'
        '        zmk_esb_diag_note_radio_rx();\n'
        '        LOG_DBG("RX SUCCESS");',
    )

    central = esb_root / "src" / "split" / "esb" / "central.c"
    replace_once(
        central,
        "            int changed_bit = __builtin_ctz(changed);\n",
        "            int changed_bit = __builtin_ctz(changed);\n"
        "            zmk_esb_diag_note_key_position();\n",
    )
    replace_once(
        central,
        "    uint8_t type = data[0];\n    ssize_t data_size = get_payload_data_size_evt(type);",
        "    zmk_esb_diag_note_rx_packet();\n"
        "    uint8_t type = data[0];\n"
        "    if (type == ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_KEY_POSITION_EVENT) {\n"
        "        zmk_esb_diag_note_key_packet();\n"
        "    }\n"
        "    ssize_t data_size = get_payload_data_size_evt(type);",
    )

    hid_listener = zmk_root / "src" / "hid_listener.c"
    replace_once(
        hid_listener,
        "#include <drivers/behavior.h>\n",
        "#include <drivers/behavior.h>\n#include <app_esb.h>\n",
    )
    replace_once(
        hid_listener,
        "    if (ev) {\n        if (ev->state) {",
        "    if (ev) {\n"
        "        zmk_esb_diag_note_keycode_event();\n"
        "        if (ev->state) {",
    )

    endpoints = zmk_root / "src" / "endpoints.c"
    replace_once(
        endpoints,
        """static int send_keyboard_report(void) {
    switch (current_instance.transport) {
    case ZMK_TRANSPORT_NONE:
        return 0;""",
        """static int send_keyboard_report(void) {
    switch (current_instance.transport) {
    case ZMK_TRANSPORT_NONE:
        zmk_esb_diag_note_no_endpoint();
        return 0;""",
    )
    replace_once(
        endpoints,
        "#include <zmk/hid.h>\n",
        "#include <zmk/hid.h>\n#include <app_esb.h>\n",
    )

    usb_hid = zmk_root / "src" / "usb_hid.c"
    replace_once(
        usb_hid,
        "#include <zmk/keymap.h>\n",
        "#include <zmk/keymap.h>\n#include <app_esb.h>\n",
    )
    replace_once(
        usb_hid,
        """int zmk_usb_hid_send_keyboard_report(void) {
    size_t len;
    uint8_t *report = get_keyboard_report(&len);
    return zmk_usb_hid_send_report(report, len);
}""",
        """int zmk_usb_hid_send_keyboard_report(void) {
    size_t len;
    uint8_t *report = get_keyboard_report(&len);
    int err = zmk_usb_hid_send_report(report, len);
    zmk_esb_diag_note_usb_result(err);
    return err;
}""",
    )




def patch_peripheral_diagnostics(esb_root: Path) -> None:
    """Report peripheral key-send queue and radio TX outcomes to the dongle."""
    header = esb_root / "include" / "app_esb.h"
    if "ZMK_SPLIT_ESB_DIAG_EVENT_TYPE" not in header.read_text(encoding="utf-8"):
        replace_once(
            header,
            "struct zmk_esb_diag_snapshot {\n",
            """#define ZMK_SPLIT_ESB_DIAG_EVENT_TYPE 0x7e

struct zmk_esb_peripheral_diag {
    uint16_t key_tx_attempts;
    uint16_t key_tx_queue_errors;
    uint16_t tx_successes;
    uint16_t tx_failures;
} __packed;

struct zmk_esb_diag_snapshot {
""",
        )
        replace_once(
            header,
            "    int32_t usb_last_error;\n};\n",
            """    int32_t usb_last_error;
    struct zmk_esb_peripheral_diag remote[2];
};
""",
        )
        replace_once(
            header,
            "struct zmk_esb_diag_snapshot zmk_esb_diag_get_snapshot(void);\n",
            """struct zmk_esb_diag_snapshot zmk_esb_diag_get_snapshot(void);
void zmk_esb_diag_note_key_tx(int err);
void zmk_esb_diag_note_tx_result(int err);
void zmk_esb_diag_get_local_peripheral(struct zmk_esb_peripheral_diag *out);
void zmk_esb_diag_update_peripheral(uint8_t source,
                                    const struct zmk_esb_peripheral_diag *diag);
""",
        )

    app_esb = esb_root / "src" / "split" / "esb" / "app_esb.c"
    replace_once(
        app_esb,
        "static atomic_t diag_usb_last_error;\n",
        """static atomic_t diag_usb_last_error;
static atomic_t diag_peripheral_key_tx_attempts;
static atomic_t diag_peripheral_key_tx_queue_errors;
static atomic_t diag_peripheral_tx_successes;
static atomic_t diag_peripheral_tx_failures;
static struct zmk_esb_peripheral_diag diag_remote[2];
""",
    )
    replace_once(
        app_esb,
        "void zmk_esb_diag_note_usb_result(int err) {\n",
        """void zmk_esb_diag_note_key_tx(int err) {
    atomic_inc(&diag_peripheral_key_tx_attempts);
    if (err != 0) {
        atomic_inc(&diag_peripheral_key_tx_queue_errors);
    }
}

void zmk_esb_diag_note_tx_result(int err) {
    if (err == 0) {
        atomic_inc(&diag_peripheral_tx_successes);
    } else {
        atomic_inc(&diag_peripheral_tx_failures);
    }
}

void zmk_esb_diag_get_local_peripheral(struct zmk_esb_peripheral_diag *out) {
    if (out == NULL) {
        return;
    }
    out->key_tx_attempts = (uint16_t)atomic_get(&diag_peripheral_key_tx_attempts);
    out->key_tx_queue_errors = (uint16_t)atomic_get(&diag_peripheral_key_tx_queue_errors);
    out->tx_successes = (uint16_t)atomic_get(&diag_peripheral_tx_successes);
    out->tx_failures = (uint16_t)atomic_get(&diag_peripheral_tx_failures);
}

void zmk_esb_diag_update_peripheral(uint8_t source,
                                    const struct zmk_esb_peripheral_diag *diag) {
    if (diag == NULL || source >= ARRAY_SIZE(diag_remote)) {
        return;
    }
    diag_remote[source] = *diag;
}

void zmk_esb_diag_note_usb_result(int err) {
""",
    )
    replace_once(
        app_esb,
        "        .usb_last_error = (int32_t)atomic_get(&diag_usb_last_error),\n    };\n}",
        """        .usb_last_error = (int32_t)atomic_get(&diag_usb_last_error),
        .remote = {diag_remote[0], diag_remote[1]},
    };
}""",
    )
    replace_once(
        app_esb,
        '    case ESB_EVENT_TX_SUCCESS:\n        LOG_DBG("TX SUCCESS: TRY %u", event->tx.tx_attempts);',
        '    case ESB_EVENT_TX_SUCCESS:\n'
        '        zmk_esb_diag_note_tx_result(0);\n'
        '        LOG_DBG("TX SUCCESS: TRY %u", event->tx.tx_attempts);',
    )
    replace_once(
        app_esb,
        '    case ESB_EVENT_TX_FAILED:\n        LOG_WRN("ESB_EVENT_TX_FAILED");',
        '    case ESB_EVENT_TX_FAILED:\n'
        '        zmk_esb_diag_note_tx_result(-EIO);\n'
        '        LOG_WRN("ESB_EVENT_TX_FAILED");',
    )

    common = esb_root / "src" / "split" / "esb" / "common.c"
    replace_once(
        common,
        '#include "common.h"\n',
        '#include "common.h"\n#include "app_esb.h"\n',
    )
    replace_once(
        common,
        "    case ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_BATTERY_EVENT:\n"
        "        size = sizeof(((struct zmk_split_transport_peripheral_event *)0)->data.battery_event);\n"
        "        break;\n",
        "    case ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_BATTERY_EVENT:\n"
        "        size = sizeof(((struct zmk_split_transport_peripheral_event *)0)->data.battery_event);\n"
        "        break;\n"
        "    case ZMK_SPLIT_ESB_DIAG_EVENT_TYPE:\n"
        "        size = sizeof(struct zmk_esb_peripheral_diag);\n"
        "        break;\n",
    )

    peripheral = esb_root / "src" / "split" / "esb" / "peripheral.c"
    replace_once(
        peripheral,
        "        ret = send_data(PERIPHERAL_ID, event->type, slot.position_state);\n",
        "        ret = send_data(PERIPHERAL_ID, event->type, slot.position_state);\n"
        "        zmk_esb_diag_note_key_tx(ret);\n",
    )
    replace_once(
        peripheral,
        "bool is_central_connected = false;\n",
        """bool is_central_connected = false;

static void peripheral_diag_report_work_handler(struct k_work *work) {
    if (!is_central_connected) {
        return;
    }

    struct zmk_esb_peripheral_diag diag;
    zmk_esb_diag_get_local_peripheral(&diag);
    int ret = send_data(PERIPHERAL_ID, ZMK_SPLIT_ESB_DIAG_EVENT_TYPE, &diag);
    if (ret != 0) {
        LOG_DBG("ESB diagnostic report enqueue failed: %d", ret);
    }
    k_work_reschedule(&peripheral_diag_report_work, K_SECONDS(1));
}
K_WORK_DELAYABLE_DEFINE(peripheral_diag_report_work, peripheral_diag_report_work_handler);
""",
    )
    replace_once(
        peripheral,
        "    k_work_reschedule(&battery_report_work, K_NO_WAIT);\n",
        "    k_work_reschedule(&battery_report_work, K_NO_WAIT);\n"
        "    k_work_reschedule(&peripheral_diag_report_work, K_SECONDS(1));\n",
    )
    replace_once(
        peripheral,
        "    is_central_connected = false;\n",
        "    is_central_connected = false;\n"
        "    k_work_cancel_delayable(&peripheral_diag_report_work);\n",
    )

    central = esb_root / "src" / "split" / "esb" / "central.c"
    replace_once(
        central,
        "    case ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_BATTERY_EVENT:\n"
        "        memcpy(&evt.data, &data[1], data_size);\n"
        "        if (zmk_split_transport_central_peripheral_event_handler(&esb_central, source, evt) ==\n"
        "            -EINVAL) {\n"
        "            k_work_submit(&notify_status_work);\n"
        "        }\n"
        "        break;\n",
        "    case ZMK_SPLIT_TRANSPORT_PERIPHERAL_EVENT_TYPE_BATTERY_EVENT:\n"
        "        memcpy(&evt.data, &data[1], data_size);\n"
        "        if (zmk_split_transport_central_peripheral_event_handler(&esb_central, source, evt) ==\n"
        "            -EINVAL) {\n"
        "            k_work_submit(&notify_status_work);\n"
        "        }\n"
        "        break;\n"
        "    case ZMK_SPLIT_ESB_DIAG_EVENT_TYPE: {\n"
        "        struct zmk_esb_peripheral_diag diag;\n"
        "        memcpy(&diag, &data[1], sizeof(diag));\n"
        "        zmk_esb_diag_update_peripheral(source, &diag);\n"
        "        break;\n"
        "    }\n",
    )

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("zmk_root", type=Path, help="West checkout path of ZMK")
    parser.add_argument(
        "display_module_root", type=Path, help="West checkout path of zmk-dongle-display"
    )
    parser.add_argument("esb_module_root", type=Path, help="West checkout path of zmk-feature-split-esb")
    args = parser.parse_args()

    required = [
        args.zmk_root / "include" / "zmk" / "split" / "central.h",
        args.zmk_root / "CMakeLists.txt",
        args.display_module_root / "boards" / "shields" / "dongle_display" / "CMakeLists.txt",
        args.display_module_root / "boards" / "shields" / "dongle_display" / "custom_status_screen.c",
        args.display_module_root / "boards" / "shields" / "dongle_display" / "widgets" / "battery_status.c",
        args.esb_module_root / "include" / "app_esb.h",
        args.esb_module_root / "src" / "split" / "esb" / "app_esb.c",
        args.esb_module_root / "src" / "split" / "esb" / "central.c",
        args.zmk_root / "src" / "hid_listener.c",
        args.zmk_root / "src" / "endpoints.c",
        args.zmk_root / "src" / "usb_hid.c",
    ]
    for path in required:
        if not path.is_file():
            print(f"error: expected source file not found: {path}", file=sys.stderr)
            return 2

    try:
        patch_zmk_central_count(args.zmk_root)
        patch_zmk_battery_event_cmake(args.zmk_root)
        patch_display_module(args.display_module_root)
        patch_esb_diagnostics(args.zmk_root, args.esb_module_root)
        patch_peripheral_diagnostics(args.esb_module_root)
    except (OSError, RuntimeError) as exc:
        print(f"error: failed to apply ESB battery/display patch: {exc}", file=sys.stderr)
        return 1

    print("Applied ESB battery/display support and ESB-to-USB input diagnostics.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
