# Another Solem Bluetooth Watering Controller

Minimal Home Assistant custom integration for SOLEM BL-IP Bluetooth watering controllers.

## Scope

This integration only handles local BLE control and status polling:

- Start one station.
- Start all stations.
- Stop watering.
- Read real controller status by polling.

It does not include weather, schedules, water consumption, rain logic, or soil moisture logic. Build that behavior with native Home Assistant automations and helpers.

## Installation

Install as a custom HACS integration, then add it from Settings > Devices & Services.

## Configuration

During setup, select the discovered BL-IP controller and configure:

- Number of stations: choose the controller variant, 1, 2, 4, or 6 stations.
- Default manual watering duration.
- Status polling interval.
- Bluetooth timeout.

You can change duration, polling interval, and Bluetooth timeout later from the integration options.

## Entities

- Station switches start watering for the configured default duration.
- The all-stations switch starts all stations for the configured default duration.
- The default duration number controls manual watering duration in minutes.
- The stop button stops manual watering.
- The status sensor shows the controller mode.
- The time remaining sensor shows the BLE timer in seconds.
- The irrigating binary sensor is on while the controller reports active irrigation.
- The raw status sensor is disabled by default and can help debug parsing issues.

Turning off any station switch sends the controller's global stop command. The known BL-IP protocol exposes a stop-manual-watering command, not a per-station stop command.

## Bluetooth Troubleshooting

Home Assistant 2025.6 added a Bluetooth connection graph. Use it to confirm whether the BL-IP is visible directly or through a Bluetooth proxy:

[Home Assistant 2025.6 Bluetooth connection graph](https://www.home-assistant.io/blog/2025/06/11/release-20256/#making-sense-of-bluetooth)

The BL-IP does not send spontaneous status updates. The integration polls status by sending the non-intrusive ON/status command and parsing the notification response.

## Future TODOs

- [ ] Complete support for 1, 2, 4, and 6 station variants, including entity naming and hardware tests for each controller type.
- [ ] Add rain sensor or water meter input status if the BLE protocol exposes it, such as rain sensor active or water meter pulse/status.
- [ ] Investigate master valve / pump output awareness. The BL-IP `P` output starts 2 seconds before each station and stays active during watering; expose pump active if readable.
- [ ] Add battery status, if available over BLE, because BL-IP controllers run on a 9V battery.
- [ ] Add read-only program/storage inspection for programs and durations configured in the MySOLEM app, without managing schedules from Home Assistant.
- [ ] Add read-only Eco Mode / controller settings for diagnostics if the protocol exposes them.
- [ ] Add richer diagnostics with raw BLE status, firmware/device info, address, last successful poll, and last command result.

## Hardware Verification

1. Confirm the BL-IP appears in Home Assistant's Bluetooth view.
2. Add the integration from Settings > Devices & Services.
3. Start station 1 from Home Assistant.
4. Confirm watering starts on the physical controller.
5. Start watering from another app.
6. Confirm Home Assistant updates after the next polling interval.
7. Press Stop in Home Assistant.
8. Confirm watering stops.
