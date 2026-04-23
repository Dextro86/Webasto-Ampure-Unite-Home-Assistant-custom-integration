# Publishing notes

This file is for the repository maintainer, not for Home Assistant end users.

## GitHub publication steps

1. Create a new GitHub repository.
2. Push this repository contents to that GitHub repository.
3. Replace the temporary metadata gaps:
   - set the real documentation URL in `custom_components/webasto_unite/manifest.json`
   - set the real issue tracker URL in `custom_components/webasto_unite/manifest.json`
4. Keep the README disclosure section intact so users can clearly see:
   - this was developed with AI assistance
   - it is not yet broadly validated on real hardware
5. Publish the repository first as an experimental custom integration.
6. Only after real-world charger validation, consider wider HACS-facing promotion.

## Recommended initial positioning

- experimental
- AI-assisted
- not yet broadly hardware-validated

## Metadata still to fill in

- real documentation URL in `custom_components/webasto_unite/manifest.json`
- real issue tracker URL in `custom_components/webasto_unite/manifest.json`
- repository topics and description on GitHub
- release tagging strategy
