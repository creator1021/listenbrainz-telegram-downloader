# Security policy

## Reporting a vulnerability

Please do not open a public issue for vulnerabilities involving Telegram session
access, credential exposure, arbitrary file writes, or container escape. Use the
repository's private GitHub security-advisory reporting feature instead.

Include the affected version, impact, reproduction steps, and a proposed fix if
available. Never attach a real `.env`, Telegram `.session` file, API hash, phone
number, login code, 2FA password, ListenBrainz token, or private channel ID.

## Operational security

The `/data` volume contains the authenticated Telegram session and must be
treated like a credential. Restrict host access, do not publish backups, and do
not mount it into unrelated containers. Use a Docker Hub access token rather
than an account password for GitHub Actions.
