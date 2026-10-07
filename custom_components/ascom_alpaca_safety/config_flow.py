"""Config flow and Options flow for ASCOM Alpaca Safety."""

from __future__ import annotations

import logging
import uuid
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import entity_registry as er, selector

from .const import (
    CONF_GROUPS,
    CONF_GROUP_ID,
    CONF_GROUP_LOGIC,
    CONF_GROUP_NAME,
    CONF_GROUP_SETTLE_TIME,
    CONF_RULE_ATTRIBUTE,
    CONF_RULE_ENTITY,
    CONF_RULE_OPERATOR,
    CONF_RULE_THRESHOLD,
    CONF_RULE_UNAVAILABLE_DELAY,
    CONF_RULE_UNSAFE_DELAY,
    CONF_RULE_WATCHDOG_TIMEOUT,
    CONF_RULES,
    DEFAULT_SETTLE_TIME,
    DEFAULT_UNSAFE_DELAY,
    DOMAIN,
    LOGIC_AND,
    LOGIC_OR,
    OPERATOR_LABELS,
    OPERATORS,
    SOURCE_STATE,
)
from .rules import (
    UNAVAILABLE_STATES,
    attribute_choices,
    default_watchdog_timeout,
    group_name_error,
    operators_for,
    rule_triggered,
    threshold_choices,
    threshold_error,
    watched_value,
)

_LOGGER = logging.getLogger(__name__)


class AlpacaSafetyConfigFlow(
    config_entries.ConfigFlow, domain=DOMAIN
):
    """Handle initial config flow for ASCOM Alpaca Safety."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial setup step — simple confirmation."""
        # Only allow one instance
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()

        if user_input is not None:
            return self.async_create_entry(
                title="ASCOM Alpaca Safety",
                data={},
            )

        return self.async_show_form(step_id="user")

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> AlpacaSafetyOptionsFlow:
        """Get the options flow."""
        return AlpacaSafetyOptionsFlow(config_entry)


class AlpacaSafetyOptionsFlow(config_entries.OptionsFlow):
    """Handle options flow for dynamic group/rule management."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self._config_entry = config_entry
        self._groups: list[dict[str, Any]] = []
        self._current_group_index: int | None = None
        self._current_rule_index: int | None = None  # None while a rule is added
        self._rule_entity: str | None = None  # chosen in the first step of a rule
        self._rule_attribute: str = ""  # chosen in the second step: "" = the state of the entity

    def _load_groups(self) -> None:
        """Load current groups from options."""
        import copy
        self._groups = copy.deepcopy(
            self._config_entry.options.get(CONF_GROUPS, [])
        )

    def _rule_label(self, rule: dict[str, Any]) -> str:
        """Build a human-readable label for a rule menu entry."""
        entity_id: str = rule.get(CONF_RULE_ENTITY, "")
        attribute: str = rule.get(CONF_RULE_ATTRIBUTE) or ""
        op_str: str = rule.get(CONF_RULE_OPERATOR, "?")
        threshold = rule.get(CONF_RULE_THRESHOLD, "?")

        # Try to get the friendly name from the entity registry
        friendly_name: str = entity_id
        try:
            ent_reg = er.async_get(self.hass)
            entry = ent_reg.async_get(entity_id)
            if entry and entry.name:
                friendly_name = entry.name
            elif entry and entry.original_name:
                friendly_name = entry.original_name
            else:
                # Fall back to state's friendly name attribute
                state = self.hass.states.get(entity_id)
                if state:
                    friendly_name = state.name
        except Exception:
            pass

        # What the rule says right now: helps to see at a glance whether a rule is right
        state = self.hass.states.get(entity_id)
        value = watched_value(state.state, state.attributes, attribute) if state else None
        if state is None or state.state in UNAVAILABLE_STATES:
            now = f"{state.state if state else 'missing'} -> UNSAFE"
        elif value is None:
            now = "missing -> UNSAFE"
        else:
            now = f"{value} -> " + (
                "safe"
                if rule_triggered(op_str, threshold, value) is False
                else "UNSAFE"
            )
        what = f"{friendly_name} [{attribute}]" if attribute else friendly_name
        return f"{what}  {op_str}  {threshold}   [now {now}]"

    def _save_options(self) -> dict[str, Any]:
        """Build the complete options dict."""
        options = dict(self._config_entry.options)
        options[CONF_GROUPS] = self._groups
        return options

    # ---- Main Menu ----

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Show main menu with groups list."""
        self._load_groups()

        if user_input is not None:
            choice = user_input.get("menu")
            if choice == "add_group":
                return await self.async_step_add_group()
            if choice and choice.startswith("edit_group_"):
                try:
                    idx = int(choice.replace("edit_group_", ""))
                    self._current_group_index = idx
                    return await self.async_step_edit_group()
                except (ValueError, IndexError):
                    pass

        # Build menu options
        menu_options = {
            "add_group": "➕ Add new group",
        }
        for i, group in enumerate(self._groups):
            name = group.get(CONF_GROUP_NAME, f"Group {i}")
            logic = group.get(CONF_GROUP_LOGIC, LOGIC_OR)
            rules_count = len(group.get(CONF_RULES, []))
            menu_options[f"edit_group_{i}"] = (
                f"📋 {name} ({logic}, {rules_count} rules)"
            )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required("menu"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                selector.SelectOptionDict(value=k, label=v)
                                for k, v in menu_options.items()
                            ],
                            mode=selector.SelectSelectorMode.LIST,
                        )
                    ),
                }
            ),
        )

    # ---- Add Group ----

    async def async_step_add_group(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Add a new safety group."""
        errors: dict[str, str] = {}

        if user_input is not None:
            name = user_input.get(CONF_GROUP_NAME, "").strip()
            name_error = self._group_name_error(name)
            if name_error:
                errors[CONF_GROUP_NAME] = name_error
            else:
                new_group = {
                    CONF_GROUP_ID: uuid.uuid4().hex[:8],
                    CONF_GROUP_NAME: name,
                    CONF_GROUP_LOGIC: user_input.get(CONF_GROUP_LOGIC, LOGIC_OR),
                    CONF_GROUP_SETTLE_TIME: int(
                        user_input.get(CONF_GROUP_SETTLE_TIME, DEFAULT_SETTLE_TIME)
                    ),
                    CONF_RULES: [],
                }
                self._groups.append(new_group)
                self._current_group_index = len(self._groups) - 1
                return await self.async_step_edit_group()

        return self.async_show_form(
            step_id="add_group",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_GROUP_NAME): selector.TextSelector(),
                    vol.Required(
                        CONF_GROUP_LOGIC, default=LOGIC_OR
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                selector.SelectOptionDict(
                                    value=LOGIC_OR,
                                    label="OR (any rule triggers unsafe)",
                                ),
                                selector.SelectOptionDict(
                                    value=LOGIC_AND,
                                    label="AND (all rules must trigger)",
                                ),
                            ],
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    ),
                    vol.Required(
                        CONF_GROUP_SETTLE_TIME,
                        default=DEFAULT_SETTLE_TIME,
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0,
                            max=7200,
                            step=1,
                            unit_of_measurement="s",
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    ),
                }
            ),
            errors=errors if errors else None,
        )

    # ---- Edit Group (rule list) ----

    async def async_step_edit_group(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Show rules in a group + management options."""
        idx = self._current_group_index
        if idx is None or idx >= len(self._groups):
            return await self.async_step_init()

        group = self._groups[idx]

        if user_input is not None:
            choice = user_input.get("menu")
            if choice == "add_rule":
                self._current_rule_index = None
                return await self.async_step_rule_entity()
            if choice == "edit_group_settings":
                return await self.async_step_edit_group_settings()
            if choice == "delete_group":
                return await self.async_step_delete_group()
            if choice == "back":
                options = self._save_options()
                return self.async_create_entry(title="", data=options)
            if choice and choice.startswith("edit_rule_"):
                try:
                    r_idx = int(choice.replace("edit_rule_", ""))
                    self._current_rule_index = r_idx
                    return await self.async_step_rule_entity()
                except (ValueError, IndexError):
                    pass
            if choice and choice.startswith("delete_rule_"):
                try:
                    r_idx = int(choice.replace("delete_rule_", ""))
                    self._current_rule_index = r_idx
                    return await self.async_step_delete_rule()
                except (ValueError, IndexError):
                    pass

        name = group.get(CONF_GROUP_NAME, "Unnamed")
        rules = group.get(CONF_RULES, [])

        menu_options = {
            "add_rule": "➕ Add new rule",
            "edit_group_settings": "⚙️ Edit group settings",
        }

        for i, rule in enumerate(rules):
            label = self._rule_label(rule)
            menu_options[f"edit_rule_{i}"] = f"📏 {label}"
            menu_options[f"delete_rule_{i}"] = f"🗑️ Delete: {label}"

        menu_options["delete_group"] = "🗑️ Delete this group"
        if any(not g.get(CONF_RULES) for g in self._groups):
            menu_options["back"] = (
                "💾 Save & Close (a group without rules reports UNSAFE)"
            )
        else:
            menu_options["back"] = "💾 Save & Close"

        return self.async_show_form(
            step_id="edit_group",
            data_schema=vol.Schema(
                {
                    vol.Required("menu"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                selector.SelectOptionDict(value=k, label=v)
                                for k, v in menu_options.items()
                            ],
                            mode=selector.SelectSelectorMode.LIST,
                        )
                    ),
                }
            ),
            description_placeholders={"group_name": name},
        )

    # ---- Edit Group Settings ----

    async def async_step_edit_group_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Edit name, logic, settle time of current group."""
        idx = self._current_group_index
        if idx is None or idx >= len(self._groups):
            return await self.async_step_init()

        group = self._groups[idx]

        errors: dict[str, str] = {}
        if user_input is not None:
            name = user_input.get(CONF_GROUP_NAME, "").strip()
            name_error = self._group_name_error(name, skip_index=idx)
            if name_error:
                errors[CONF_GROUP_NAME] = name_error
            else:
                group[CONF_GROUP_NAME] = name
                group[CONF_GROUP_LOGIC] = user_input.get(
                    CONF_GROUP_LOGIC, group[CONF_GROUP_LOGIC]
                )
                group[CONF_GROUP_SETTLE_TIME] = int(
                    user_input.get(
                        CONF_GROUP_SETTLE_TIME, group[CONF_GROUP_SETTLE_TIME]
                    )
                )
                return await self.async_step_edit_group()

        shown = user_input or group
        return self.async_show_form(
            step_id="edit_group_settings",
            errors=errors or None,
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_GROUP_NAME,
                        default=shown.get(CONF_GROUP_NAME, ""),
                    ): selector.TextSelector(),
                    vol.Required(
                        CONF_GROUP_LOGIC,
                        default=shown.get(CONF_GROUP_LOGIC, LOGIC_OR),
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                selector.SelectOptionDict(
                                    value=LOGIC_OR,
                                    label="OR (any rule triggers unsafe)",
                                ),
                                selector.SelectOptionDict(
                                    value=LOGIC_AND,
                                    label="AND (all rules must trigger)",
                                ),
                            ],
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    ),
                    vol.Required(
                        CONF_GROUP_SETTLE_TIME,
                        default=shown.get(CONF_GROUP_SETTLE_TIME, DEFAULT_SETTLE_TIME),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0,
                            max=7200,
                            step=1,
                            unit_of_measurement="s",
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    ),
                }
            ),
        )

    # ---- Add / edit a rule: first the entity, then the details ----

    async def async_step_rule_entity(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 1 of a rule: choose the entity. The next steps show what it reports and offer its states."""
        group = self._current_group()
        if group is None:
            return await self.async_step_init()

        if user_input is not None:
            self._rule_entity = user_input[CONF_RULE_ENTITY]
            return await self.async_step_rule_source()

        rule = self._current_rule(group)
        entity_kwargs: dict[str, Any] = {}
        if rule and rule.get(CONF_RULE_ENTITY):
            entity_kwargs["default"] = rule[CONF_RULE_ENTITY]

        return self.async_show_form(
            step_id="rule_entity",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_RULE_ENTITY, **entity_kwargs
                    ): selector.EntitySelector(),
                }
            ),
            description_placeholders={
                "group_name": group.get(CONF_GROUP_NAME, "Unnamed")
            },
        )

    async def async_step_rule_source(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 2 of a rule: watch the state of the entity or one of its attributes.

        An entity without attributes worth watching goes straight to the next step.
        """
        group = self._current_group()
        entity_id = self._rule_entity
        if group is None or not entity_id:
            return await self.async_step_rule_entity()

        if user_input is not None:
            choice = user_input.get(CONF_RULE_ATTRIBUTE, SOURCE_STATE)
            self._rule_attribute = "" if choice == SOURCE_STATE else str(choice).strip()
            return await self.async_step_rule_details()

        rule = self._current_rule(group)
        stored = ""
        if rule is not None and rule.get(CONF_RULE_ENTITY) == entity_id:
            stored = rule.get(CONF_RULE_ATTRIBUTE) or ""

        state = self.hass.states.get(entity_id)
        attributes = state.attributes if state else {}
        names = attribute_choices(attributes, stored)
        if not names:
            self._rule_attribute = ""
            return await self.async_step_rule_details()

        options = [selector.SelectOptionDict(value=SOURCE_STATE, label="The state of the entity")]
        for name in names:
            now = str(attributes[name])[:40] if name in attributes else "not present now"
            options.append(selector.SelectOptionDict(value=name, label=f"{name}  (now: {now})"))

        return self.async_show_form(
            step_id="rule_source",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_RULE_ATTRIBUTE, default=stored or SOURCE_STATE
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=options,
                            custom_value=True,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    ),
                }
            ),
            description_placeholders={
                "entity_id": entity_id,
                "current_state": self._state_text(state),
            },
        )

    async def async_step_rule_details(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 3 of a rule: operator, threshold, delays and watchdog, for what the rule watches."""
        group = self._current_group()
        entity_id = self._rule_entity
        if group is None or not entity_id:
            return await self.async_step_rule_entity()

        attribute = self._rule_attribute
        rule = self._current_rule(group)
        errors: dict[str, str] = {}
        if user_input is not None:
            values = {
                **user_input,
                CONF_RULE_ENTITY: entity_id,
                CONF_RULE_ATTRIBUTE: attribute,
            }
            errors = self._rule_errors(values)
            if not errors:
                new_rule = self._rule_from_input(values)
                if rule is not None:
                    rule.update(new_rule)
                else:
                    group.setdefault(CONF_RULES, []).append(new_rule)
                return await self.async_step_edit_group()

        # Show what the user typed after an error; when editing the same entity: the stored rule
        if user_input is not None:
            defaults = user_input
        elif rule is not None and rule.get(CONF_RULE_ENTITY) == entity_id:
            defaults = rule
        else:
            defaults = {}

        state = self.hass.states.get(entity_id)
        return self.async_show_form(
            step_id="rule_details",
            data_schema=self._rule_details_schema(entity_id, state, defaults, attribute),
            errors=errors or None,
            description_placeholders={
                "entity_id": f"{entity_id} [{attribute}]" if attribute else entity_id,
                "current_state": self._state_text(state, attribute),
            },
        )

    # ---- Delete Group ----

    async def async_step_delete_group(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Confirm group deletion."""
        idx = self._current_group_index
        if idx is None or idx >= len(self._groups):
            return await self.async_step_init()

        if user_input is not None:
            if user_input.get("confirm"):
                del self._groups[idx]
                self._current_group_index = None
                options = self._save_options()
                return self.async_create_entry(title="", data=options)
            return await self.async_step_edit_group()

        name = self._groups[idx].get(CONF_GROUP_NAME, "Unnamed")
        return self.async_show_form(
            step_id="delete_group",
            data_schema=vol.Schema(
                {
                    vol.Required("confirm", default=False): selector.BooleanSelector(),
                }
            ),
            description_placeholders={"group_name": name},
        )

    # ---- Delete Rule ----

    async def async_step_delete_rule(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Confirm rule deletion."""
        g_idx = self._current_group_index
        r_idx = self._current_rule_index
        if (
            g_idx is None
            or g_idx >= len(self._groups)
            or r_idx is None
            or r_idx >= len(self._groups[g_idx].get(CONF_RULES, []))
        ):
            return await self.async_step_edit_group()

        if user_input is not None:
            if user_input.get("confirm"):
                del self._groups[g_idx][CONF_RULES][r_idx]
                self._current_rule_index = None
                options = self._save_options()
                return self.async_create_entry(title="", data=options)
            return await self.async_step_edit_group()

        rule = self._groups[g_idx][CONF_RULES][r_idx]
        entity = rule.get(CONF_RULE_ENTITY, "?")

        return self.async_show_form(
            step_id="delete_rule",
            data_schema=vol.Schema(
                {
                    vol.Required("confirm", default=False): selector.BooleanSelector(),
                }
            ),
            description_placeholders={"rule_entity": entity},
        )

    # ---- Helpers ----

    @staticmethod
    def _rule_errors(user_input: dict[str, Any]) -> dict[str, str]:
        """Reject a rule that can never work (threshold that cannot match the operator or entity)."""
        error = threshold_error(
            user_input.get(CONF_RULE_ENTITY, ""),
            user_input.get(CONF_RULE_OPERATOR, ">"),
            user_input.get(CONF_RULE_THRESHOLD, ""),
            user_input.get(CONF_RULE_ATTRIBUTE, ""),
        )
        return {CONF_RULE_THRESHOLD: error} if error else {}

    @staticmethod
    def _rule_from_input(user_input: dict[str, Any]) -> dict[str, Any]:
        """Build the stored rule from the form input.

        An empty watchdog field gets the default of the entity type: 300 s for sensors and
        weather, off for everything that only reports when it changes (binary sensors, switches, ...).
        """
        entity_id = user_input.get(CONF_RULE_ENTITY, "")
        watchdog = user_input.get(CONF_RULE_WATCHDOG_TIMEOUT)
        return {
            CONF_RULE_ENTITY: entity_id,
            CONF_RULE_ATTRIBUTE: user_input.get(CONF_RULE_ATTRIBUTE, ""),
            CONF_RULE_OPERATOR: user_input.get(CONF_RULE_OPERATOR, ">"),
            CONF_RULE_THRESHOLD: str(user_input.get(CONF_RULE_THRESHOLD, "0")).strip(),
            CONF_RULE_UNSAFE_DELAY: int(
                user_input.get(CONF_RULE_UNSAFE_DELAY, DEFAULT_UNSAFE_DELAY)
            ),
            CONF_RULE_UNAVAILABLE_DELAY: int(
                user_input.get(CONF_RULE_UNAVAILABLE_DELAY) or 0
            ),
            CONF_RULE_WATCHDOG_TIMEOUT: (
                default_watchdog_timeout(entity_id)
                if watchdog in (None, "")
                else int(watchdog)
            ),
        }

    def _current_group(self) -> dict[str, Any] | None:
        idx = self._current_group_index
        if idx is None or idx >= len(self._groups):
            return None
        return self._groups[idx]

    def _current_rule(self, group: dict[str, Any]) -> dict[str, Any] | None:
        """The rule that is edited, or None while a rule is added."""
        r_idx = self._current_rule_index
        rules = group.get(CONF_RULES, [])
        if r_idx is None or r_idx >= len(rules):
            return None
        return rules[r_idx]

    def _group_name_error(self, name: str, skip_index: int | None = None) -> str | None:
        """Name of a group: not empty, not used by another group."""
        others = [
            group.get(CONF_GROUP_NAME, "")
            for i, group in enumerate(self._groups)
            if i != skip_index
        ]
        return group_name_error(name, others)

    @staticmethod
    def _state_text(state: Any, attribute: str = "") -> str:
        """What the rule watches now (the state or the attribute), for the form text."""
        if state is None:
            return "none (the entity does not exist or has no state yet)"
        if attribute:
            value = state.attributes.get(attribute)
            return str(value) if value is not None else f"none (no attribute '{attribute}')"
        unit = state.attributes.get("unit_of_measurement")
        return f"{state.state} {unit}" if unit else str(state.state)

    @staticmethod
    def _rule_details_schema(
        entity_id: str, state: Any, defaults: dict[str, Any], attribute: str = ""
    ) -> vol.Schema:
        """Schema of the last rule step: the choices follow what the rule watches (on/off, weather, selects)."""
        allowed = operators_for(entity_id, attribute)
        operator_options = [
            selector.SelectOptionDict(value=o, label=OPERATOR_LABELS[o])
            for o in OPERATORS
            if allowed is None or o in allowed
        ]
        operator_default = defaults.get(CONF_RULE_OPERATOR)
        if operator_default not in [o["value"] for o in operator_options]:
            operator_default = operator_options[0]["value"] if allowed else ">"

        choices = threshold_choices(
            entity_id, state.attributes if state else None, attribute
        )
        threshold_default = str(defaults.get(CONF_RULE_THRESHOLD, "")).strip()
        if choices is not None:
            options, free_text = choices
            if not free_text:
                # on / off only: the stored value in the spelling of the list
                threshold_default = threshold_default.casefold()
            if not threshold_default or (not free_text and threshold_default not in options):
                threshold_default = options[0]
            threshold_selector: Any = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=options,
                    custom_value=free_text,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            )
        else:
            threshold_default = threshold_default or "0"
            threshold_selector = selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
            )

        # No default when adding a rule: left empty the watchdog becomes the default of the entity type
        watchdog_kwargs: dict[str, Any] = {}
        if defaults.get(CONF_RULE_WATCHDOG_TIMEOUT) not in (None, ""):
            watchdog_kwargs["default"] = defaults[CONF_RULE_WATCHDOG_TIMEOUT]

        return vol.Schema(
            {
                vol.Required(
                    CONF_RULE_OPERATOR, default=operator_default
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=operator_options,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(
                    CONF_RULE_THRESHOLD, default=threshold_default
                ): threshold_selector,
                vol.Optional(
                    CONF_RULE_UNSAFE_DELAY,
                    default=defaults.get(CONF_RULE_UNSAFE_DELAY, DEFAULT_UNSAFE_DELAY),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0,
                        max=3600,
                        step=1,
                        unit_of_measurement="s",
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
                vol.Optional(
                    CONF_RULE_UNAVAILABLE_DELAY,
                    default=defaults.get(CONF_RULE_UNAVAILABLE_DELAY, 0),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0,
                        max=3600,
                        step=1,
                        unit_of_measurement="s",
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
                vol.Optional(
                    CONF_RULE_WATCHDOG_TIMEOUT, **watchdog_kwargs
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0,
                        max=7200,
                        step=1,
                        unit_of_measurement="s",
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
            }
        )
