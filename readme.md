# faxomemo

**faxomemo prints news, weather, puzzles and messages on a Phomemo M220 thermal printer.**

faxomemo runs on a Linux computer. It sends printouts to the printer over USB or Bluetooth. You control faxomemo from a settings page in your web browser.

faxomemo is a fork of [PC-1 (paper-console)](https://github.com/travmiller/paper-console) by Travis Miller. PC-1 is a self-built printer appliance with a Raspberry Pi, a rotary dial and a button. faxomemo keeps the PC-1 modules and settings page. faxomemo removes the dial and adds a driver for the Phomemo M220.

**New to faxomemo?** Read the [Getting Started guide](docs/getting-started.md). The guide assumes no technical knowledge.

---

## Features

- **Phomemo M220 driver:** faxomemo prints over USB or Bluetooth. The `./faxomemo` script finds and pairs the printer.
- **Channels:** a channel is a list of modules that print together. You can add as many channels as you need.
- **Schedules:** each channel can print at set times. If the computer is off at a scheduled time, faxomemo prints the most recent missed schedule of the same day when it starts again.
- **Printing switch:** one switch in the settings page stops all automatic prints. Manual prints continue to work.
- **Label designer:** an optional browser label designer, based on [phomymo](https://github.com/transcriptionstream/phomymo).
- **Background service:** faxomemo can run as a systemd user service that starts with your computer.

## Modules

A module makes one printout. Add modules to channels in the settings page.

### News and reading

| Module | What it prints |
|---|---|
| Newsstand | Top articles from [calibre](https://calibre-ebook.com/) news recipes and feed URLs. Optional QR codes link to the full articles. |
| News API | Top headlines from [NewsAPI](https://newsapi.org/). Needs an API key. |
| RSS Feeds | Articles from RSS feed URLs that you choose |
| Daily Quote | The Wikiquote quote of the day. If faxomemo is offline, it uses a bundled quote list. |
| On This Day | Historical events from the current date |
| Word of the Day | Words of the day in 12 languages, including Japanese with furigana |

### Puzzles and games

| Module | What it prints |
|---|---|
| Daily Chess Puzzle | The Lichess daily puzzle as a chess diagram. The solution prints upside down. |
| Crossword | A generated crossword, or a downloaded daily mini crossword |
| Sudoku | A Sudoku puzzle, in medium or hard difficulty |
| Maze Generator | A 61x61 maze |
| Word Search | A word search puzzle |
| Adventure Game | A choose-your-own-adventure text game. The game uses the PC-1 dial and button for choices, and faxomemo has no dial. |

### Daily information

| Module | What it prints |
|---|---|
| Weather Forecast | Current conditions, a 24-hour forecast and a 7-day forecast |
| Astronomy | Sunrise, sunset, moon phase and the path of the sun |
| Calendar | Events from iCal calendar URLs |
| Berlin Transit | The next departures from the nearest BVG stops. This module works in Berlin only. |
| Journal Prompt | A random journal prompt |

### Messages and automation

| Module | What it prints |
|---|---|
| Slack | Direct messages, links and images that people send to your Slack bot |
| Beeper | Incoming messages from approved senders in [Beeper](https://www.beeper.com/). Beeper Desktop must run on the same computer. |
| Email Inbox | Unread emails from an IMAP inbox |
| Print Endpoint | Print jobs that automations send to a faxomemo URL |
| Webhook | Data from a URL or API |

### Tools

| Module | What it prints |
|---|---|
| Text / Note | A note that you write in a rich text editor |
| QR Code | A QR code for a URL, WiFi network, contact, phone number or text |
| Image | An image that you upload |
| System Monitor | The IP address, disk use, memory, uptime and CPU temperature of the computer |

---

## Quick start

You need a Linux computer and a Phomemo M220 printer. The [Getting Started guide](docs/getting-started.md) gives each step in detail.

1. Install the system packages. This command is for Ubuntu and Debian:

   ```bash
   sudo apt install -y git python3 python3-venv python3-dev build-essential pkg-config libxml2-dev bluez rsync
   ```

2. Download faxomemo:

   ```bash
   git clone https://github.com/zdavison/faxomemo.git
   cd faxomemo
   ```

3. Turn on the printer. Connect the printer with USB, or keep the printer near the computer for Bluetooth.
4. Start faxomemo:

   ```bash
   ./faxomemo
   ```

5. Open the settings page:

   ```bash
   ./faxomemo web
   ```

The `./faxomemo web` command shows the **Device Password**. The settings page asks for this password.

### The `faxomemo` script

On the first run, `./faxomemo` does these steps:

1. It creates the Python environment in `.venv` and installs the dependencies.
2. It looks for the printer. If `/dev/usb/lp0` exists, it uses USB. If not, it scans for the M220 over Bluetooth, then pairs and trusts the printer.
3. It saves the printer settings in `.faxomemo.env`. Later runs use these settings.
4. It starts the server on port 3290.

Useful options:

| Command | What it does |
|---|---|
| `./faxomemo web` | Shows the Device Password and opens the settings page |
| `./faxomemo --install-daemon` | Installs faxomemo as a background service that starts with your computer |
| `./faxomemo --uninstall-daemon` | Removes the background service |
| `./faxomemo --rediscover` | Scans for the Bluetooth printer again |
| `./faxomemo --transport usb` | Uses USB and skips the printer search. Use `bluetooth` for Bluetooth. |
| `./faxomemo --dry-run` | Shows the checks and the final command, but changes nothing |
| `./faxomemo --help` | Shows all options |

If you install the background service, run `./faxomemo --install-daemon` again after each update. The command copies the new files to `~/.local/share/faxomemo` and restarts the service.

---

## Configuration

Use the settings page for most settings. faxomemo saves the settings in `config.json`. This file is not in git.

### Settings page

- **GENERAL** tab: location, time format, printer settings and the Device Password.
- **CHANNELS** tab: channels, modules and schedules.
- **LABELS** tab: the label designer. This tab shows only after you install the label designer.

### Environment variables

The `faxomemo` script sets the printer variables for you. Set a variable yourself only if you need a value that is different from the default.

| Variable | Default | What it does |
|---|---|---|
| `PC1_PRINTER_DRIVER` | `auto` | `phomemo` uses the M220. `mock` prints to the terminal. `auto` uses the PC-1 serial printer on a Raspberry Pi and `mock` on other computers. |
| `PC1_PHOMEMO_TRANSPORT` | `usb` | `usb` or `bluetooth` |
| `PC1_PHOMEMO_USB_DEVICE` | `/dev/usb/lp0` | The USB device file of the printer |
| `PC1_PHOMEMO_BT_ADDRESS` | (empty) | The Bluetooth address of the printer |
| `PC1_PAPER_WIDTH_MM` | `54` | The printable width in millimetres. 57 mm and 58 mm paper has a 54 mm print area. |
| `PC1_PHOMEMO_MEDIA_TYPE` | `11` | `11` is continuous paper. `10` is labels with gaps. `38` is paper with black marks. |
| `PC1_SKIP_READY_PRINT` | `0` | Set to `1` to skip the SYSTEM READY receipt at startup |
| `PC1_DEVICE_PASSWORD` | (generated) | Sets the Device Password |
| `PC1_CORS_ORIGINS` | local origins | A comma-separated list of allowed CORS origins |
| `PC1_LOG_LEVEL` | `WARNING` | The log level of the backend |
| `FAXOMEMO_PORT` | `3290` | The port that `./faxomemo` uses. `./run.sh` uses `PORT`. |

### Label designer (optional)

The label designer is [phomymo](https://github.com/transcriptionstream/phomymo). faxomemo does not include phomymo. To install phomymo, run:

```bash
./scripts/fetch_label_designer.sh
```

The script downloads phomymo into `vendor/phomymo`. Restart faxomemo. Then open the **LABELS** tab.

How to print a label:

1. Log in to the settings page. The label designer cannot print if you are not logged in.
2. Design the label in the **LABELS** tab.
3. Click **Print on faxomemo**.

Do not use the phomymo **Print** button. That button connects to the printer directly. The M220 accepts only one connection, and faxomemo already holds that connection.

faxomemo prints the label at the size that you designed. If the label is wider than the paper, faxomemo reduces the label to the paper width. For gapless tape, tick **Continuous** next to the print button.

The label designer needs internet access, because phomymo loads its libraries from public CDNs.

### Newsstand recipes

The Newsstand module reads [calibre news recipes](https://github.com/kovidgoyal/calibre/tree/master/recipes). faxomemo does not need calibre. faxomemo downloads each recipe from the calibre repository and runs the recipe with a small copy of the calibre recipe API.

Some recipes need a headless browser. If a recipe needs a browser, click **Install browser** in the Newsstand module settings.

---

## Development

### Set up

```bash
python3 -m venv .venv
./.venv/bin/python -m pip install -r requirements-dev.txt
cd web && npm install
```

### Run without a printer

```bash
./run.sh
```

On a computer that is not a Raspberry Pi, `./run.sh` uses the mock printer. The mock printer writes each printout to the terminal. The API documentation is at <http://localhost:3290/docs>.

### Run the settings page in development mode

```bash
cd web
npm run dev
```

Open <http://localhost:5173>. The repository includes a built copy of the settings page in `web/dist`. After you change the settings page, run `npm run build` in the `web` folder.

### Tests and previews

| Command | What it does |
|---|---|
| `./testing/run_tests.sh` | Runs the tests |
| `./.venv/bin/python testing/render_all_prints.py` | Renders each printout to PNG files |
| `./.venv/bin/python testing/render_settings_ui.py` | Takes screenshots of the settings page |
| `./.venv/bin/python testing/console_raster_preview.py --image <png>` | Shows a printout as dots in the terminal |

[AGENTS.md](AGENTS.md) gives more detail about the print previews.

### Directory structure

```
faxomemo/
├── faxomemo                # Setup and start script
├── run.sh                  # Starts the server without printer detection
├── app/
│   ├── main.py             # FastAPI app, schedules and print control
│   ├── config.py           # Settings, channels and module models
│   ├── drivers/            # Printer drivers: Phomemo, PC-1 serial and mock
│   ├── modules/            # One file for each module
│   ├── static/             # Files for the label designer bridge
│   └── data/               # Offline content and lookup data
├── web/                    # Settings page (React, Vite, Tailwind CSS)
├── scripts/                # Label designer download, Raspberry Pi and release scripts
├── testing/                # Tests and print preview tools
└── docs/                   # Getting Started guide
```

---

## Troubleshooting

| Problem | What to do |
|---|---|
| `Permission denied` for `/dev/usb/lp0` | Run `sudo usermod -a -G lp "$USER"`. Then log out and log in again. |
| faxomemo does not find the Bluetooth printer | Turn the printer off and on. Then run `./faxomemo --rediscover`. |
| The printer shows a serial number, not a name, in Bluetooth | Run `bluetoothctl devices`. Look for a device that you do not know. |
| A scheduled print did not print | Make sure that the printing switch in the settings page is on. |
| The label designer says `Open faxomemo settings and log in first` | Log in to the settings page. Then print the label again. |
| The Email Inbox module cannot log in | Use an app password from your email provider, not your normal password. |
| The weather is for the wrong place | Set your location in the **GENERAL** tab. |

If the problem continues, [open an issue](https://github.com/zdavison/faxomemo/issues).

---

## Thanks

- **[Travis Miller (travmiller)](https://github.com/travmiller)** made [PC-1 (paper-console)](https://github.com/travmiller/paper-console). faxomemo starts from PC-1. The design, the module system and most of the code come from PC-1. Thank you, Travis, and thank you to all PC-1 contributors.
- **[transcriptionstream](https://github.com/transcriptionstream)** made [phomymo](https://github.com/transcriptionstream/phomymo), the browser label designer for Phomemo printers. faxomemo embeds phomymo as the optional label designer.

## License

faxomemo is free software under the [MIT License](LICENSE).

faxomemo uses third-party libraries, fonts, icons and data under their own licenses. Some Python dependencies use the GPL. Read [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for the full list.
