<div align="center">

# Beszel API for Home Assistant

Bring your Beszel systems into Home Assistant with native sensors, diagnostics, update information, and dashboard-ready entities.

[![Latest release](https://img.shields.io/github/v/release/mojelumi0/beszel-ha?style=flat-square)](https://github.com/mojelumi0/beszel-ha/releases/latest)
[![HACS](https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=flat-square)](https://my.home-assistant.io/redirect/hacs_repository/?owner=mojelumi0&repository=beszel-ha&category=integration)
[![Tests](https://github.com/mojelumi0/beszel-ha/actions/workflows/tests.yml/badge.svg)](https://github.com/mojelumi0/beszel-ha/actions/workflows/tests.yml)
[![Hassfest](https://github.com/mojelumi0/beszel-ha/actions/workflows/hassfest.yml/badge.svg)](https://github.com/mojelumi0/beszel-ha/actions/workflows/hassfest.yml)
[![HACS validation](https://github.com/mojelumi0/beszel-ha/actions/workflows/validate.yaml/badge.svg)](https://github.com/mojelumi0/beszel-ha/actions/workflows/validate.yaml)
[![License](https://img.shields.io/github/license/mojelumi0/beszel-ha?style=flat-square)](LICENSE)

[Install with HACS](https://my.home-assistant.io/redirect/hacs_repository/?owner=mojelumi0&repository=beszel-ha&category=integration) · [Releases](https://github.com/mojelumi0/beszel-ha/releases) · [Report an issue](https://github.com/mojelumi0/beszel-ha/issues)

</div>

## What this integration does

Beszel already collects useful information about your servers. This integration makes that information available as native Home Assistant entities, ready for dashboards, history, automations, and notifications.

- Local polling directly from your Beszel Hub
- Automatic discovery of every system visible to the configured Beszel user
- Configurable polling interval from 10 to 3600 seconds
- Native Home Assistant device classes, state classes, and units
- Automatic reauthentication and Home Assistant reauth flow
- Parallel system-stat requests for faster updates with multiple systems
- Optional S.M.A.R.T., load-average, fan, battery, and systemd diagnostics
- Beszel Hub update entity when update checks are enabled in Beszel

## Installation

### HACS

1. Select **Install with HACS** above, or open HACS and add this repository as a custom **Integration** repository:

   ```text
   https://github.com/mojelumi0/beszel-ha
   ```

2. Download **Beszel API** in HACS.
3. Restart Home Assistant.
4. Open **Settings → Devices & services → Add integration**.
5. Search for **Beszel API**.

### Manual installation

1. Download the latest release.
2. Copy `custom_components/beszel_api` into your Home Assistant `custom_components` directory.
3. Restart Home Assistant.
4. Add **Beszel API** from **Settings → Devices & services**.

## Connecting to Beszel

Enter the root address of your Beszel Hub, including `http://` or `https://`. A standard local Beszel installation listens on port `8090`.

| Setup | Example URL |
|---|---|
| Local IP address | `http://192.168.1.50:8090` |
| Local hostname | `http://beszel.local:8090` |
| Local DNS name | `http://beszel.home.arpa:8090` |
| Reverse proxy with HTTPS | `https://beszel.example.com` |

In other words, for a normal local installation use:

```text
http://<IP-ADDRESS-OF-YOUR-BESZEL-HUB>:8090
```

Example:

```text
http://192.168.178.40:8090
```

The address must be reachable from Home Assistant itself. Do not use `localhost` unless the Beszel Hub is running inside the same Home Assistant environment and is actually reachable there.

The setup form asks for:

| Field | Description |
|---|---|
| URL | Root URL of the Beszel Hub, without an API path |
| Username | Beszel account email or username |
| Password | Password for the Beszel account |
| Update interval | Polling interval in seconds; default is `120` |
| Verify SSL | Verify the HTTPS certificate; leave enabled for trusted certificates |

> [!TIP]
> Create a dedicated Beszel user for Home Assistant and give it access only to the systems you want to expose. The integration automatically adds every system visible to that account.

## Available entities

Each Beszel system becomes a Home Assistant device. Entity IDs are based on the system name in Beszel. A system called `homeserver`, for example, normally creates `sensor.homeserver_cpu`, `sensor.homeserver_ram`, and `binary_sensor.homeserver_status`.

### Main sensors

| Entity | Unit | Details |
|---|---:|---|
| CPU | `%` | Per-core usage and user/system/iowait/steal/idle breakdown are attributes |
| RAM | `%` | Used, total, buffer/cache, and ZFS ARC values are attributes |
| RAM Total | `GiB` | Total installed memory |
| Disk | `%` | Used and total space are attributes |
| Disk Total | `GiB` | Total size of the primary disk |
| Bandwidth | `MiB/s` | Combined current network rate |
| Network Receive | `KiB/s` | Current received data rate |
| Network Send | `KiB/s` | Current sent data rate |
| Uptime | `min` | System uptime |
| Status | on/off | Connectivity binary sensor for the Beszel Agent |

### Automatically discovered sensors

These entities appear only when the Beszel Hub or Agent reports the corresponding data.

| Entity | Unit | Details |
|---|---:|---|
| GPU | `%` | One per GPU, with VRAM and power attributes |
| SWAP | `%` | Includes used and total swap attributes |
| Temperature | `°C` | Main temperature with named temperature zones as attributes |
| Additional disks | `%` / `GiB` | Usage and total size for additional mounted filesystems |
| S.M.A.R.T. | problem/ok | Disk health, temperature, capacity, lifetime, and selected failure attributes |
| Load Average 1m / 5m / 15m | — | Diagnostic sensors, disabled by default |
| Failed Services | — | Number of failed systemd services |
| Total Services | — | Diagnostic sensor, disabled by default |
| Fan | `rpm` | One diagnostic sensor per fan reported by recent Linux Agents |
| Battery | `%` | Existing primary battery plus additional named batteries |
| Beszel Hub Update | — | Available when Hub update checks are enabled |

Some diagnostics are disabled by default to keep the entity list and Home Assistant Recorder database manageable. You can enable them from the Beszel device page in **Settings → Devices & services → Entities**.

Fan monitoring and multiple named batteries require a recent Beszel Hub and Agent. Older Agents continue to work with the metrics they support.

## Native Home Assistant dashboard example

This example uses only cards included with Home Assistant. It does not require Mushroom, Bar Card, Card Mod, or any other frontend extension.

It includes:

- CPU, RAM, and disk gauges with colored ranges
- Status, uptime, temperature, traffic, and failed-service tiles
- Optional load-average tiles
- A 24-hour CPU, RAM, and disk history graph

Before pasting the YAML, replace every occurrence of `homeserver` with the entity prefix created for your own Beszel system. Check **Developer tools → States** if you are unsure about an entity ID. Remove any optional tile whose entity does not exist on your system.

To add it, edit a dashboard, select **Add card → Manual**, and paste:

```yaml
type: vertical-stack
cards:
  - type: heading
    heading: Homeserver
    heading_style: title
    icon: mdi:server

  - type: grid
    columns: 3
    square: false
    cards:
      - type: gauge
        entity: sensor.homeserver_cpu
        name: CPU
        min: 0
        max: 100
        needle: true
        segments:
          - from: 0
            color: var(--success-color)
          - from: 70
            color: var(--warning-color)
          - from: 90
            color: var(--error-color)

      - type: gauge
        entity: sensor.homeserver_ram
        name: RAM
        min: 0
        max: 100
        needle: true
        segments:
          - from: 0
            color: var(--success-color)
          - from: 75
            color: var(--warning-color)
          - from: 90
            color: var(--error-color)

      - type: gauge
        entity: sensor.homeserver_disk
        name: Disk
        min: 0
        max: 100
        needle: true
        segments:
          - from: 0
            color: var(--success-color)
          - from: 75
            color: var(--warning-color)
          - from: 90
            color: var(--error-color)

  - type: heading
    heading: System
    heading_style: subtitle
    icon: mdi:server-network

  - type: grid
    columns: 2
    square: false
    cards:
      - type: tile
        entity: binary_sensor.homeserver_status
        name: Agent status
        icon: mdi:server-network
        color: green

      - type: tile
        entity: sensor.homeserver_uptime
        name: Uptime
        icon: mdi:clock-outline
        color: blue

      - type: tile
        entity: sensor.homeserver_temperature
        name: Temperature
        icon: mdi:thermometer
        color: orange

      - type: tile
        entity: sensor.homeserver_services_failed
        name: Failed services
        icon: mdi:alert-circle-outline
        color: red

      - type: tile
        entity: sensor.homeserver_network_receive
        name: Network receive
        icon: mdi:download-network
        color: green

      - type: tile
        entity: sensor.homeserver_network_send
        name: Network send
        icon: mdi:upload-network
        color: blue

  - type: heading
    heading: Load average
    heading_style: subtitle
    icon: mdi:chart-line

  - type: grid
    columns: 3
    square: false
    cards:
      - type: tile
        entity: sensor.homeserver_load_average_1m
        name: 1 minute
        icon: mdi:numeric-1-circle-outline
        color: green

      - type: tile
        entity: sensor.homeserver_load_average_5m
        name: 5 minutes
        icon: mdi:numeric-5-circle-outline
        color: amber

      - type: tile
        entity: sensor.homeserver_load_average_15m
        name: 15 minutes
        icon: mdi:timer-sand
        color: orange

  - type: heading
    heading: Last 24 hours
    heading_style: subtitle
    icon: mdi:chart-areaspline

  - type: history-graph
    hours_to_show: 24
    entities:
      - entity: sensor.homeserver_cpu
        name: CPU
      - entity: sensor.homeserver_ram
        name: RAM
      - entity: sensor.homeserver_disk
        name: Disk
```

### Compact tile-only alternative

For a smaller mobile dashboard, this version uses only native tile cards:

```yaml
type: grid
columns: 2
square: false
cards:
  - type: tile
    entity: binary_sensor.homeserver_status
    name: Status
    icon: mdi:server-network
    color: green
  - type: tile
    entity: sensor.homeserver_uptime
    name: Uptime
    icon: mdi:clock-outline
    color: blue
  - type: tile
    entity: sensor.homeserver_cpu
    name: CPU
    icon: mdi:cpu-64-bit
    color: green
  - type: tile
    entity: sensor.homeserver_ram
    name: RAM
    icon: mdi:memory
    color: blue
  - type: tile
    entity: sensor.homeserver_disk
    name: Disk
    icon: mdi:harddisk
    color: orange
  - type: tile
    entity: sensor.homeserver_temperature
    name: Temperature
    icon: mdi:thermometer
    color: red
```

## Automations

Because the integration exposes regular Home Assistant entities, you can use them in automations without any special service calls. Useful examples include:

- Notify when `binary_sensor.homeserver_status` turns off
- Alert when CPU, RAM, or disk usage stays above a threshold
- Notify when `sensor.homeserver_services_failed` rises above `0`
- Alert when a S.M.A.R.T. problem sensor turns on
- Notify when a battery drops below a chosen percentage
- Show a persistent notification when a Beszel Hub update is available

## Troubleshooting

### Home Assistant cannot connect

- Open the Beszel URL from another device on the same network.
- Confirm that Home Assistant can reach the Hub address and port.
- For a standard local installation, include port `8090`.
- Do not add `/api`, `/login`, or another path to the URL.
- If you use HTTPS, verify that the certificate is valid for the hostname entered.

### Authentication fails

- Sign in to the Beszel web interface with the same account.
- Make sure both username and password are present.
- If the credentials changed, open the Home Assistant reauthentication notification and enter the new credentials.

### A sensor is missing

Optional entities are created only when Beszel reports the corresponding metric. Confirm that the Hub and Agent are current and that the value appears in Beszel itself. Reload the integration after adding new hardware such as a fan, battery, GPU, or disk.

### A diagnostic entity is disabled

Open **Settings → Devices & services → Entities**, search for the entity, and enable it. Load-average and total-service sensors are intentionally disabled by default.

## Credits

- Original Home Assistant integration by [Ronjar](https://github.com/Ronjar/beszel-ha)
- Beszel by [henrygd](https://github.com/henrygd/beszel)
- Additional feature and documentation inspiration from [inventor7777/improved-beszel-ha](https://github.com/inventor7777/improved-beszel-ha)
- This fork is maintained by [mojelumi0](https://github.com/mojelumi0) with assistance from GitHub Copilot, Anthropic Claude, and OpenAI Codex

## License

Released under the [MIT License](LICENSE).
