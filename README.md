# Another Solem Bluetooth Watering Controller

Minimal Home Assistant custom integration for SOLEM BL-IP Bluetooth watering controllers.

## Scope

This integration handles local BLE control, status polling and program management:

- Start one station with a custom duration.
- Start all stations.
- Run one of the controller's pre-configured programs (A, B, C).
- Stop watering.
- Read the live controller status, battery, and Bluetooth signal strength.
- Read the 3 user programs (name, frequency, water budget, start times,
  station assignments).
- Fully edit a program slot (name, frequency, days, start times, station
  assignments, water budget) in a single atomic write via the
  `configure_program` service. See [Configuring a program from Home
  Assistant](#configuring-a-program-from-home-assistant).

It does not include weather, schedules outside the controller's native
programs, water consumption, rain logic, or soil moisture logic. Build
that behavior with native Home Assistant automations and helpers.

### Screenshots

A visual tour of what the integration looks like inside Home Assistant
once installed. All images live in [`docs/screenshots/`](docs/screenshots/);
see that folder's README if you want to contribute updated versions.

| Screenshot | What it shows |
|---|---|
| [Device card](docs/screenshots/device-card.png) | Every BL-IP shows up as a single device in `Settings > Devices & services`, grouping all the sensors, switches, buttons and diagnostic entries the integration creates. |
| [Sample dashboard](docs/screenshots/dashboard.png) | The ready-to-use Lovelace layout for the most common controls, generated from [`examples/dashboard.yaml`](examples/dashboard.yaml). |
| [Options flow](docs/screenshots/options-flow.png) | `Settings > Devices & services > <your SOLEM> > Configure` — polling cadence, Bluetooth timeout and connection-idle knobs. Suggested presets in [Tuning for battery life](#tuning-for-battery-life). |
| [`start_station` in Developer Tools](docs/screenshots/start-station-service.png) | `Developer Tools > Actions`, the fastest way to fire any service for a one-off run. Pick the device from the dropdown and HA fills in `device_id`. |
| [`configure_program` in Developer Tools](docs/screenshots/configure-program-service.png) | Composing an entire program in a single service call. Toggle the form to YAML mode for fields like `start_times: []`. |

> When you copy [`examples/dashboard.yaml`](examples/dashboard.yaml) into
> Lovelace, remember to replace the entity IDs with the ones your instance
> generated (they are derived from your device name).

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

### `another_solem_bluetooth_watering_controller.run_program`

Run one of the controller's pre-configured programs (A, B, or C). The
controller iterates over the program's stations on its own using the
durations stored on the device.

```yaml
- service: another_solem_bluetooth_watering_controller.run_program
  target:
    device_id: <solem_device_id>
  data:
    program: 1   # 1=A, 2=B, 3=C
```

## Configuring a program from Home Assistant

A "program" on the BL-IP is the full package the controller runs
autonomously when its clock hits one of the program's **start times**:
name + frequency + per-station durations + start times + water budget.
The MySolem app composes one with a multi-step UI; this integration
exposes the same capability as a **single service call** so you can
build/update an entire program from a script or automation.

> You only need this if you want the BL-IP to water on its own (e.g.
> when Home Assistant is offline). If you drive everything from HA
> automations, ignore this section.

### `another_solem_bluetooth_watering_controller.configure_program`

Read-modify-write a program slot atomically. Only the fields you pass
are changed; reserved/undecoded bytes are preserved byte-for-byte
(write is refused if a change would touch them).

| Field | Type | Notes |
|---|---|---|
| `program` | `1` / `2` / `3` | program slot to modify (A/B/C) |
| `name` | string | max 16 ASCII characters |
| `water_budget` | int `0..200` | percentage multiplier, `100` = normal |
| `frequency` | string | one of `daily`, `custom`, `even_days`, `odd_days`, `odd_days_excl_31`, `interval` |
| `period_days` | int `1..30` | used with `frequency: interval` (e.g. `7` for weekly) |
| `days_of_week` | list | used with `frequency: custom`, items from `mon, tue, wed, thu, fri, sat, sun` |
| `start_times` | list of `HH:MM` | up to 8 entries; empty list clears the schedule |
| `stations` | mapping `{station: minutes}` | up to 5 stations; missing/`0` entries deassign |

Full example: a "Morning" program that runs every Mon/Wed/Fri at 06:30
and 19:00, watering station 1 for 15 min and station 2 for 10 min:

```yaml
- service: another_solem_bluetooth_watering_controller.configure_program
  target:
    device_id: <solem_device_id>
  data:
    program: 1
    name: "Morning"
    frequency: custom
    days_of_week: [mon, wed, fri]
    water_budget: 100
    start_times: ["06:30", "19:00"]
    stations:
      1: 15
      2: 10
```

### Running a service from Home Assistant

There are three ways to fire any of these services, from the most ad-hoc to
the most reusable. The same approach works for `start_station`,
`start_all_stations`, `stop`, `run_program` and `configure_program`.

**A — Developer Tools (one-off run)**

Open `Developer Tools` (the screwdriver icon in the sidebar) → `Actions` tab,
search for the service, fill in the form, press `Perform action`. For
fields typed as "object" (`stations`, `start_times` when you want an empty
list), toggle the form to YAML mode and paste a payload like the examples
above. See the
[Calling configure_program from Developer Tools](#calling-configure_program-from-developer-tools)
screenshot for what the panel looks like.

**B — A reusable script**

Save the call as a script so you can run it from a Lovelace button card, an
automation, or by calling `script.<id>` from anywhere:

```yaml
solem_disable_program_a:
  alias: "Solem: pause Program A"
  sequence:
    - action: another_solem_bluetooth_watering_controller.configure_program
      data:
        device_id: <solem_device_id>
        program: 1
        start_times: []
```

**C — Inline in an automation**

```yaml
- alias: "Pause SOLEM programs when it rains"
  trigger:
    - platform: state
      entity_id: binary_sensor.is_raining
      to: "on"
  action:
    - action: another_solem_bluetooth_watering_controller.configure_program
      data:
        device_id: <solem_device_id>
        program: 1
        start_times: []
```

### Common recipes

Quick payload-only references for the most useful operations. In every
example, replace `<solem_device_id>` with your controller's `device_id`
(you can find it in `Settings > Devices & services > <your SOLEM>` — it's
the last segment of the browser URL, or it gets filled automatically when
you pick the device from the Developer Tools form).

**Pause a program (DIY rain delay)**

The program stays on the controller but never fires by itself, because it
has no scheduled start times:

```yaml
action: another_solem_bluetooth_watering_controller.configure_program
data:
  device_id: <solem_device_id>
  program: 1
  start_times: []
```

**Resume the program**

```yaml
action: another_solem_bluetooth_watering_controller.configure_program
data:
  device_id: <solem_device_id>
  program: 1
  start_times: ["06:30", "19:00"]
```

**Lower watering for the cooler months**

```yaml
action: another_solem_bluetooth_watering_controller.configure_program
data:
  device_id: <solem_device_id>
  program: 1
  water_budget: 60
```

**Switch a program to "every 3 days"**

```yaml
action: another_solem_bluetooth_watering_controller.configure_program
data:
  device_id: <solem_device_id>
  program: 1
  frequency: interval
  period_days: 3
```

**Rename a program**

```yaml
action: another_solem_bluetooth_watering_controller.configure_program
data:
  device_id: <solem_device_id>
  program: 2
  name: "Evening"
```

**Force-stop anything that is currently watering**

Halts both manual cycles and any program currently being executed by the
controller's clock:

```yaml
action: another_solem_bluetooth_watering_controller.stop
data:
  device_id: <solem_device_id>
```

After any `configure_program` write completes the cached program sensors
(`sensor.<device>_program_X_name`, `..._frequency`, `..._water_budget`,
`..._start_times`, `..._stations`) refresh automatically.

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

### Use a recent ESPHome Bluetooth Proxy

If you reach the BL-IP via an ESPHome Bluetooth Proxy, keep the proxy firmware up to date. **ESPHome 2025.11.0** cut BLE event processing latency from 0-16 ms to ~12 µs and ships a coexistence fix (`status=0x85`/133) that keeps `ESP_COEX_PREFER_BT` held for the full lifetime of any active BLE connection. Combined with this integration's persistent-but-short BLE session, that translates into noticeably fewer spurious disconnects and faster command/notification round-trips on the BL-IP. No configuration change is required on the integration side.

## Future TODOs

- [ ] Complete support for 1, 2, 4, and 6 station variants, including entity naming and hardware tests for each controller type.
- [ ] Add rain sensor or water meter input status if the BLE protocol exposes it, such as rain sensor active or water meter pulse/status.
- [ ] Investigate master valve / pump output awareness. The BL-IP `P` output starts 2 seconds before each station and stays active during watering; expose pump active if readable.
- [ ] Reverse-engineer the MySolem "Permanent OFF / Rain Delay" command (currently the integration only reads back the `programmed_off` mode; the opcode to enter it is unknown). Once captured, expose it as a switch or service.
- [ ] Add read-only Eco Mode / controller settings for diagnostics if the protocol exposes them.

## Hardware Verification

1. Confirm the BL-IP appears in Home Assistant's Bluetooth view.
2. Add the integration from Settings > Devices & Services.
3. Start station 1 from Home Assistant.
4. Confirm watering starts on the physical controller.
5. Start watering from another app.
6. Confirm Home Assistant updates after the next polling interval.
7. Press Stop in Home Assistant.
8. Confirm watering stops.
