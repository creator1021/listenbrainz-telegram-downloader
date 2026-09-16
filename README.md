# ListenBrainz Telegram Audio Downloader

A self-hosted Python service that watches a ListenBrainz user's recent listening
history, searches authorized Telegram channels in a configured priority order,
and downloads the first confident audio match into a music-library folder. It
can also import audio that you manually forward to Telegram Saved Messages.

The output is suitable for Navidrome and similar filesystem-based music servers.
The service writes or preserves title, artist, album, album artist, and cover
metadata where the audio container supports it.

> Use this project only with media that you are legally authorized to access and
> download. It does not join channels, bypass Telegram access controls, defeat
> DRM, or grant rights to copyrighted material.

## Features

- Telegram user-session authentication for channels already joined by the user
- Strict Channel A → B → C fallback order
- Public usernames and private numeric channel IDs
- ListenBrainz pagination, polling, retry scheduling, and configurable lookback
- Filename, caption, embedded-title, and embedded-artist matching
- Artist/title safety gates to reduce false-positive downloads
- SQLite persistence and duplicate prevention
- Atomic downloads through a hidden `.partial` directory
- Telegram Saved Messages inbox for manually forwarded audio
- ListenBrainz release-name preference with Telegram album preservation
- Existing artwork preservation and Telegram-thumbnail cover embedding
- MP3, M4A, FLAC, OGG, OPUS, WAV, and limited raw AAC handling
- Non-root, read-only production container with dropped Linux capabilities
- Persistent heartbeat-based Docker health check
- Multi-architecture Docker images for `linux/amd64` and `linux/arm64`
- GitHub Actions CI and tag-triggered Docker Hub publishing

## How it works

For ListenBrainz downloads:

1. Poll recent completed listens from ListenBrainz.
2. Register unseen listens in SQLite.
3. Search the first configured Telegram channel using `artist + title` and
   `title` queries.
4. Score audio candidates using the filename, caption, Telegram audio title,
   and performer.
5. If no acceptable candidate exists, continue to the next channel.
6. Download the best candidate from the first successful channel.
7. Write metadata, publish the completed file atomically, and remember it.

For manual downloads, forward an audio attachment to your own Telegram Saved
Messages. The next polling cycle downloads it into the same output folder.

## Requirements

- Docker Engine with Docker Compose v2
- A Telegram account that already belongs to the configured groups/channels
- Telegram API credentials from <https://my.telegram.org/apps>
- A ListenBrainz username
- A host directory that Navidrome can scan

The ListenBrainz token is optional for ordinary public listening history.

## Deployment using the published Docker image

This is the recommended production deployment after the maintainer has published
the image to Docker Hub.

### 1. Download the deployment files

Clone the repository and enter it:

```bash
git clone https://github.com/YOUR_GITHUB_USERNAME/listenbrainz-telegram-downloader.git
cd listenbrainz-telegram-downloader
cp .env.example .env
mkdir -p downloads
```

Make the host download directory writable by the image's UID `1000` when
required by your Linux host:

```bash
sudo chown -R 1000:1000 downloads
```

### 2. Configure the image and credentials

Edit `.env`:

```dotenv
DOCKER_IMAGE=YOUR_DOCKERHUB_USERNAME/listenbrainz-telegram-downloader
IMAGE_TAG=latest

TELEGRAM_API_ID=12345678
TELEGRAM_API_HASH=replace_with_your_api_hash
LISTENBRAINZ_USERNAME=your_listenbrainz_username
TELEGRAM_CHANNELS=-1001111111111,-1002222222222,@public_channel
```

For reproducible deployments, use a fixed version such as `IMAGE_TAG=0.0.1`
instead of `latest`.

### 3. Authenticate Telegram once

```bash
docker compose -f compose.production.yml run --rm song-fetcher auth
```

Enter the Telegram phone number, login code, and 2FA password when prompted.
Telethon stores the authenticated session under `/data` in the persistent
`telegram-data` volume. Treat this volume like a password.

### 4. Discover private channel IDs

```bash
docker compose -f compose.production.yml run --rm song-fetcher channels
```

Copy the exact negative IDs into `TELEGRAM_CHANNELS` in search-priority order.

### 5. Test one cycle

Set `DRY_RUN=true`, then run:

```bash
docker compose -f compose.production.yml run --rm song-fetcher once
```

Review the match scores and channel selection. Set `DRY_RUN=false` before
starting the permanent service.

### 6. Start the service

```bash
docker compose -f compose.production.yml up -d
docker compose -f compose.production.yml logs -f song-fetcher
```

Check container health:

```bash
docker compose -f compose.production.yml ps
docker compose -f compose.production.yml exec song-fetcher \
  python -m app.cli healthcheck
```

## Deployment by building from source

The default `docker-compose.yml` builds the checked-out source locally:

```bash
cp .env.example .env
mkdir -p downloads
docker compose build --pull
docker compose run --rm song-fetcher auth
docker compose run --rm song-fetcher channels
docker compose run --rm song-fetcher once
docker compose up -d
```

## Saved Messages manual-download workflow

Enable the importer in `.env`:

```dotenv
WATCH_SAVED_MESSAGES=true
SAVED_MESSAGES_INITIAL_LIMIT=20
SAVED_MESSAGES_POLL_LIMIT=200
```

Forward actual audio attachments from Telegram groups or channels to your own
**Saved Messages** chat. Forwarding from the mobile app is supported, provided
the app and Docker session use the same Telegram account.

Each polling cycle emits a diagnostic line:

```text
Saved Messages poll: cursor=12345 inspected=50 new=1
```

Possible outcomes are logged as imported, ignored, or failed. The first enabled
scan checks only `SAVED_MESSAGES_INITIAL_LIMIT` recent messages so it does not
unexpectedly import the account's complete history. Later cycles inspect the
latest `SAVED_MESSAGES_POLL_LIMIT` messages and select IDs newer than the stored
cursor.

Metadata priority for forwarded files is:

1. Existing embedded audio tags
2. Telegram audio title and performer
3. An `Artist - Title` filename

The existing embedded album and artwork are preserved. If artwork is missing
and Telegram supplies a document thumbnail, the service embeds that thumbnail
as the front cover. Because a manual forward has no ListenBrainz listen record,
ListenBrainz cannot provide a missing album for it.

## Navidrome integration

Bind `./downloads` to a directory inside Navidrome's music library, or configure
Navidrome to scan that host directory. The downloader performs an atomic rename
only after a file is complete, so Navidrome should not see partial audio files.

For ListenBrainz-triggered downloads, album priority is:

1. ListenBrainz `release_name`
2. Album already embedded in the Telegram file
3. `METADATA_FALLBACK_ALBUM`, only when explicitly configured

After downloads, run a Navidrome Quick Scan/Full Scan or wait for its scheduled
scan. Raw AAC/ADTS files cannot be tagged reliably; M4A, MP3, FLAC, OGG, or OPUS
is preferable when metadata matters.

## Configuration reference

| Variable | Default | Description |
| --- | --- | --- |
| `DOCKER_IMAGE` | none | Docker Hub image used by `compose.production.yml` |
| `IMAGE_TAG` | `latest` | Published image version used in production |
| `TELEGRAM_API_ID` | required | Numeric API ID from Telegram |
| `TELEGRAM_API_HASH` | required | Telegram API hash; keep secret |
| `TELEGRAM_PHONE` | blank | Optional phone passed to interactive authentication |
| `TELEGRAM_CHANNELS` | required | Ordered comma-separated channel IDs/usernames |
| `TELEGRAM_SESSION` | `/data/telegram` | Persistent Telethon session path |
| `LISTENBRAINZ_USERNAME` | required | ListenBrainz account to monitor |
| `LISTENBRAINZ_TOKEN` | blank | Optional ListenBrainz token |
| `LISTENBRAINZ_BASE_URL` | official API | ListenBrainz API base URL |
| `POLL_INTERVAL_SECONDS` | `300` | Delay between synchronization cycles |
| `INITIAL_LOOKBACK_HOURS` | `6` | Lookback used when no listen cursor exists |
| `LISTEN_FETCH_COUNT` | `100` | Listens requested per API page |
| `LISTEN_FETCH_MAX_PAGES` | `10` | Maximum pages fetched in one cycle |
| `SEARCH_RESULTS_PER_QUERY` | `50` | Telegram results examined per query |
| `MATCH_THRESHOLD` | `76` | Minimum combined match score |
| `RETRY_INTERVAL_MINUTES` | `60` | Delay before retrying an unmatched track |
| `MAX_RETRY_HOURS` | `168` | Maximum pending-track retry window |
| `MAX_TRACKS_PER_CYCLE` | `10` | Telegram searches allowed per cycle |
| `MAX_FILE_SIZE_MB` | `150` | Maximum accepted audio size |
| `ALLOWED_EXTENSIONS` | common audio types | Comma-separated extension allowlist |
| `WRITE_METADATA` | `true` | Write/preserve Navidrome-friendly tags |
| `METADATA_FALLBACK_ALBUM` | blank | Last-resort album when all other sources are blank |
| `WATCH_SAVED_MESSAGES` | `false` | Enable the manual-forward inbox |
| `SAVED_MESSAGES_INITIAL_LIMIT` | `20` | Messages inspected on first enable |
| `SAVED_MESSAGES_POLL_LIMIT` | `200` | Messages fetched during later polls |
| `HEALTHCHECK_MAX_AGE_SECONDS` | `0` | `0` derives the threshold from the poll interval |
| `DRY_RUN` | `false` | Search without downloading |
| `LOG_LEVEL` | `INFO` | Python logging level |
| `DATA_DIR` | `/data` | SQLite and Telegram-session directory |
| `DOWNLOAD_DIR` | `/downloads` | Completed-audio directory |

## Operational commands

For source deployments, omit `-f compose.production.yml` from these commands.

```bash
# Follow logs
docker compose -f compose.production.yml logs -f song-fetcher

# Run one cycle immediately (stop the service first to avoid session locking)
docker compose -f compose.production.yml stop song-fetcher
docker compose -f compose.production.yml run --rm song-fetcher once
docker compose -f compose.production.yml up -d

# Show pending/downloaded/expired counts
docker compose -f compose.production.yml run --rm song-fetcher status

# List joined Telegram channels
docker compose -f compose.production.yml run --rm song-fetcher channels

# Retag files recorded in SQLite
docker compose -f compose.production.yml run --rm song-fetcher retag

# Restore albums overwritten by the historical v1.2 fallback
docker compose -f compose.production.yml run --rm song-fetcher restore-albums

# Show the packaged version
docker compose -f compose.production.yml run --rm song-fetcher --version
```

## Persistent data and backups

- `/data` contains `state.sqlite3` and the authenticated Telegram session.
- `/downloads` contains completed audio and `.partial` temporary downloads.
- `docker compose down` preserves both the named data volume and bind-mounted
  downloads.
- Do not run `docker compose down -v` unless you intentionally want to erase the
  Telegram session and application state.

Back up both the `telegram-data` volume and host `downloads` directory. Protect
the backup because the Telegram session may allow account access.


## Troubleshooting

- **No Saved Messages poll logs:** verify `WATCH_SAVED_MESSAGES=true`,
  `DRY_RUN=false`, `LOG_LEVEL=INFO`, and that the container uses the current
  image tag.
- **Saved message ignored:** forward the actual audio attachment, verify its
  extension is allowed, and confirm it is below `MAX_FILE_SIZE_MB`.
- **Telegram session database locked:** stop the permanent service before
  running `auth`, `channels`, or `once`.
- **Private channel cannot be resolved:** run `channels` using the same Docker
  deployment and copy the exact negative ID.
- **`IndexError` from `get_input_entity`:** update to the latest image and
  verify the account is still joined to the channel.
- **No Telegram match:** raise `SEARCH_RESULTS_PER_QUERY` or carefully lower
  `MATCH_THRESHOLD`; values below `68` increase false positives.
- **Downloads permission denied:** make the host directory writable by UID
  `1000`.
- **Container unhealthy:** inspect logs, then run the `healthcheck` command. A
  stale heartbeat indicates that the synchronization loop stopped progressing.
- **Telethon says `Exporting auth for new borrowed sender`:** this is a normal
  informational message when media is stored in another Telegram data center.


## License

MIT. See [LICENSE](LICENSE).
