# Installation

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Ronjar&repository=beszel-ha&category=integration)

1. Install the **Beszel API** integration through **Marketplace** using the link above, or search for it under **Settings > Marketplace**.
2. Restart Home Assistant if requested after installation or an update.
3. Go to **Settings > Devices & services > Add integration** and search for **Beszel API**.
4. Enter your Beszel Hub connection details:
    - **URL**: The root URL or IP address, including `http://` or `https://` and the port when needed. Examples: `https://beszel.example.com` or `http://192.168.1.123:8090`. Use your Hub's actual address and port.
    - **Username**: Your Beszel username/email. A dedicated user assigned only to the desired systems is recommended.
    - **Password**: The password for that user.
5. Choose the monitored systems as described below.

The default monitoring interval is 120 seconds and can be changed in the integration options.
On older Home Assistant versions without Marketplace, install through HACS and restart Home Assistant.

### Choosing monitored systems

After validating the connection, choose **All systems** (the default) or
**Selected systems** and select one or more systems from the list. System names
and stable IDs distinguish machines with the same name; offline systems can be
selected too. All systems automatically includes systems added later. Selected
systems only monitors the saved IDs, even when a system is renamed.

To change the selection, open **Settings > Devices & services > Beszel API >
Configure**, submit the connection settings, then update the system selection.
Existing installations without selection settings continue to monitor all
systems. Changes take effect after the integration reloads. Deselecting a system
does not delete its entity registry entries; these remain unavailable and reuse
their existing IDs when selected again. Previously selected systems remain in
the selection list by ID while absent from the Hub response.

The selection controls Home Assistant monitoring. To restrict the account's
access itself, assign only the desired systems to that user in Beszel.

# Usage

Each Beszel system becomes a Home Assistant device. Entity IDs are based on the system name in Beszel. A system called `homeserver`, for example, normally creates `sensor.homeserver_cpu`, `sensor.homeserver_ram`, and `binary_sensor.homeserver_status`.

### Main sensors

| Entity | Unit | Details |
|---|---:|---|
| CPU | `%` | Per-core usage and user/system/iowait/steal/idle/IRQ/SoftIRQ/Nice breakdown are attributes |
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
| GPU | `%` | One per GPU, with VRAM, power, package power, and engine usage attributes |
| SWAP | `%` | Includes used and total swap attributes |
| Temperature | `°C` | Main temperature with named temperature zones as attributes |
| Additional disks | `%` / `GiB` | Usage, total size, and supported I/O details for additional mounted filesystems |
| ZFS pool | `%` | One diagnostic usage sensor plus a problem binary sensor per pool, with health, capacity, and I/O attributes |
| S.M.A.R.T. | problem/ok | Disk health, temperature, capacity, lifetime, and selected failure attributes |
| Load Average 1m / 5m / 15m | - | Diagnostic sensors, disabled by default |
| Failed Services | - | Number of failed systemd services |
| Total Services | - | Diagnostic sensor, disabled by default |
| Fan | `rpm` | One diagnostic sensor per fan reported by recent Linux Agents |
| Wi-Fi Signal | `dBm` | One diagnostic sensor per interface with a current native signal reading; SSID is an attribute when reported |
| Battery | `%` | Existing primary battery plus additional named batteries |
| Beszel Hub Update | - | Available when Hub update checks are enabled |

Some diagnostics are disabled by default to keep the entity list and Home Assistant Recorder database manageable. You can enable them from the Beszel device page in **Settings > Devices & services > Entities**.

Fan monitoring, multiple named batteries, and the newest I/O and GPU details
require a recent Beszel Hub and Agent. ZFS pool metrics require Beszel 0.19 or
newer. Older Agents continue to work with the metrics they support.

The main disk entity also exposes supported read/write rates, cumulative I/O,
latency, and utilization values as attributes. Network receive and send
entities expose a compact `interfaces` attribute with per-interface rates and
cumulative traffic, so these details do not create dozens of extra entities.


For a system named `test`, the CPU entity normally has the ID `sensor.test_cpu`.

## Additional diagnostics and compatibility

Additional metrics are exposed when reported by the Beszel Hub and Agent:
CPU load averages, per-core CPU attributes, disk I/O, per-interface network
attributes, fan speeds, named batteries, systemd service counts, ZFS pools and
health, and per-interface Wi-Fi signal sensors. GPU and S.M.A.R.T. entities expose
additional diagnostic attributes. Later-reported metrics are discovered on
subsequent updates without reloading the integration.

Beszel 0.21.1 RAM and Swap byte fields are preferred, with legacy fields retained
as fallbacks. Swap total and used values are validated as a complete pair.
Existing sensor unique IDs and legacy attributes are preserved. During a Hub or
Agent outage, measurements become unavailable instead of displaying stale values.

Wi-Fi sensors require a current, finite native RSSI reading in dBm. No entity is
created for an interface without a signal reading. Existing entities remain
registered during outages and recover without duplicates. The interface is the
identity; SSID changes do not create a new sensor. Optional `access_point` and
Windows `profile` attributes are shown only when reported. For an OpenWrt access
point, the signal is the average RSSI of its associated clients. Optional Wi-Fi
metadata depends on the Hub and Agent version. Signal and metadata are cleared
when a current reading is unavailable.

Setup and options validate connectivity, credentials, the HTTP(S) URL, and an
update interval from 10 to 3600 seconds. Username and password are required;
use a dedicated Beszel user assigned to the systems you want to monitor.
Expired sessions are refreshed automatically, and rejected credentials trigger
Home Assistant's reauthentication flow for the existing config entry.

## Development tests

The tests target Home Assistant 2026.11.2 and require Python 3.14.2 or newer
within the 3.14 series. Run the complete Home Assistant fixture suite on Linux:

```shell
python -m pip install -r requirements_test.txt
python -m pytest -v
python -m ruff check custom_components/beszel_api tests
```

# Examples
Here is one of my machines with the entities the integration currently exports
![Screenshot from the Home Assistant device page and its entities](/pictures/sensors.png)

And here one card I created for myself using those sensors:
![Screenshot from a Home Assistant dashboard showing CPU, RAM and disk usage](/pictures/example_card.png)

The YAML for this card layout:
``` YAML
type: custom:vertical-stack-in-card
cards:
  - type: horizontal-stack
    cards:
      - type: custom:mushroom-template-card
        primary: Evergreen
        icon: mdi:server
        secondary: ""
        icon_color: |-
          {% if states('binary_sensor.evergreen_status') | bool %}
            green
          {% else %}
            red
          {% endif %}
        fill_container: false
        multiline_secondary: false
        entity: binary_sensor.evergreen_status
      - type: custom:mushroom-template-card
        entity: sensor.evergreen_uptime
        icon: mdi:sort-clock-descending
        primary: "{{ (states('sensor.evergreen_uptime') | int / 1440) | int  }} Days"
        secondary: ""
        icon_color: blue
        card_mod:
          style: |
            ha-card {
              margin: 0 10px;
              align-items: end;
              box-shadow: none;
            }
  - type: custom:bar-card
    entities:
      - entity: sensor.evergreen_cpu
        name: CPU
        color: "#4caf50"
      - entity: sensor.evergreen_ram
        name: RAM
        color: "#2196f3"
      - entity: sensor.evergreen_disk
        name: Disk
        color: "#f44336"
    positions:
      indicator: "off"
```
