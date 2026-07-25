# Home OS

Home OS is organized as a small monorepo with each product in its own directory.

## Projects

| Path | Purpose |
| --- | --- |
| `server/` | Python Flask server, tests, deployment scripts, and local server data |
| `apps/ios-music/` | HomeMusic iOS app and its build helper |
| `apps/macos-files/` | HomeOS Files macOS app, File Provider extension, and tests |
| `docs/` | Shared development and design notes |
| `archive/` | Preserved legacy checkout; not part of the active workspace |

## Common Commands

Run server tests:

```bash
cd server
venv/bin/python -m pytest
```

Build the iOS music app for the simulator:

```bash
./apps/ios-music/scripts/build.sh simulator
```

Build the HomeOS macOS app without signing:

```bash
./apps/macos-files/scripts/build_and_run.sh --no-sign
```

The macOS project currently also contains the experimental `HomeOS-Music`
target. Its helper is `apps/macos-files/scripts/build_and_run_music.sh`.
