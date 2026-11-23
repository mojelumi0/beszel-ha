# Installation

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Ronjar&repository=beszel-ha&category=integration)

1. Install the Beszel API addon via HACS from the link above or by searching for it inside of HACS
2. Restart HomeAssistant
3. Go to integrations, press Add integration and search for BeszelAPI
4. In the Setup Dialog use the following values
    - *URL*: The root url / IP of your Beszel instance, like http://beszel.example.com or https://beszel.example.com
    - *user*: Either your default admin username / email or (recommended) create another user with the role user and assigning the agents you want to expose to it.
    - *password*: The password to the user
5. The API will pull the data and reload every 2 minutes

Currently all machines are added, selection will be added later (you can change this yourself by creating a new user in Beszel's PocketBase and adding this user only to the machines you want to be monitored).

# Usage
After installing the following entities will exposed as sensors (more to come):
- Status (Connection)
- Uptime (Minutes)
- CPU (Percentage)
- Disk usage (Percentage)
- Temperature (°C)
- Bandwidth (Mbit/s)
- RAM (Percentage)
- Battery (Percentage)

For example if your machine is named *test*, CPU will be available as ```sensor.test_cpu```

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
![Screenshot from HomeAssistant settings page of my device and its entities](/pictures/sensors.png)

And here one card I created for myself using those sensors:
![Screenshot from HomeAssistant dashboard with a card showing CPU, RAM and Disk usage as bar charts](/pictures/example_card.png)

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
