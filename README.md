# SC Cockpit

SC Cockpit is a Windows tray app that displays live Star Citizen telemetry in your browser on the same PC. Use it to monitor player vitals on foot or in EVA, check flight instruments from a ship, and record a session for later playback.

**[Download SC Cockpit v1.0 for Windows x64](https://github.com/lordtempis/sc-cockpit/releases/download/v1.0/SC-Cockpit-1.0-Windows-x64.zip)** · [Release notes](https://github.com/lordtempis/sc-cockpit/releases/tag/v1.0)

## First launch

1. On Windows 10 or 11 (x64), extract the ZIP to a folder you plan to keep. It contains `SC-Cockpit-Windows-x64.exe` and `READ ME FIRST.txt`. Python is bundled.
2. Double-click **SC-Cockpit-Windows-x64.exe**. The **Overview** page opens in your default browser. Look for the navy-and-teal cockpit icon near the Windows clock, under the overflow arrow if necessary.
3. To try it with the game closed, right-click the tray icon and choose **Use demo telemetry**. The dashboard shows **DEMO · SIMULATED**. Choose **Use live telemetry** in the same menu when you’re ready to connect.

## Connect to Star Citizen LIVE

1. Close Star Citizen and fully quit the RSI Launcher, including its tray icon.
2. Open PowerShell and run this one-time command for your Windows account.

   ```powershell
   setx GRPC_CLIENT_SERVER_ENABLED true
   ```

3. Reopen the RSI Launcher, start Star Citizen LIVE, and enter the game. Open the in-game console (the key below Esc on a US keyboard) and enter this command.

   ```text
   server.game.telemetry.enabled 1
   ```

4. In SC Cockpit, click **LIVE SOURCE** beside the Light/Dark switch. **Telemetry diagnostics** should show **Receiving telemetry** and a recent packet. Check Health and Oxygen on foot, then take the pilot seat to see which flight instruments your ship reports.

**Custom game location.** SC Cockpit checks the running game and the RSI Launcher’s recent install location. If detection misses your installation, right-click the tray icon, choose **Locate game**, and select `LIVE\Bin64\StarCitizen.exe` inside your Star Citizen folder. The app remembers your selection.

## Use the dashboard

| Tab | What you can see |
| --- | --- |
| **Overview** | Health and oxygen gauges, other player vitals, and available flight instruments. Player stays visible on foot and in EVA. |
| **Player** | Suit and environment readings, plus configurable audio warnings for health, oxygen, nutrition and hydration. |
| **Flight Deck** | Speed history over 30 or 60 seconds and recent session events. |
| **Ship Systems** | Reported power, warnings, weapons, ship configuration and flight-assist states. |
| **Event Log** | Timestamped changes with a warnings filter. The kill monitor shows a reported death or downing and any available weapon or damage cause. |

The Light/Dark switch applies across tabs and remembers your choice. Closing the browser leaves the reader running. Double-click the tray icon to reopen the dashboard. To run quietly at sign-in, enable **Start with Windows** in the tray menu and keep the EXE in its chosen folder. **Quit SC Cockpit** finishes any active recording and exits the app.

## Understand a missing reading

Click the feed badge beside the theme switch to check the source, connection, last packet age and individual fields. **Waiting for first sample** means the reader has yet to receive a frame. After five seconds without an update, a retained section is labeled **Last known**. A disconnect clears the live instrument panels.

The dashboard hides fields until the reader receives a usable value. A previously seen field can show a dash or a hollow gray light when its current value is unavailable. Explicit zero and false values count as readings. For status lights, green means the labeled condition is true and red means false. Under **System Indicators**, green means no warning and red means a warning. Hover over a light for its exact state.

Field delivery varies with the game build, ship and seat. Hydrogen fill is shown as a percentage when reported. The scale of the quantum fuel reading is unverified, and a ship can omit it entirely. If a door, transformation or fuel reading is missing, **Decoder field delivery** in Diagnostics shows which fields the reader has actually observed.

## Record and replay a session

1. Click the feed badge and choose **Start Recording**. A recording indicator appears in the header.
2. To mark a moment, enter a bookmark label and choose **Add Bookmark**. Choose **Stop Recording** to save the session. Windows recordings are stored in `%LOCALAPPDATA%\SC-Cockpit\recordings`.
3. Under **Saved recording**, select the session and choose **Open replay**. It opens paused and works with the game closed. Play, seek or jump to a bookmark to inspect readings and feed interruptions across the dashboard tabs.
4. Choose **Return to Live** or **Return to Demo** to leave replay.
