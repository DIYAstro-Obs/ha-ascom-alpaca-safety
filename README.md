# ASCOM Alpaca Safety (Safety Monitor) for Home Assistant

**ASCOM Alpaca Safety** is a sophisticated safety monitoring system for astronomical observatories. it aggregates various Home Assistant sensors and entities into a unified "Safe/Unsafe" status, which it then exposes to the ASCOM Alpaca ecosystem (via [ASCOM Alpaca Server](https://github.com/DIYAstro-Obs/ha-ascom-alpaca-server)).

![Alpaca Safety Logo](custom_components/ascom_alpaca_safety/brand/icon@2x.png)

> [!CAUTION]
> **AS-IS / EXPERIMENTAL**
> This project is provided "as-is" without any warranty. It has not yet been tested in a real-life observatory environment. Use it at your own risk.
>
> The project is **under active development**. Testing is very welcome! Please report any bugs by opening an [issue](https://github.com/DIYAstro-Obs/ha-ascom-alpaca-safety/issues). Contributions are also highly appreciated.

## Features

- **Logic Groups**: Organize safety rules into groups with configurable logic (`AND` / `OR`).
- **Flexible Rules**: Create rules based on any HA entity with numeric or state comparisons:
  - Operators: `>`, `<`, `>=`, `<=`, `==`, `!=`.
  - **Unsafe Delay**: Require a rule to be "unsafe" for a specified duration before it triggers an overall unsafe status.
  - **Settle Time**: Require the system to be "safe" for a sustained period (e.g., 15 minutes) before clearing the unsafe status (prevents rapid toggling).
- **Watchdog Protection**: Monitors if entities are `unavailable` or haven't updated within a timeout, triggering an unsafe state if data is stale.
- **Manual Overrides**: Includes virtual switches to "Force Unsafe" for maintenance or emergencies.
- **Server Integration**: Automatically registers itself as a `SafetyMonitor` device with the **ASCOM Alpaca Server** integration. Without the server, Safety runs in standalone mode: all Home Assistant entities keep working, but no Alpaca `SafetyMonitor` is exposed.

## Installation

1. *(Optional, required to expose the Safety Monitor via Alpaca)* Ensure the **ASCOM Alpaca Server** integration is installed and configured.
2. Copy the `custom_components/ascom_alpaca_safety` folder to your Home Assistant `custom_components` directory.
3. Restart Home Assistant.
4. Go to **Settings -> Devices & Services -> Add Integration** and search for **ASCOM Alpaca Safety**.

## Configuration

The configuration is handled via the integration's **Options** menu:
- Define groups and their logic.
- Add rules to groups, selecting HA entities and defining thresholds.
- Configure global and rule-specific timers (Settle Time, Unsafe Delay, Watchdog).

## Dashboard Entities

The integration provides several entities for your Home Assistant dashboard:
- **Master Safe Sensor**: A binary sensor showing the overall observatory safety status.
- **Group Safe Sensors**: Individual binary sensors for each logical group.
- **Force Unsafe Switch**: Toggle to manually trigger an unsafe state.
- **Force Safe Button**: Skips running settle timers. It has no effect while a rule is unsafe or data is missing, and it ends as soon as any group turns unsafe again.
