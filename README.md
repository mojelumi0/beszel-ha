# Beszel API for Home Assistant

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=mojelumi0&repository=beszel-ha&category=integration)
[![Validate with hassfest](https://github.com/mojelumi0/beszel-ha/actions/workflows/hassfest.yml/badge.svg)](https://github.com/mojelumi0/beszel-ha/actions/workflows/hassfest.yml)
[![HACS Validate](https://github.com/mojelumi0/beszel-ha/actions/workflows/validate.yaml/badge.svg)](https://github.com/mojelumi0/beszel-ha/actions/workflows/validate.yaml)

A [Beszel](https://beszel.dev) monitoring integration for Home Assistant. Connects to your Beszel Hub's REST API and exposes your monitored systems, their disks and the Hub itself as native Home Assistant entities.

## About this fork

This repository is a fork of [ronjar/beszel-ha](https://github.com/ronjar/beszel-ha) — all credit for the original integration, its architecture and the initial idea goes to **[Ronjar](https://github.com/ronjar)**. If you find this useful, please check out and support the original project as well.

Since forking, this version has been extended with the help of **GitHub Copilot**, **Claude Sonnet 5** (Anthropic), and **OpenAI Codex**. AI-assisted changes are reviewed by the maintainer and validated with automated checks and focused runtime testing before release.

Notable changes compared to the original repository:
- New sensors: GPU usage, SWAP, RAM/Disk totals, network send/receive, additional (EFS) mounted disks, S.M.A.R.T. disk health, and a Beszel Hub update entity
- More robust authentication: the integration now automatically re-authenticates instead of getting permanently stuck after a single failed login or an expired session
- Config flow hardening: username/password are now required fields (Beszel does not support anonymous/unauthenticated access) and the password field is masked in the UI
- System stats are now fetched in parallel instead of one-by-one, reducing polling time when you have several systems
- Various crash fixes (uptime and battery sensors, device-info handling on incomplete data)

## Installation

1. Install the Beszel API integration via HACS using the badge above, or by adding this repository manually as a custom repository in HACS
2. Restart Home Assistant
3. Go to **Settings → Devices & Services → Add Integration** and search for "Beszel API"
4. In the setup dialog, provide:
    - **URL**: the root URL of your Beszel instance, e.g. `http://beszel.example.com` or `https://beszel.example.com`
    - **Username**: a Beszel user account (required — anonymous access is not supported by Beszel). Recommended: create a dedicated user in Beszel's PocketBase admin UI and only assign it the systems you want exposed to Home Assistant, instead of using your main admin account
    - **Password**: the password for that user
    - **Update interval**: how often to poll Beszel, in seconds (10–3600, default 120)
    - **Verify SSL**: whether to verify the Beszel instance's SSL certificate
5. The integration will poll Beszel on the configured interval and create entities for every system the given user has access to

Currently all systems the user has access to are added automatically; per-system selection isn't built into the config flow yet. In the meantime, you can control this from the Beszel side by creating a Beszel user that's only assigned to the systems you want monitored in Home Assistant.

You can change the URL, credentials, update interval or SSL verification at any time via **Settings → Devices & Services → Beszel API → Configure**.

## Usage

After installing, the following entities are exposed per monitored system (more may appear automatically depending on what your Beszel agent reports):

### Sensors
| Entity | Unit | Notes |
|---|---|---|
| CPU | % | |
| GPU | % | One per detected GPU, with VRAM/power draw as attributes |
| RAM | % | Total RAM available as a separate `_ram_total` sensor (GiB) |
| SWAP | % | Only created if the system reports swap usage |
| Disk | % | Total disk size available as a separate `_disk_total` sensor (GiB) |
| Additional disks (EFS) | % | One per extra mounted disk reported by the agent |
| Bandwidth | MiB/s | Combined send and receive rate |
| Network Receive | KiB/s | |
| Network Send | KiB/s | |
| Temperature | °C | Only created if the system reports temperature sensors |
| Uptime | min | |
| Battery | % | Only created if the system reports battery data |

### Binary sensors
| Entity | Notes |
|---|---|
| Status | Connectivity — on when the system is reachable |
| S.M.A.R.T. | One per disk; "problem" state if the disk's S.M.A.R.T. health check has failed, with temperature, capacity, power-on hours/cycles, model, serial and firmware as attributes |

### Update entity
| Entity | Notes |
|---|---|
| Beszel Hub Update | Shows whether a newer Beszel Hub version is available (only created if update checking is enabled on your Hub) |

For example, if your machine is named *test*, CPU will be available as `sensor.test_cpu`.

## Examples

Here is one of my machines with the entities the integration currently exports:
![Screenshot from HomeAssistant settings page of my device and its entities](/pictures/sensors.png)

And here a card I created for myself using those sensors:
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

## Credits & License

- Original integration by Ronjar - [ronjar/beszel-ha](https://github.com/ronjar/beszel-ha)
- Beszel monitoring software by [henrygd](https://github.com/henrygd/beszel)
- This fork got extended with GitHub Copilot, Claude Sonnet 5 (Anthropic), and OpenAI Codex, maintained by [mojelumi0](https://github.com/mojelumi0)

Licensed under the MIT License — see [LICENSE](LICENSE)
