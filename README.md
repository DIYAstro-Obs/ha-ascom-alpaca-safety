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
  - **Settle Time** (per group, default 300 s, 0 = no waiting): Require the group to be "safe" for a sustained period before clearing the unsafe status (prevents rapid toggling).
- **Watchdog Protection**: Monitors if entities are `unavailable` or haven't updated within a timeout, triggering an unsafe state if data is stale.
- **Manual Overrides**: Includes virtual switches to "Force Unsafe" for maintenance or emergencies (it stays on over restarts and reloads until you switch it off).
- **Server Integration**: Automatically registers itself as a `SafetyMonitor` device with the **ASCOM Alpaca Server** integration. Without the server, Safety runs in standalone mode: all Home Assistant entities keep working, but no Alpaca `SafetyMonitor` is exposed.

## Installation

1. *(Optional, required to expose the Safety Monitor via Alpaca)* Ensure the **ASCOM Alpaca Server** integration is installed and configured. When Home Assistant starts, the Server is set up first (`after_dependencies`) and Safety registers with it at once. If it is missing, Safety runs in standalone mode and shows a notification; it registers as soon as the Server is added.
2. Copy the `custom_components/ascom_alpaca_safety` folder to your Home Assistant `custom_components` directory.
3. Restart Home Assistant.
4. Go to **Settings -> Devices & Services -> Add Integration** and search for **ASCOM Alpaca Safety**.

## Configuration

The configuration is handled via the integration's **Options** menu:
- Define groups and their logic.
- Add rules to groups, selecting HA entities and defining thresholds.
- Configure global and rule-specific timers (Settle Time, Unsafe Delay, Watchdog).

### Rules

- A rule describes the **unsafe** condition: `Rain sensor == on` means "unsafe while it rains".
- `binary_sensor`, `switch`, `input_boolean` and `light` entities only know `on` and `off`: use `==` or `!=` with `on` or `off`. The options flow refuses other values. Numbers need `>`, `<`, `>=` or `<=` with a number as the threshold. Other states (for example `rainy` of a weather entity) are compared without regard to upper or lower case.
- Adding or editing a rule takes two steps: first the entity, then operator, threshold and timers. The second step shows the **current state** of the entity and offers its states in a list (on / off, the conditions of a weather entity, the options of a select). In the group menu every rule shows what it says right now, for example `Rain sensor == on [now off -> safe]`.
- A rule that cannot be evaluated counts as unsafe, an entity that is `unavailable` or `unknown` as well.
- A rule on an entity that **does not exist or is disabled** reports unsafe and raises an issue under *Settings → System → Repairs* ("Safety rule: entity not found") until you fix or delete the rule. The check runs every 30 seconds once Home Assistant has started.
- A group **without rules** monitors nothing and reports unsafe (so does a monitor without any group).
- **Watchdog timeout**: "unsafe if the entity has not reported for N seconds". Only entities that report again and again (a `sensor`, a weather entity) can go stale: for them the default is 300 s. A binary sensor or a switch only reports when its state changes, an unchanged value is not stale, so the default is off (0) for everything but sensors and weather. Leave the field empty to get that default; if you enter a watchdog for a binary sensor, it will report "watchdog expired" after that time without a change.
- The watchdog is checked every 30 seconds, a timeout below that is detected up to 30 seconds late.

### Behaviour at start

Every start of the monitor begins with all groups **unsafe**: a restart of Home Assistant, but also **saving the options**, which reloads the integration. The monitor does not know what happened before, so it treats the start like a recovery from "unsafe". A group reports safe again once all its entities have reported and all its rules are safe for the **settle time** of the group (default 300 s; set it per group, 0 means no waiting). While it waits, the group sensor shows "Settling (…)". The **Force Safe** button skips the waiting for groups whose rules are really safe; it never overrides an unsafe rule or missing data. The settle time of groups you created before has not changed; edit the group to change it.

## Dashboard Entities

The integration provides several entities for your Home Assistant dashboard:
- **Master Safe Sensor**: A binary sensor showing the overall observatory safety status.
- **Group Safe Sensors**: Individual binary sensors for each logical group. While a group settles, the attribute `settle_ends_at` holds the time it reports safe again (a time, not a countdown: the sensor is only written when something changes). The attribute `settle_remaining` of earlier versions is gone.
- **Force Unsafe Switch**: Toggle to manually trigger an unsafe state (maintenance mode). It is stored: it stays on over restarts and reloads.
- **Force Safe Button**: Skips running settle timers. It has no effect while a rule is unsafe or data is missing, and it ends as soon as any group turns unsafe again.

## Development

Run the unit tests with `python -m pytest`. Only `pytest` is required; Home Assistant is stubbed (see `tests/ha_stubs.py`).

## Development Deployment

`deploy.ps1` copies the integration to `/config/custom_components/` on your Home Assistant via SCP and restarts Home Assistant Core. Copy `deployconf` to `deployconf.secrets` (ignored by git) and set:

| Key | Purpose |
|-----|---------|
| `SSH_URL` | Host or `host:port` of the SSH add-on (required, port defaults to 22) |
| `SSH_USER` | SSH user (default `root`) |
| `SSH_PW` | Optional SSH password. Stored in plain text, so use it only for test systems. Leave empty to be asked on every deploy. |

`HA_URL` and `HA_TOKEN` may stay in the file, but `deploy.ps1` does not use them.

Run `.\deploy.ps1 -DryRun` first: it prints the target (`user@host:port`), the auth mode and the planned steps without connecting.
