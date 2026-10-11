# MediaSeed

Work only in this app repository. Never modify production Seedbox/mediahub-platform.

Keep this runtime independent of MediaHub Core/Agent. Preserve fail-closed VPN and storage guarantees before enabling downloads. Do not migrate live data or credentials without explicit, tested migration and rollback.

Use synthetic transports in tests; no real torrents or VPN credentials. Preview Compose starts the app only. Health endpoints must not claim verified VPN health without fresh evidence.