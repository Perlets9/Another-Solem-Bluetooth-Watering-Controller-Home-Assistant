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

- **Number of stations**: choose the controller variant, 1, 2, 4, or 6 stations.
- **Default watering duration**: used by the station switches when turned on from the UI.
- **Polling cadence**: separate idle and active intervals (see "Tuning for battery life" below).
- **Bluetooth timeout**: how long to wait for a BLE connect or notification.
- **Connection mode**: whether to keep the BLE session open between operations, and for how long.

All values can be changed later from the integration options.

## Entities

- Station switches start watering for the configured **Watering Duration**.
- The all-stations switch starts every station sequentially for the configured duration.
- The **Watering Duration** number controls the duration used by the switches above (services can override it per-call, see below).
- The **Stop** button stops manual watering.
- The **Refresh Status** button forces a status read on demand.
- The **Reset Bluetooth Connection** button (diagnostic, disabled by default) forcefully tears down the BLE session and clears stale OS/proxy connections.
- The **Status** sensor shows the controller mode.
- The **Time Remaining** sensor shows the BLE timer in seconds.
- The **Irrigating** binary sensor is on while the controller reports active irrigation.
- The **Raw Status** sensor is disabled by default and can help debug parsing issues.

Turning off any station switch sends the controller's global stop command. The known BL-IP protocol exposes a stop-manual-watering command, not a per-station stop command.

## Services for automations

The switches always use the global **Watering Duration**. To run different stations for different times from an automation, use the per-call services:

### `another_solem_bluetooth_watering_controller.start_station`

Run one station for a specific duration.

```yaml
- service: another_solem_bluetooth_watering_controller.start_station
  target:
    device_id: <solem_device_id>
  data:
    station: 1
    duration: 10
```

### `another_solem_bluetooth_watering_controller.start_all_stations`

Run every station sequentially for the given duration per station.

```yaml
- service: another_solem_bluetooth_watering_controller.start_all_stations
  target:
    device_id: <solem_device_id>
  data:
    duration: 15
```

### `another_solem_bluetooth_watering_controller.stop`

Stop any manual watering.

```yaml
- service: another_solem_bluetooth_watering_controller.stop
  target:
    device_id: <solem_device_id>
```

### Full automation example: different durations per station

```yaml
alias: Morning watering
trigger:
  - platform: time
    at: "06:00:00"
action:
  - repeat:
      for_each:
        - { station: 1, duration: 10 }
        - { station: 2, duration: 20 }
        - { station: 3, duration: 15 }
      sequence:
        - service: another_solem_bluetooth_watering_controller.start_station
          target:
            device_id: <solem_device_id>
          data:
            station: "{{ repeat.item.station }}"
            duration: "{{ repeat.item.duration }}"
        - delay:
            minutes: "{{ repeat.item.duration }}"
            seconds: 30
```

## Tuning for battery life

The BL-IP runs on a battery, so the integration is built around an adaptive polling strategy and a persistent-but-short BLE session. Every option you see in the UI affects the battery/responsiveness trade-off. Three suggested presets:

### 🔋 Battery Saver (maximum lifetime)

Status updates only after commands or on demand. Best if you mostly drive the controller from automations and don't need live UI state.

| Option | Value |
|---|---|
| Enable status polling | **Off** |
| Active polling interval | `120` s |
| Bluetooth timeout | `15` s |
| Keep BLE connection between operations | **On** |
| Connection idle timeout | `5` s |

Notes: the *Active* interval still matters if a watering session is started; once the BL-IP reports idle again polling stops automatically. Press **Refresh Status** anytime to force a read.

### ⚖️ Balanced (default)

The shipped defaults. Live sensors with reasonable battery cost.

| Option | Value |
|---|---|
| Enable status polling | **On** |
| Idle polling interval | `600` s (10 min) |
| Active polling interval | `30` s |
| Bluetooth timeout | `15` s |
| Keep BLE connection between operations | **On** |
| Connection idle timeout | `15` s |

### ⚡ Responsive (battery is not a concern)

Use this if the BL-IP is mains-powered, or if you prefer instantaneous UI feedback over battery life.

| Option | Value |
|---|---|
| Enable status polling | **On** |
| Idle polling interval | `60` s |
| Active polling interval | `15` s |
| Bluetooth timeout | `20` s |
| Keep BLE connection between operations | **On** |
| Connection idle timeout | `120` s |

### What each knob does

- **Idle polling interval**: how often to read status when the controller is not watering. By far the most impactful setting at rest. Increase aggressively for battery life.
- **Active polling interval**: how often to read status during a watering session, so the timer countdown stays accurate. Increase to drain less battery during long sessions.
- **Connection idle timeout**: how long the BLE session lingers after the last operation. Short values (5-15s) save battery; long values (60-120s) make back-to-back commands snappier.
- **Keep BLE connection between operations**: leave it on. Disabling it forces a fresh connect/disconnect every single op, which the bleak service cache mostly hides but does add overhead and load on the OS Bluetooth stack.
- **Bluetooth timeout**: raise it if you reach the BL-IP through a slow ESPHome proxy and see "no notification received" errors.

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
