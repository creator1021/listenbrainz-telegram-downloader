# Contributing

Thank you for improving the project.

## Development setup

Use Python 3.12:

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt -r requirements-dev.txt
```

Run the same checks used by CI:

```bash
python -m unittest discover -s tests -v
ruff check .
ruff format --check .
python -m compileall -q app
docker build --build-arg VERSION=development -t lb-telegram-downloader:dev .
```

## Pull requests

- Keep changes focused and include tests for new behavior.
- Do not commit `.env`, Telegram session files, SQLite state, downloaded media,
  credentials, channel IDs, or personal logs.
- Preserve strict channel-priority behavior unless a change explicitly documents
  a different policy.
- Use the project only with media that you are authorized to access and download.

By contributing, you agree that your contribution is licensed under the MIT
License included in this repository.
