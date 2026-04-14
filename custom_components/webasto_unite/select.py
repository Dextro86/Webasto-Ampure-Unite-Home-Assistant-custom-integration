from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .entity import WebastoUniteCoordinatorEntity
from .models import ChargeMode, PvControlStrategy, PvPhaseSwitchingMode

CHARGE_MODE_LABELS = {
    ChargeMode.OFF: "Off",
    ChargeMode.NORMAL: "Normal",
    ChargeMode.PV: "PV",
    ChargeMode.FIXED_CURRENT: "Fixed Current",
}
CHARGE_MODE_BY_LABEL = {label: mode for mode, label in CHARGE_MODE_LABELS.items()}


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        WebastoModeSelect(coordinator),
        WebastoPhaseSwitchSelect(coordinator),
    ])


class WebastoModeSelect(WebastoUniteCoordinatorEntity, SelectEntity):
    _attr_name = "Charge Mode"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_charge_mode"

    @property
    def options(self) -> list[str]:
        modes = [ChargeMode.OFF, ChargeMode.NORMAL, ChargeMode.FIXED_CURRENT]
        if self.coordinator.control_config.pv_control_strategy != PvControlStrategy.DISABLED:
            modes.insert(2, ChargeMode.PV)
        return [CHARGE_MODE_LABELS[mode] for mode in modes]

    @property
    def current_option(self) -> str | None:
        if self.coordinator.data is None:
            return CHARGE_MODE_LABELS[ChargeMode.NORMAL]
        current_mode = self.coordinator.data.mode
        if (
            current_mode == ChargeMode.PV
            and self.coordinator.control_config.pv_control_strategy == PvControlStrategy.DISABLED
        ):
            return CHARGE_MODE_LABELS[ChargeMode.NORMAL]
        return CHARGE_MODE_LABELS[current_mode]

    async def async_select_option(self, option: str) -> None:
        self.coordinator.set_mode(CHARGE_MODE_BY_LABEL[option])
        await self.coordinator.async_request_refresh()


PHASE_SWITCH_LABELS = {
    1: "1 Phase",
    3: "3 Phases",
}
PHASE_SWITCH_BY_LABEL = {label: phases for phases, label in PHASE_SWITCH_LABELS.items()}
PHASE_SWITCH_PHASES_BY_RAW = {
    0: 1,
    1: 3,
}


class WebastoPhaseSwitchSelect(WebastoUniteCoordinatorEntity, SelectEntity):
    _attr_name = "Phase Switch Mode"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_phase_switch"

    @property
    def options(self) -> list[str]:
        return list(PHASE_SWITCH_LABELS.values())

    @property
    def available(self) -> bool:
        return (
            super().available
            and self.coordinator.control_config.pv_phase_switching_mode != PvPhaseSwitchingMode.DISABLED
        )

    @property
    def current_option(self) -> str | None:
        data = self.coordinator.data
        if data is None or data.wallbox.phase_switch_mode_raw is None:
            return None
        phases = PHASE_SWITCH_PHASES_BY_RAW.get(data.wallbox.phase_switch_mode_raw)
        if phases is None:
            return None
        return PHASE_SWITCH_LABELS[phases]

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_set_phase_switch_mode(PHASE_SWITCH_BY_LABEL[option])
