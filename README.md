# ASCOM Alpaca Safety (Safety Monitor) for Home Assistant

**ASCOM Alpaca Safety** is a sophisticated safety monitoring system for astronomical observatories. It aggregates various Home Assistant sensors and entities into a unified "Safe/Unsafe" status, which it then exposes to the ASCOM Alpaca ecosystem (via [ASCOM Alpaca Server](https://github.com/DIYAstro-Obs/ha-ascom-alpaca-server)).

Safety works on its own in Home Assistant (master sensor, reason sensor, group sensors and the event `ascom_alpaca_safety_changed`). The ASCOM Alpaca Server is only needed to expose it to astronomy software.

![Alpaca Safety Logo](custom_components/ascom_alpaca_safety/brand/icon@2x.png)

> [!CAUTION]
> **AS-IS / EXPERIMENTAL**
> This project is provided "as-is" without any warranty. It has not yet been tested in a real-life observatory environment. Use it at your own risk.
>
> The project is **under active development**: breaking changes are possible and even likely. Options, entity names and IDs, and the interface between the integrations may change from one version to the next, and you may have to set things up again. Testing is very welcome! Please report any bugs by opening an [issue](https://github.com/DIYAstro-Obs/ha-ascom-alpaca-safety/issues). Contributions are also highly appreciated.

This project is not affiliated with or endorsed by the ASCOM Initiative.

## Features

- **Logic Groups**: Organize safety rules into groups with configurable logic (`AND` / `OR`).
- **Flexible Rules**: Create rules based on any HA entity with numeric or state comparisons:
  - Operators: `>`, `<`, `>=`, `<=`, `==`, `!=`, `in`, `not in` (a comma separated list such as `rainy, pouring, lightning`).
  - A rule can watch the **state** of an entity or one of its **attributes** (the wind speed of a weather entity, the elevation of the sun, ...).
  - **Unsafe Delay**: Require a rule to be "unsafe" for a specified duration before it triggers an overall unsafe status.
  - **Settle Time** (per group, default 300 s, 0 = no waiting): Require the group to be "safe" for a sustained period before clearing the unsafe status (prevents rapid toggling).
- **Watchdog Protection**: Monitors if entities are `unavailable` or haven't updated within a timeout, triggering an unsafe state if data is stale. An optional **grace time** per rule waits for an entity that is only unavailable for a moment.
- **Manual Overrides**: Includes virtual switches to "Force Unsafe" for maintenance or emergencies (it stays on over restarts and reloads until you switch it off), and "Manual Safe" to report SAFE while you watch the sky yourself because a sensor has failed (it ends after the hours you choose).
- **Automations**: An event, a reason sensor and a diagnostics download tell why the monitor is safe or unsafe.
- **Server Integration**: Automatically registers itself as a `SafetyMonitor` device with the **ASCOM Alpaca Server** integration. Without the server, Safety runs in standalone mode: all Home Assistant entities keep working, but no Alpaca `SafetyMonitor` is exposed.

## Installation

**ASCOM Alpaca Server** (optional, but required to expose the Safety Monitor via Alpaca): install the [ASCOM Alpaca Server](https://github.com/DIYAstro-Obs/ha-ascom-alpaca-server) integration, **version 0.11.0 or newer** (older versions do not keep `Connected` per client, and astronomy software cannot connect to the Safety Monitor). When Home Assistant starts, the Server is set up first (`after_dependencies`) and Safety registers with it at once. If it is missing, Safety runs in standalone mode and shows a notification; it registers as soon as the Server is added.

### Via HACS (recommended)

1. Make sure [HACS](https://hacs.xyz/) is installed.
2. Open **HACS**, click the three dots in the top right corner and select **Custom repositories**.
3. Paste `https://github.com/DIYAstro-Obs/ha-ascom-alpaca-safety`, select **Integration** as the category and click **Add**.
4. Find **ASCOM Alpaca Safety** in the list and click **Download**.
5. Restart Home Assistant.

### Manual installation

1. Copy the `custom_components/ascom_alpaca_safety` folder to the `custom_components` directory of your Home Assistant.
2. Restart Home Assistant.

Then go to **Settings -> Devices & Services -> Add Integration** and search for **ASCOM Alpaca Safety**.

## Configuration

The configuration is handled via the integration's **Options** menu:
- Define groups and their logic.
- Add rules to groups, selecting HA entities and defining thresholds.
- Configure global and rule-specific timers (Settle Time, Unsafe Delay, Watchdog).

The user interface is English only; the project does not provide translations.

### Rules

- A rule describes the **unsafe** condition: `Rain sensor == on` means "unsafe while it rains".
- `binary_sensor`, `switch`, `input_boolean` and `light` entities only know `on` and `off`: use `==` or `!=` with `on` or `off`. The options flow refuses other values. Numbers need `>`, `<`, `>=` or `<=` with a number as the threshold. Other states (for example `rainy` of a weather entity) are compared without regard to upper or lower case.
- Adding or editing a rule takes up to three steps: first the entity; then, for an entity that has attributes, **what to watch** (its state or one of the attributes, shown with their current values); then operator, threshold and timers. The last step shows the **current value** and offers the states in a list (on / off, the conditions of a weather entity, the options of a select). In the group menu every rule shows what it says right now, for example `Rain sensor == on [now off -> safe]`.
- **Attributes**: the value of an attribute can be anything, so all operators are available for it and the threshold is a free text. A rule on an attribute that is missing counts as unsafe (the group says `... missing`).
- **Lists**: `in` is unsafe while the value is one of the entries, `not in` while it is none of them, for example `weather.home in rainy, pouring, lightning`. Entries are compared like `==` (numbers as numbers, text without regard to case). The state of a binary sensor, switch or light only knows on and off: use `==` or `!=` there.
- A rule that cannot be evaluated counts as unsafe, an entity that is `unavailable` or `unknown` as well.
- **Unavailable grace time** (per rule, default 0 = unsafe at once): an entity that is `unavailable` or `unknown` for less than this time does not change the rule: it keeps its last result, and a cloud sensor that drops out for a minute does not start the settle time of its group. After the time has passed, the rule counts as unsafe. The grace time does not apply at the start of the monitor (nothing is known about the entity then) and the watchdog runs on its own: the one that comes first wins.
- A rule on an entity that **does not exist or is disabled** reports unsafe and raises an issue under *Settings → System → Repairs* ("Safety rule: entity not found") until you fix or delete the rule. The check runs every 30 seconds once Home Assistant has started.
- A group **without rules** monitors nothing and reports unsafe (so does a monitor without any group).
- **Watchdog timeout**: "unsafe if the entity has not reported for N seconds". Only entities that report again and again (a `sensor`, a weather entity) can go stale: for them the default is 300 s. A binary sensor or a switch only reports when its state changes, an unchanged value is not stale, so the default is off (0) for everything but sensors and weather. Leave the field empty to get that default; if you enter a watchdog for a binary sensor, it will report "watchdog expired" after that time without a change.
- The watchdog is checked every 30 seconds; for a timeout below a minute it is checked twice per timeout (at least once a second), so a short timeout is not noticed late.
- **AND groups** report unsafe as soon as one entity of the group is missing, unavailable or has not reported yet, not only when all rules trigger: a rule without a value cannot say "safe". Use AND for redundant sensors only when each of them reports reliably.

### Behaviour at start

Every start of the monitor begins with all groups **unsafe**: a restart of Home Assistant, but also **saving the options**, which reloads the integration. The monitor does not know what happened before, so it treats the start like a recovery from "unsafe". A group reports safe again once all its entities have reported and all its rules are safe for the **settle time** of the group (default 300 s; set it per group, 0 means no waiting). While it waits, the group sensor shows "Settling (…)". The **Skip Settle Time** button skips the waiting for groups whose rules are really safe; it never overrides an unsafe rule or missing data. The settle time of groups you created before has not changed; edit the group to change it.

## Dashboard Entities

The integration provides several entities for your Home Assistant dashboard:
- **Master Safety Sensor** ("Observatory Safety"): A binary sensor showing the overall observatory safety status. Like every Home Assistant safety sensor, **on means unsafe** and off means safe, shown as "Unsafe" and "Safe".
- **Reason Sensor**: A text that says why the monitor is safe or unsafe (the description of the master state). A state holds 255 characters at most: the complete text is in the attribute `description`.
- **Group Safe Sensors**: Individual binary sensors for each logical group. While a group settles, the attribute `settle_ends_at` holds the time it reports safe again (a time, not a countdown: the sensor is only written when something changes). The attribute `settle_remaining` of earlier versions is gone.
- **Force Unsafe Switch**: Toggle to manually trigger an unsafe state (maintenance mode). It is stored: it stays on over restarts and reloads.
- **Skip Settle Time Button** (called Force Safe before): Skips running settle timers. It has no effect while a rule is unsafe or data is missing, and it ends as soon as any group turns unsafe again.
- **Manual Safe Switch** and **Manual Safe Duration**: see below.

### Manual Safe (override)

A failed sensor keeps its group, and with it the monitor, on UNSAFE for good: that is the safe default. If you watch the sky yourself meanwhile, **Manual Safe (Override)** makes the monitor report **SAFE whatever the groups and rules say**; `IsSafe` in Alpaca follows. The reason sensor shows `SAFE: MANUAL OVERRIDE until …`, the master sensor has the attributes `override: manual_safe` and `override_until`, and the event `ascom_alpaca_safety_changed` is sent.

> [!WARNING]
> The override ignores **every** sensor, including a working rain sensor. Use it only when you are watching yourself.

- **Manual Safe Duration** (hours, default 12, 0 to 168) is how long the override lasts from the moment you switch it on. A running override keeps its end when you change the number. **0 means until you switch it off**: you can forget that.
- When the time is over (or you switch it off) the real state counts again, which can be UNSAFE at once.
- The override stays on over restarts and reloads (saving the options) and keeps its end time; an end that passed while Home Assistant was off is dropped.
- **Force Unsafe always wins**: switching it on ends Manual Safe, and Manual Safe cannot be switched on while Force Unsafe is on.
- Do not mix it up with **Skip Settle Time**: that button only skips the waiting of groups whose rules are really safe and never overrides an unsafe rule.

## Automations

Whenever the monitor changes between safe and unsafe, it fires the event `ascom_alpaca_safety_changed` with the data `is_safe` (`true` / `false`) and `reason` (the text of the reason sensor). Every start of the monitor sends one too (see *Behaviour at start*: it starts unsafe). A change of the reason while the monitor stays unsafe sends nothing.

```yaml
triggers:
  - trigger: event
    event_type: ascom_alpaca_safety_changed
    event_data:
      is_safe: false
actions:
  - action: notify.notify
    data:
      message: "Observatory unsafe: {{ trigger.event.data.reason }}"
```

**Diagnostics**: *Settings -> Devices & services -> ASCOM Alpaca Safety -> ... -> Download diagnostics* contains the configuration and the current state of every group and rule (value, triggered, unsafe, watchdog, availability) for bug reports.

## Alpaca behaviour

- `Connected` is kept per client by the **ASCOM Alpaca Server** (not by Safety): a client that disconnects does not disconnect another one. This needs **ASCOM Alpaca Server 0.11.0 or newer**.
- Without a group the SafetyMonitor is still there: it reports `IsSafe = false` (reason "No safety groups configured") instead of disappearing, so that astronomy software never runs without a monitor unnoticed.
- `IsSafe` answers whether or not the client has connected (ASCOM would answer `NotConnected` before). This is meant: saving the options reloads Safety, and clients that are connected must not be cut off from the monitor by an error.
- Safety implements the interface version 1 of the SafetyMonitor. The members of ASCOM Platform 7 (`Connect`, `Disconnect`, `Connecting`, `DeviceState`) are not implemented: clients use them only for devices that report interface version 3, and every client still supports the classic `Connected`.

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
