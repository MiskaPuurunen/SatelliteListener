# GPS Satellite Monitor

A desktop GUI tool that connects to a GPS receiver over the network (e.g. your phone or tablet's GPS shared over WiFi/hotspot) and visualizes what it's tracking in real time.

<img width="1152" height="753" alt="image" src="https://github.com/user-attachments/assets/dec70554-775d-4186-ae59-395d5408a27f" />


![Python](https://img.shields.io/badge/python-3.x-blue) ![Tkinter](https://img.shields.io/badge/GUI-Tkinter-informational) ![License](https://img.shields.io/badge/license-MIT-green)

## Features

- **Live sky-plot** - a polar chart showing every satellite the receiver can see or hear (North = top, center = straight overhead, edge = horizon). Satellites currently used in the position fix are highlighted in green.
- **Satellite table** - PRN, GNSS constellation, azimuth, elevation, signal strength (SNR), and used/unused status for each satellite, sorted by signal strength.
- **Raw data log** - a scrolling, auto-trimmed view of the raw stream coming from the GPS server, useful for debugging.
- **Fix status bar** - current fix type (no fix / 2D / 3D), number of satellites used vs. tracked, and lat/lon/altitude when available.
- **Demo mode** - generates simulated satellite data so you can try the UI without a real GPS source.

## Supported GPS Server Formats

The app auto-detects and parses two common formats from the incoming TCP stream:

| Format | Typical Port | Notes |
|---|---|---|
| **gpsd JSON** | 2947 (default) | Used by `gpsd` on Linux, and some Android apps that expose a gpsd-compatible interface. Sends `?WATCH={"enable":true,"json":true};` on connect to start the stream. Parses `SKY` (satellites) and `TPV` (position/fix) messages. |
| **Raw NMEA-0183** | Varies (often 4000–11000) | Streamed over a plain TCP socket by apps like GPS2IP, BlueNMEA, ShareGPS, GpsGate, etc. Check the app's settings for its port. Parses `GSV` (satellites in view), `GGA` (fix/position), and `GSA` (active satellites/DOP) sentences. |

Both the host/IP and port are configurable in the UI, so you can point it at any device on your network streaming one of these formats.

## Requirements

- Python 3
- [matplotlib](https://matplotlib.org/)
- Tkinter (ships with standard Python on Windows/macOS; on Linux you may need to install it separately)

### Install

```bash
pip install matplotlib
```

On Linux, if `tkinter` is missing:

```bash
sudo apt install python3-tk
```

## Usage

1. On your phone/tablet (any device with gps and WiFi), install and run a GPS-sharing app that exposes either a gpsd-compatible interface or raw NMEA over TCP, and make sure it's on the same network as your computer (e.g. connect your computer to the phone's hotspot, or vice versa).
2. Note the IP address and port the app is broadcasting on (check the app's settings screen).
3. Run the monitor:

   ```bash
   python SatListnr.py
   ```

4. Enter the **Tablet IP** and **Port** in the top toolbar and click **Connect**.
5. Watch the sky-plot, table, and fix status update live. Use **Disconnect** to close the connection.

No GPS source handy? Click **Demo Data** to see the UI populated with simulated satellites.

## GUI Overview

- **Top toolbar** - connection settings (IP/port), Connect/Disconnect, Demo Data toggle, connection status, and current fix summary.
- **Sky-plot (left)** - polar chart of satellite positions. Dot size scales with signal strength; green dots are satellites used in the fix, gray dots are tracked but unused.
- **Satellite table (middle)** - sortable-by-signal list of all tracked satellites with azimuth, elevation, SNR, and used status.
- **Raw data pane (right)** - live feed of raw JSON/NMEA lines from the server, with autoscroll toggle and a clear button. Capped at the last 500 lines.

## Troubleshooting

- **"Could not connect to \<ip\>:\<port\>"** - double-check that your computer and the GPS-sharing device are on the same network, that the app is actively running/broadcasting, and that the IP/port match what's shown in the app's settings.
- **Connecting but no satellites appear** - some apps need a moment (or a GPS lock) before they start streaming `SKY`/`GSV` data; try waiting outdoors with a clear view of the sky (Place and device used has a big difference).
- **Connection closes immediately** - some servers only accept one client at a time; make sure no other app/tool is already connected.

## License

MIT - feel free to use, modify, and share.
