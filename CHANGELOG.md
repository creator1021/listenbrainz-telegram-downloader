# Changelog

All notable changes are documented here. Releases use semantic versioning.

## 0.0.1 — First public release

- Added ListenBrainz recent-listen polling and strict Telegram channel fallback.
- Added matching, retries, SQLite deduplication, and atomic audio downloads.
- Added Telegram Saved Messages importing with metadata and cover handling.
- Added ListenBrainz release-name preference and Telegram album preservation.
- Added GitHub Actions CI for tests, linting, formatting, compilation, and image builds.
- Added tag-triggered, multi-architecture Docker Hub publishing.
- Added OCI metadata, SBOM, and build-provenance generation.
- Added a production Compose file with read-only filesystem, capability dropping,
  and `no-new-privileges`.
- Added a persistent heartbeat-based Docker health check.
- Added complete source-build, published-image, upgrade, rollback, Navidrome,
  Docker Hub, security, and contribution documentation.
