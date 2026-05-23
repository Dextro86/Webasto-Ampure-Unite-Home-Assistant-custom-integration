# Installation

## Requirements

- Home Assistant
- Webasto Unite or Ampure Unite charger
- Charger reachable over the local network
- Modbus/TCP enabled on the charger
- TCP port `502` reachable from Home Assistant
- Recommended Modbus unit ID: `255`

Use a fixed IP address or DHCP reservation for the charger. Avoid running another active Modbus/TCP master at the same time.

## HACS Installation

1. Open `HACS`.
2. Open the top-right menu and choose `Custom repositories`.
3. Add this repository URL:
   `https://github.com/Dextro86/Webasto-Ampure-Unite-Home-Assistant-custom-integration`
4. Select category `Integration`.
5. Click `Add`.
6. Search for `Webasto/Ampure Unite`.
7. Open the integration and click `Download`.
8. Restart Home Assistant.
9. Go to `Settings` -> `Devices & Services`.
10. Click `Add Integration`.
11. Search for `Webasto/Ampure Unite`.
12. Complete the config flow.

## Manual Installation

Copy:

```text
custom_components/webasto_unite
```

to:

```text
config/custom_components/webasto_unite
```

Then restart Home Assistant and add the integration through `Settings` -> `Devices & Services`.

## First Setup

Recommended first setup:

1. Use `Integration Charging Control = Monitoring Only`.
2. Confirm that charger state, currents, power and energy values update correctly.
3. Confirm that `Connected` is on and `Client Error` stays empty.
4. Switch to `Integration Charging Control = Enabled` only after monitoring is stable.
5. Enable DLB and Solar only after selecting suitable live sensors.

## Charger Configuration

Ensure:

- Modbus/TCP is enabled in the charger web interface.
- The charger has a fixed IP address.
- The charger is reachable from Home Assistant.
- TCP port `502` is not blocked.
- No other system keeps an active Modbus connection open to the charger.

## Firewall And VLAN Notes

If Home Assistant and the charger are on different VLANs, allow local TCP communication:

- source: Home Assistant
- destination: Webasto/Ampure Unite charger
- port: TCP `502`

Multicast discovery is not required. The integration connects directly to the configured host/IP.
