# Cornix LP — Kumbung ESB (TDMA/FHSS)

This repository builds an ESB-only ZMK firmware set for a Cornix LP split keyboard:

- nice!nano USB dongle: ESB central + USB HID
- Cornix LP left half: ESB peripheral ID 0
- Cornix LP right half: ESB peripheral ID 1

## Radio schedule

The firmware uses Kumbung's TDMA/FHSS transport and matching custom nRF Connect SDK ESB implementation.

The central is configured for a **2 kHz global TDMA slot rate**:

- `CONFIG_ESB_POLLING_RATE_2KHZ=y`
- `CONFIG_ESB_TDMA_PIPE_COUNT=2`
- `CONFIG_ZMK_SPLIT_BLE_CENTRAL_PERIPHERALS=2`

At 2 kHz, one global slot is 1,000,000 µs / 2,000 = 500 µs. The central rotates between the two active TDMA pipes, so each peripheral is scheduled on every second slot: nominally once every 1 ms (1 kHz per peripheral).

**Why the separate TDMA pipe setting exists:** Kumbung's ESB transport registers an eight-byte address-prefix table during initialization. The SDK's `CONFIG_ESB_PIPE_COUNT` controls that physical ESB pipe capacity and must remain 8 for that table. The unmodified SDK also uses that capacity as the TDMA scheduling count, which would create eight 500 µs slots and would not give the two active halves the intended 1 kHz interval. The build therefore applies the small, reproducible patch in `scripts/apply_kumbung_tdma_pipe_count.py`: physical pipe capacity remains 8, while `CONFIG_ESB_TDMA_PIPE_COUNT=2` sets the central scheduler and is communicated to the peripherals during synchronization.

The configured interval is the **ESB radio service interval**, not the keyboard matrix scan rate and not the USB HID polling interval. The firmware configuration targets the nominal timing; actual radio delivery can still be affected by synchronization, retransmissions, and RF conditions.

## Other configuration

- BLE, BLE split transport, wired split transport, and battery reporting are disabled.
- Kumbung's whole-key-state mode remains disabled for the initial baseline.
- Kumbung's documented requirements are met: MPSL timeslot sessions = 2, ESB max payload = 48, and system workqueue stack = 4096.
- The build patches only the checked-out SDK source in the GitHub Actions workspace; it does not modify Kumbung's upstream repository.

## Pinned dependencies

All moving branches are pinned to exact revisions in `config/west.yml`:

| Component | Revision |
|---|---|
| ZMK | `4dd95d7840eb3d5a3f59bb29fbf1ab054715ef85` |
| Kumbung split-ESB module | `f7b1bd15bfc5e96f3301f328efb2a0df16918b98` |
| Kumbung nRF Connect SDK | `07b7e70c0d223429c58e6cffb4e6ecaa70ff949e` |
| mbedtls | `c5115abac477249fab42e61368b8f87c3c9265e1` |
| oberon-psa-crypto | `d682b30a4498ecbaa8992e909b8d8c31f4988956` |
| nrfxlib | `dfadf17305d8f000eda9aa74a5b9ff1c5647a23e` |
| Cornix board module | `v3.0.0` |

The ZMK revision uses Zephyr 4.1.0+zmk-fixes, matching the Zephyr 4.1-based Kumbung SDK branch.

## Build outputs

GitHub Actions builds three artifacts:

- `cornix_dongle_esb`
- `cornix_left_esb`
- `cornix_right_esb`

Flash the dongle and both halves with the artifacts from the same workflow run.
