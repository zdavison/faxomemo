# Getting Started with faxomemo

This guide shows you how to set up faxomemo from the start. You do not need technical knowledge. Do the steps in order. Do not skip a step.

## What faxomemo does

faxomemo is a program that runs on your computer. It sends news, weather, puzzles, messages and other content to a small Phomemo M220 thermal printer. You control faxomemo from a settings page in your web browser.

## What you need

- A computer with Linux. This guide uses Ubuntu or Debian. Raspberry Pi OS also works, because it is a type of Debian.
- A Phomemo M220 printer, with paper in the printer.
- A USB cable for the printer, or Bluetooth on your computer.
- An internet connection.

faxomemo does not work on Windows or macOS.

## Words in this guide

- **Terminal:** a window where you type commands. A command is a line of text that tells the computer what to do.
- **Run a command:** type the command in the terminal, then push the **Enter** key.
- **Folder:** a place on your computer that holds files.

## Step 1: Open a terminal

1. Push the **Ctrl**, **Alt** and **T** keys at the same time.
2. If no window opens, open the application menu. Search for "Terminal", then click it.

A window opens with a blinking cursor. Type all commands in this guide in this window.

> **Tip:** You can copy a command from this guide. To paste the command in the terminal, push **Ctrl**, **Shift** and **V** at the same time.

## Step 2: Install the tools that faxomemo needs

1. Run this command:

   ```bash
   sudo apt update
   ```

2. If the terminal asks for a password, type the password that you use to log in to your computer. Then push **Enter**. The terminal does not show the letters when you type. This is normal.
3. Run this command:

   ```bash
   sudo apt install -y git python3 python3-venv python3-dev build-essential pkg-config libxml2-dev bluez rsync
   ```

4. Wait until the terminal shows the blinking cursor again. This can take some minutes.

## Step 3: Download faxomemo

1. Run this command:

   ```bash
   git clone https://github.com/zdavison/faxomemo.git
   ```

2. Run this command to go into the new `faxomemo` folder:

   ```bash
   cd faxomemo
   ```

All the commands in the next steps must run from the `faxomemo` folder. If you close the terminal, open a new terminal and run `cd faxomemo` again.

## Step 4: Connect the printer

Choose one method: USB or Bluetooth. USB is easier.

### Method A: USB

1. Turn on the printer.
2. Connect the printer to your computer with the USB cable.
3. Run this command to give your user account permission to use the printer:

   ```bash
   sudo usermod -a -G lp "$USER"
   ```

4. Log out of your computer, then log in again. The permission does not start until you log in again.
5. Open a terminal again. Then run `cd faxomemo`.

### Method B: Bluetooth

1. Turn on the printer.
2. Keep the printer near your computer.
3. Make sure that Bluetooth is on in the settings of your computer.

Do not connect the printer from the Bluetooth settings of your computer. faxomemo finds and connects the printer in the next step.

## Step 5: Start faxomemo

1. Run this command:

   ```bash
   ./faxomemo
   ```

2. Wait. The first start can take some minutes, because faxomemo downloads the parts that it needs.
3. If faxomemo finds more than one Bluetooth printer, it shows a numbered list. Type the number of your printer, then push **Enter**.
4. When faxomemo is ready, the printer prints a receipt.

Keep this terminal open. If you close this terminal, faxomemo stops. Step 8 shows you how to keep faxomemo on when the terminal is closed.

## Step 6: Open the settings page

1. Open a second terminal. Push **Ctrl**, **Alt** and **T** again.
2. Run this command:

   ```bash
   cd faxomemo
   ```

3. Run this command:

   ```bash
   ./faxomemo web
   ```

4. Find the line that starts with `Device Password:` in the terminal. Write down this password.
5. Your web browser opens the settings page. If the browser does not open, type `http://localhost:3290` in the address bar of your browser.
6. Type the Device Password in the **Unlock Settings** box. Then push **Enter**.

## Step 7: Set up your first print

1. Click the **GENERAL** tab.
2. Find **Set Location**. Type the name of your city, then select your city from the list. faxomemo uses your location for the weather and the time.
3. Click the **CHANNELS** tab.
4. Find channel 1. Click **+ ADD MODULE**, then select a module, for example **Weather**.
5. Click the print icon next to channel 1.

The printer prints the weather. Each channel can hold more than one module. Click the clock icon next to a channel to print the channel at a set time each day.

## Step 8: Keep faxomemo on all the time (optional)

If you do this step, faxomemo starts when your computer starts. You do not need to keep a terminal open.

1. Go to the first terminal.
2. Push **Ctrl** and **C** at the same time. faxomemo stops.
3. Run this command:

   ```bash
   ./faxomemo --install-daemon
   ```

4. To open the settings page at a later time, open a terminal and run `cd faxomemo`. Then run `./faxomemo web`.

To remove the background service, run `./faxomemo --uninstall-daemon`.

## Get a new version of faxomemo

1. Open a terminal.
2. Run `cd faxomemo`.
3. Run this command:

   ```bash
   git pull
   ```

4. If you did Step 8, run `./faxomemo --install-daemon` again. If you did not do Step 8, start faxomemo again with `./faxomemo`.

## If you have a problem

| Problem | What to do |
|---|---|
| The terminal says `Permission denied` for `/dev/usb/lp0`. | Do Method A of Step 4 again. Make sure that you log out and log in again. |
| faxomemo does not find the Bluetooth printer. | Turn the printer off and on. Then run `./faxomemo --rediscover`. |
| The printer is in a Bluetooth list but has a number, not a name. | Some printers show a serial number. Select the device that is near you. |
| The installation in Step 5 stops with an error. | Do Step 2 again. Then run `./faxomemo` again. |
| The settings page does not open. | Make sure that faxomemo runs in the first terminal. Then type `http://localhost:3290` in your browser. |
| The settings page asks for a password that you do not know. | Run `./faxomemo web`. Use the password on the `Device Password:` line. |

If the problem continues, open an issue at <https://github.com/zdavison/faxomemo/issues>. Copy the text from the terminal into the issue.

## Next steps

- Read [readme.md](../readme.md) for a list of all modules and settings.
- Read the **Label Designer** part of [readme.md](../readme.md) to design and print labels.
