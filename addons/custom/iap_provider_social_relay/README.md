# IAP Provider Social Relay

Concrete implementation of an IAP alternative provider that routes requests
through a self-hosted relay endpoint. Enables Odoo Social features to work
without connecting to the official Odoo IAP service.

## Features

- Custom relay endpoint configuration via system settings
- Compatible with all IAP-dependent social modules
- Self-hosted infrastructure for full data control

## Dependencies

- iap
- iap_alternative_provider
- base_setup
