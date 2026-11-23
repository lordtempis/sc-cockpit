# SC Cockpit

Standalone, local Star Citizen instruments and configurable audio warnings. The dashboard reads telemetry locally. The optional door diagnostic saves a capture on your PC.

## Windows test build

Share **SC-Cockpit-Windows-x64.exe** with Windows 10/11 x64 testers. Double-click it to open Overview and start the Windows tray app. Closing the browser leaves the reader running. Double-click the navy-and-teal cockpit icon or launch the EXE again to reopen the dashboard. On first launch it unpacks its bundled runtime into the user's local app data and opens Overview in the default browser. Python and the telemetry libraries are included.

The launcher checks for a running Star Citizen client, a previously selected executable, the RSI Launcher's recent LIVE launch location, and the standard installation folder. If it cannot find the game, right-click the tray icon and choose **Locate game** to select `Bin64\StarCitizen.exe`, or choose **Use demo telemetry**. Live readings require local telemetry to be enabled in Star Citizen. Recordings stay under `%LOCALAPPDATA%\SC-Cockpit\recordings`.

## Live readings from the source checkout

Double-click **Start-Live.cmd** in the project folder or an extracted copy of the ZIP. The launcher keeps its Python environment on the Windows drive under `%LOCALAPPDATA%\SC-Cockpit\venv`, including when launched from File Explorer through the WSL share. On first launch it sets up Python dependencies (Python 3.12 or 3.11 and internet access required). It reads the telemetry schema from the installed LIVE executable and opens **http://127.0.0.1:8744**. Spawn into the game and look for **LIVE** and your ship or vitals. Keep the window open while using the cockpit. If setup fails, the launcher displays the error and saves the most recent run to `%LOCALAPPDATA%\SC-Cockpit\startup.log`.

**Start-Demo.cmd** opens the simulated dashboard on port 8743. Select Approach, Low fuel, On foot, EVA, Partial feed, Waiting for data, Stale feed or Disconnected in the header’s feed dialog. Pause freezes a running demo. Live mode exposes no scenario controls.

The packaged tray menu shows packet status, including stale telemetry and recording activity. **Start with Windows** is optional and launches the tray app silently at sign-in. Keep the EXE in its chosen location when this is enabled. **Quit SC Cockpit** finishes an active recording and exits. The tray rechecks a missing installation and reloads the reader when the game executable is replaced. Restarting or switching readers also finishes the current recording.

### Diagnostics, recording and replay

Click the feed badge in the header to open **Telemetry diagnostics**. Mapped fields show normalized values, units and time since each usable reading. A missing update leaves the previous value labeled stale. Five seconds of silence also makes a reading stale. **Decoder field delivery** shows which raw inputs the adapter consumes, including received unmapped fields. “Unseen on wire” applies to a known schema field during this reader session. “Unknown” means the reader lacks delivery evidence. An omitted protobuf scalar can correspond to a default the sender did not encode.

Choose **Start Recording**, optionally enter a label and choose **Add Bookmark**, then choose **Stop Recording**. The header indicates when recording is active. Completed sessions appear under **Saved recording** in both the live reader and the demo reader. Files are stored in `%LOCALAPPDATA%\SC-Cockpit\recordings` on Windows and `~/.local/share/SC-Cockpit/recordings` elsewhere.

Select a session and choose **Open replay**. Replay opens paused at the beginning. Use Play, the position slider or the bookmark menu while browsing any dashboard tab. **Return to Live** (or **Return to Demo**) restores the reader. Playback works with the game closed. Each browser controls its own playhead. The recording includes timestamped dashboard snapshots, decoded field evidence and feed changes. The older door JSONL and delivery-count reports contain only a subset of these data.

### Check field delivery or capture a door toggle

Sit in the pilot seat with the ship doors closed. Double-click **Capture-Doors.cmd** in the source checkout. It uses the Windows environment prepared by **Start-Live.cmd**. When the console says **Recording vehicle frames**, open the doors, leave them open for five seconds, close them, and repeat once. Watch for the doors to physically move. For a separate transformation test on a ship with a known transformation control, repeat the capture and cycle that control instead.

The diagnostic subscribes to LIVE for one minute and writes a timestamped JSONL file in `%LOCALAPPDATA%\SC-Cockpit\captures`. Each vehicle frame has a row, including frames that omit both fields. The console counts frames carrying a door position or transformation value, including explicit unspecified values. Zero vehicle frames means the capture cannot answer the door question.

The same run writes a matching `field-delivery-*.json` report for every leaf field in the installed stream schema. Each count is the number of frames carrying that field. Explicit zero and false count as delivered. Empty sections can increment a section count without delivering any leaf values. A field count of zero applies only to that recording. Presence alone does not validate units or make a placeholder a usable reading.

## Dashboard

The root page opens on Overview. The Operations Desk places Player to the left of Flight on wide screens and above Flight on narrow screens. Circular gauges show health and oxygen. The header’s Light and Dark control remembers an explicit choice in the browser and otherwise follows the system theme. Player details include suit and environment readings and audio alerts. Flight Deck provides 30/60-second speed history and the three latest session events. Ship Systems shows power, warning, weapon, configuration and flight-assist states. Event Log keeps the latest 200 timestamped transitions and supports a warnings-only filter.

In System Indicators, green means no warning and red means a minor or major warning, including flashing variants. For other status lights, green means the labeled condition is true and red means false. The reader remembers which readings have supplied a usable value during its session, including zero and false. Previously reported fields stay visible across browser reloads. A hollow gray light or a dash means the current sample omitted a previously reported reading. Fields stay hidden until first reported. On foot and in EVA, ship instruments are hidden. Hover or inspect the accessible name for the reported state, including intermediate states such as gear deploying.

A section is marked last known after five seconds without an update, even if another section remains current. A reader disconnect clears the instrument panels. On foot or EVA, the actor remains visible and old vehicle data is cleared. The local decoder preserves explicitly transmitted zero and false values. For implicit proto3 scalar fields, a value omitted by the sender cannot establish either a zero or an off state.

### Kill monitor

Open **Event Log → Kill monitor** while the live reader is running. It scans the current LIVE `Game.log` at startup, identifies your character from the login record and follows new lines across log rotations. A matching actor death shows the timestamp and any reported weapon or damage cause. Overview and Event Log also show the latest telemetry death or downing. Each source has its own timestamp. This feature keeps results in the local session and works independently of the gRPC connection.

The normal Windows launcher locates the log automatically. For a custom install or descriptor-only reader, pass `--game-log "D:\path\to\LIVE\Game.log"`. If the login record is unavailable, supply your exact handle with `--player-handle MyHandle`.

### Session activity

Overview summarizes objectives, ship destructions credited to your character's nonzero game entity ID, and the last reported jurisdiction, monitored space and armistice state. Event Log can filter the timeline to missions, credited ship kills, travel or zone and hangar notifications. The reader replays the current Game.log at startup and follows it as you play. Log rotation starts a new activity summary. Quantum selections and projected origins are logged separately from explicit arrival events. When an arrival omits its destination, the timeline can show the same vessel’s last reported target as context.

The bottom of Event Log shows the newest readable saved level statistics report with its file date, frame count, CPU timings and memory peaks. GPU timing depends on the file reporting a valid GPU measurement. This report may predate the current session.

### Telemetry evidence

The installed LIVE descriptor establishes the actor vitals, locomotion, seat and atmosphere fields, vehicle instruments, deployment states, power switches, flight-assist active states, annunciators and weapon state. Gear and VTOL use the deployment enum. Shield and quantum drive indicators use annunciator states. The build has no shield-strength field.

The operator’s HUD and simultaneous Liberator frame verify the fraction-to-amount hydrogen conversion against the displayed 53.20M capacity. The HUD showed a full quantum tank while the decoded reading was zero. Overview displays a reported quantum value in its native scale, including an explicitly reported zero. The in-game quantity remains unverified. An omitted value stays unavailable. To verify its scale, capture a non-full tank with the HUD before and after a jump. Verify the scales and units for body and external temperature, heart rate, pressure, gravity, radiation, intoxication, speed, altitude, climb/descent, heading and boost against simultaneous target-build HUD readings before labeling them in live mode. Demo scenario units describe the simulation only.

## Custom game installation

If you installed the game outside the default Program Files directory, run the following from the extracted folder, substituting your actual executable path.

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python cockpit.py --game-exe "D:\path\to\StarCitizen\LIVE\Bin64\StarCitizen.exe" --port 8744 --open-browser
```

The reader subscribes only to `sc.game.telemetry.v1.GameTelemetryService/TelemetryStream` at `127.0.0.1:2942`. Telemetry must be enabled and the character spawned. It reconnects after the game closes and marks retained sections last known after five seconds without updates.

**A descriptor set matching your game build is required.** The game does not provide gRPC reflection. The included best-effort extractor reads the executable as a file and validates embedded descriptors before writing `telemetry.desc`. It does not change files or access process memory. Extraction and the TelemetryStream method were verified against the installed LIVE executable. A later build may change the descriptor format. It refuses to overwrite an existing output; after an update, use a new `--output` name.

Alternatively, supply a protobuf `FileDescriptorSet` (`.desc`), including imports, for the installed build. This project does not ship an extracted game schema or copy a community implementation. If you already have the corresponding `.proto` files and `protoc`, export a descriptor set with `--include_imports --descriptor_set_out=your-build.desc`. A single concatenated text dump with multiple `syntax` declarations is not directly compilable.

For your LIVE configuration, you already ran `setx GRPC_CLIENT_SERVER_ENABLED true` and `server.game.telemetry.enabled 1`. Restart the RSI Launcher fully if it was open when `setx` ran: that command affects newly started processes. The reader still needs a spawned character and a working local telemetry stream.

Community references (unofficial; API can change):

- [SC Telemetry Deck API documentation](https://github.com/Sokren/sc-telemetry-streamdeck#the-api-in-short)
- [Observed schema field names](https://github.com/Sokren/sc-telemetry-streamdeck/blob/main/schema/sc_extrait.proto)

## Instruments and alerts

- Health, oxygen, nutrition and hydration percentages are calculated from each gauge's current and maximum values. Missing or invalid maxima are unavailable, never interpreted as zero.
- Hydrogen uses the telemetry fill fraction on every ship. Values from zero to one display as a percentage. On the Liberator, the gauge also shows the amount calculated from the 53.20M capacity observed on its HUD. Quantum fuel quantity remains unverified.
- Each vital has an enable switch and percentage threshold. **Apply thresholds** affects the running reader session; settings reset when it restarts.
- Alerts fire once per low episode. Recovery by five percentage points rearms a warning, with at least 60 seconds between warnings for the same vital. Simultaneous warnings are combined. Audio is off until enabled; mute and volume controls are available.
- No browser permission for notifications, microphone or recording is used. Browser voice availability varies. Speech may depend on browser/OS voice services; select an installed local voice in the OS if offline speech is required.
- All fresh incoming fields can be inspected in the expandable telemetry view.
- The server binds only to localhost. Tablet/LAN access is not implemented in this first version.

## Verification

```sh
python -m unittest discover -s tests -v
python tests/check_browser.py
```

The optional browser check uses installed Chrome or Edge and a local test reader to exercise the real dashboard at desktop and narrow widths.

Live compatibility and units require a real game session and its matching descriptor. Automated tests do not establish compatibility with every LIVE/PTU build.

## Rebuilding the Windows release

Run `packaging/build_windows.py` with `--runtime-zip` pointing to a Windows x64 CPython 3.12 install archive (with a Lib directory) and `--site-packages` pointing to a Windows CPython 3.12 x64 environment containing the three release packages. This release uses Python 3.12.10, grpcio 1.85.3, protobuf 7.36.2 and typing_extensions 4.16.0. The default output is the single-file EXE. Supply `--output SC-Cockpit-Windows-x64.zip` to build the extracted-folder distribution. On Windows the builder defaults to the installed .NET Framework compiler. The ZIP contains the dashboard, runtime, dependency licenses, launcher and quick-start guide.
# sc-cockpit
