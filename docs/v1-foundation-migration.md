# V1 foundation migration

## Status schema

The temporary climate compatibility aliases have been removed. Consumers must use `climate.recirculation_active` and `climate.recirculation_raw`.

## Dashboard backend imports

Media implementation details are no longer re-exported from `ui.web_dashboard.server`. Import provider functions from their owning modules:

- `ui.web_dashboard.radio`
- `ui.web_dashboard.usb`
- `ui.web_dashboard.jellyfin`
- `ui.web_dashboard.bluetooth`

Only HTTP routing and server lifecycle belong in `server.py`.

## Frontend assets

The dashboard now loads explicit JavaScript and CSS modules in the order recorded by `index.html`. Integrations should use the documented `window.openMmi*` module interfaces rather than copying internal functions from `app.js`.

## Installation

Rebuild and reinstall the wheel after upgrading. Existing source checkouts should recreate their virtual environment when dependency resolution differs from the previous unbounded requirements.
