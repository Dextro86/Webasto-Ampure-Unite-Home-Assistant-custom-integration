import sys
import types
from pathlib import Path
from dataclasses import dataclass


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# Minimal stubs so pure-Python unit tests can import the integration package
# without a full Home Assistant runtime.
vol = types.ModuleType("voluptuous")
class _Invalid(Exception):
    pass
class _Schema:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
    def __call__(self, value):
        return value
vol.Schema = _Schema
vol.Required = lambda value: value
vol.Optional = lambda value, default=None: value
vol.In = lambda value: value
vol.Coerce = lambda t: t
vol.Invalid = _Invalid
sys.modules.setdefault("voluptuous", vol)

ha = types.ModuleType("homeassistant")
sys.modules.setdefault("homeassistant", ha)

config_entries = types.ModuleType("homeassistant.config_entries")
config_entries.ConfigEntry = object
class _ConfigEntryNotReady(Exception):
    pass
class _ConfigFlow:
    def __init_subclass__(cls, **kwargs):
        return super().__init_subclass__()
    async def async_set_unique_id(self, unique_id):
        self.unique_id = unique_id
    def _abort_if_unique_id_configured(self):
        return None
    def async_create_entry(self, title="", data=None):
        return {"type": "create_entry", "title": title, "data": data}
    def async_show_form(self, step_id, data_schema=None, errors=None):
        return {"type": "form", "step_id": step_id, "data_schema": data_schema, "errors": errors or {}}
class _OptionsFlow:
    def async_create_entry(self, title="", data=None):
        return {"type": "create_entry", "title": title, "data": data}
    def async_show_form(self, step_id, data_schema=None, errors=None):
        return {"type": "form", "step_id": step_id, "data_schema": data_schema, "errors": errors or {}}
config_entries.ConfigFlow = _ConfigFlow
config_entries.OptionsFlow = _OptionsFlow
config_entries.ConfigEntryNotReady = _ConfigEntryNotReady
sys.modules.setdefault("homeassistant.config_entries", config_entries)

const = types.ModuleType("homeassistant.const")
const.CONF_HOST = "host"
const.CONF_PORT = "port"
class _EntityCategory:
    DIAGNOSTIC = "diagnostic"
class _UnitOfElectricCurrent:
    AMPERE = "A"
class _UnitOfEnergy:
    KILO_WATT_HOUR = "kWh"
class _UnitOfPower:
    WATT = "W"
class _UnitOfTime:
    SECONDS = "s"
class _UnitOfElectricPotential:
    VOLT = "V"
const.EntityCategory = _EntityCategory
const.UnitOfElectricCurrent = _UnitOfElectricCurrent
const.UnitOfEnergy = _UnitOfEnergy
const.UnitOfPower = _UnitOfPower
const.UnitOfTime = _UnitOfTime
const.UnitOfElectricPotential = _UnitOfElectricPotential
sys.modules.setdefault("homeassistant.const", const)

core = types.ModuleType("homeassistant.core")
core.HomeAssistant = object
core.ServiceCall = object
core.callback = lambda fn: fn
sys.modules.setdefault("homeassistant.core", core)

data_entry_flow = types.ModuleType("homeassistant.data_entry_flow")
class _Section:
    def __init__(self, schema, options=None):
        self.schema = schema
        self.options = options or {}
def _section(schema, options=None):
    return _Section(schema, options)
data_entry_flow.section = _section
sys.modules.setdefault("homeassistant.data_entry_flow", data_entry_flow)

helpers = types.ModuleType("homeassistant.helpers")
sys.modules.setdefault("homeassistant.helpers", helpers)

cv = types.ModuleType("homeassistant.helpers.config_validation")
cv.string = str
sys.modules.setdefault("homeassistant.helpers.config_validation", cv)
helpers.config_validation = cv

selector = types.ModuleType("homeassistant.helpers.selector")
class _SelectSelectorConfig:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
class _SelectSelector:
    def __init__(self, config):
        self.config = config
class _EntitySelectorConfig:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
class _EntitySelector:
    def __init__(self, config):
        self.config = config
class _NumberSelectorConfig:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
class _NumberSelector:
    def __init__(self, config):
        self.config = config
class _NumberSelectorMode:
    BOX = "box"
selector.SelectSelectorConfig = _SelectSelectorConfig
selector.SelectSelector = _SelectSelector
selector.EntitySelectorConfig = _EntitySelectorConfig
selector.EntitySelector = _EntitySelector
selector.NumberSelectorConfig = _NumberSelectorConfig
selector.NumberSelector = _NumberSelector
selector.NumberSelectorMode = _NumberSelectorMode
sys.modules.setdefault("homeassistant.helpers.selector", selector)
helpers.selector = selector

event = types.ModuleType("homeassistant.helpers.event")
event.async_track_state_change_event = lambda *args, **kwargs: (lambda: None)
sys.modules.setdefault("homeassistant.helpers.event", event)

storage = types.ModuleType("homeassistant.helpers.storage")
class _Store:
    def __init__(self, hass, version, key):
        self.hass = hass
        self.version = version
        self.key = key
    async def async_load(self):
        return getattr(self.hass, "_storage_data", {}).get(self.key)
    async def async_save(self, data):
        if not hasattr(self.hass, "_storage_data"):
            self.hass._storage_data = {}
        self.hass._storage_data[self.key] = data
storage.Store = _Store
sys.modules.setdefault("homeassistant.helpers.storage", storage)
helpers.storage = storage

update_coordinator = types.ModuleType("homeassistant.helpers.update_coordinator")
class _DataUpdateCoordinator:
    def __class_getitem__(cls, item):
        return cls
    def __init__(self, *args, **kwargs):
        self.data = None
    async def async_request_refresh(self):
        return None
    def async_set_updated_data(self, data):
        self.data = data
class _UpdateFailed(Exception):
    pass
class _CoordinatorEntity:
    def __init__(self, coordinator):
        self.coordinator = coordinator
update_coordinator.DataUpdateCoordinator = _DataUpdateCoordinator
update_coordinator.UpdateFailed = _UpdateFailed
update_coordinator.CoordinatorEntity = _CoordinatorEntity
sys.modules.setdefault("homeassistant.helpers.update_coordinator", update_coordinator)

switch = types.ModuleType("homeassistant.components.switch")
class _SwitchEntity:
    pass
switch.SwitchEntity = _SwitchEntity
sys.modules.setdefault("homeassistant.components.switch", switch)

sensor = types.ModuleType("homeassistant.components.sensor")
class _SensorEntity:
    pass
@dataclass(frozen=True)
class _SensorEntityDescription:
    key: str | None = None
    name: str | None = None
    entity_category: str | None = None
    native_unit_of_measurement: str | None = None
sensor.SensorEntity = _SensorEntity
sensor.SensorEntityDescription = _SensorEntityDescription
sys.modules.setdefault("homeassistant.components.sensor", sensor)

entity_platform = types.ModuleType("homeassistant.helpers.entity_platform")
entity_platform.AddEntitiesCallback = object
sys.modules.setdefault("homeassistant.helpers.entity_platform", entity_platform)

device_registry = types.ModuleType("homeassistant.helpers.device_registry")
class _DeviceInfo(dict):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
device_registry.DeviceInfo = _DeviceInfo
sys.modules.setdefault("homeassistant.helpers.device_registry", device_registry)

pymodbus = types.ModuleType("pymodbus")
sys.modules.setdefault("pymodbus", pymodbus)

pymodbus_client = types.ModuleType("pymodbus.client")
class _AsyncModbusTcpClient:
    def __init__(self, *args, **kwargs):
        self.connected = False
    async def connect(self):
        self.connected = True
        return True
    def close(self):
        self.connected = False
pymodbus_client.AsyncModbusTcpClient = _AsyncModbusTcpClient
sys.modules.setdefault("pymodbus.client", pymodbus_client)

pymodbus_exceptions = types.ModuleType("pymodbus.exceptions")
class _ModbusException(Exception):
    pass
pymodbus_exceptions.ModbusException = _ModbusException
sys.modules.setdefault("pymodbus.exceptions", pymodbus_exceptions)

pymodbus_pdu = types.ModuleType("pymodbus.pdu")
class _ExceptionResponse:
    def isError(self):
        return True
pymodbus_pdu.ExceptionResponse = _ExceptionResponse
sys.modules.setdefault("pymodbus.pdu", pymodbus_pdu)
