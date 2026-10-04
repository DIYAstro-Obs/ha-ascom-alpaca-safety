"""Config flow and Options flow for ASCOM Alpaca Safety."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import entity_registry as er, selector

from .const import (
    CONF_GROUPS,
    CONF_GROUP_LOGIC,
    CONF_GROUP_NAME,
    CONF_GROUP_SETTLE_TIME,
    CONF_RULE_ENTITY,
    CONF_RULE_OPERATOR,
    CONF_RULE_THRESHOLD,
    CONF_RULE_UNSAFE_DELAY,
    CONF_RULE_WATCHDOG_TIMEOUT,
    CONF_RULES,
    DEFAULT_SETTLE_TIME,
    DEFAULT_UNSAFE_DELAY,
    DEFAULT_WATCHDOG_TIMEOUT,
    DOMAIN,
    LOGIC_AND,
    LOGIC_OR,
    OPERATOR_LABELS,
    OPERATORS,
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
        self._current_rule_index: int | None = None

    def _load_groups(self) -> None:
        """Load current groups from options."""
        import copy
        self._groups = copy.deepcopy(
            self._config_entry.options.get(CONF_GROUPS, [])
        )

    def _rule_label(self, rule: dict[str, Any]) -> str:
        """Build a human-readable label for a rule menu entry."""
        entity_id: str = rule.get(CONF_RULE_ENTITY, "")
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

        return f"{friendly_name}  {op_str}  {threshold}"

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
            if not name:
                errors[CONF_GROUP_NAME] = "Name is required"
            elif any(
                g.get(CONF_GROUP_NAME, "").lower() == name.lower()
                for g in self._groups
            ):
                errors[CONF_GROUP_NAME] = "A group with this name already exists"
            else:
                new_group = {
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
                return await self.async_step_add_rule()
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
                    return await self.async_step_edit_rule()
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
        menu_options["back"] = "💾 Save & Back"

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

        if user_input is not None:
            group[CONF_GROUP_NAME] = user_input.get(
                CONF_GROUP_NAME, group[CONF_GROUP_NAME]
            )
            group[CONF_GROUP_LOGIC] = user_input.get(
                CONF_GROUP_LOGIC, group[CONF_GROUP_LOGIC]
            )
            group[CONF_GROUP_SETTLE_TIME] = int(
                user_input.get(CONF_GROUP_SETTLE_TIME, group[CONF_GROUP_SETTLE_TIME])
            )
            return await self.async_step_edit_group()

        return self.async_show_form(
            step_id="edit_group_settings",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_GROUP_NAME,
                        default=group.get(CONF_GROUP_NAME, ""),
                    ): selector.TextSelector(),
                    vol.Required(
                        CONF_GROUP_LOGIC,
                        default=group.get(CONF_GROUP_LOGIC, LOGIC_OR),
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
                        default=group.get(CONF_GROUP_SETTLE_TIME, DEFAULT_SETTLE_TIME),
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

    # ---- Add Rule ----

    async def async_step_add_rule(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Add a new rule to the current group."""
        idx = self._current_group_index
        if idx is None or idx >= len(self._groups):
            return await self.async_step_init()

        if user_input is not None:
            rule = {
                CONF_RULE_ENTITY: user_input.get(CONF_RULE_ENTITY, ""),
                CONF_RULE_OPERATOR: user_input.get(CONF_RULE_OPERATOR, ">"),
                CONF_RULE_THRESHOLD: str(user_input.get(CONF_RULE_THRESHOLD, "0")),
                CONF_RULE_UNSAFE_DELAY: int(
                    user_input.get(CONF_RULE_UNSAFE_DELAY, DEFAULT_UNSAFE_DELAY)
                ),
                CONF_RULE_WATCHDOG_TIMEOUT: int(
                    user_input.get(
                        CONF_RULE_WATCHDOG_TIMEOUT, DEFAULT_WATCHDOG_TIMEOUT
                    )
                ),
            }
            self._groups[idx].setdefault(CONF_RULES, []).append(rule)
            return await self.async_step_edit_group()

        return self.async_show_form(
            step_id="add_rule",
            data_schema=self._rule_schema(),
        )

    # ---- Edit Rule ----

    async def async_step_edit_rule(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Edit an existing rule."""
        g_idx = self._current_group_index
        r_idx = self._current_rule_index
        if (
            g_idx is None
            or g_idx >= len(self._groups)
            or r_idx is None
            or r_idx >= len(self._groups[g_idx].get(CONF_RULES, []))
        ):
            return await self.async_step_edit_group()

        rule = self._groups[g_idx][CONF_RULES][r_idx]

        if user_input is not None:
            rule[CONF_RULE_ENTITY] = user_input.get(
                CONF_RULE_ENTITY, rule[CONF_RULE_ENTITY]
            )
            rule[CONF_RULE_OPERATOR] = user_input.get(
                CONF_RULE_OPERATOR, rule[CONF_RULE_OPERATOR]
            )
            rule[CONF_RULE_THRESHOLD] = str(
                user_input.get(CONF_RULE_THRESHOLD, rule[CONF_RULE_THRESHOLD])
            )
            rule[CONF_RULE_UNSAFE_DELAY] = int(
                user_input.get(CONF_RULE_UNSAFE_DELAY, rule[CONF_RULE_UNSAFE_DELAY])
            )
            rule[CONF_RULE_WATCHDOG_TIMEOUT] = int(
                user_input.get(
                    CONF_RULE_WATCHDOG_TIMEOUT, rule[CONF_RULE_WATCHDOG_TIMEOUT]
                )
            )
            return await self.async_step_edit_group()

        return self.async_show_form(
            step_id="edit_rule",
            data_schema=self._rule_schema(defaults=rule),
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

    def _rule_schema(
        self, defaults: dict[str, Any] | None = None
    ) -> vol.Schema:
        """Build the schema for adding/editing a rule."""
        d = defaults or {}

        operator_options = [
            selector.SelectOptionDict(value=o, label=OPERATOR_LABELS[o])
            for o in OPERATORS
        ]

        return vol.Schema(
            {
                vol.Required(
                    CONF_RULE_ENTITY,
                    default=d.get(CONF_RULE_ENTITY, ""),
                ): selector.EntitySelector(),
                vol.Required(
                    CONF_RULE_OPERATOR,
                    default=d.get(CONF_RULE_OPERATOR, ">"),
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=operator_options,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(
                    CONF_RULE_THRESHOLD,
                    default=d.get(CONF_RULE_THRESHOLD, "0"),
                ): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
                ),
                vol.Optional(
                    CONF_RULE_UNSAFE_DELAY,
                    default=d.get(CONF_RULE_UNSAFE_DELAY, DEFAULT_UNSAFE_DELAY),
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
                    CONF_RULE_WATCHDOG_TIMEOUT,
                    default=d.get(
                        CONF_RULE_WATCHDOG_TIMEOUT, DEFAULT_WATCHDOG_TIMEOUT
                    ),
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
