import json
import queue
import socket
import threading
import time
import tkinter as tk
from collections import deque
from tkinter import ttk, messagebox
import random
import math

import matplotlib
matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

RAW_LINE_LIMIT = 500
SOCKET_TIMEOUT = 2.0
GPSD_WATCH_CMD = b'?WATCH={"enable":true,"json":true};\n'



GNSS_NAMES = {
    "GP": "GPS", "GL": "GLONASS", "GA": "Galileo", "GB": "BeiDou",
    "BD": "BeiDou", "GQ": "QZSS", "GI": "NavIC", "GN": "Multi",
}



GPSD_GNSSID_NAMES = {0: "GPS", 1: "SBAS", 2: "Galileo", 3: "BeiDou",
                      5: "QZSS", 6: "GLONASS"}


#Connects to GPS server, reads it and returns parsed events -> these are pushed to GUI
class GpsReader(threading.Thread):

    def __init__(self, host, port, out_queue):
        super().__init__(daemon=True)
        self.host = host
        self.port = port
        self.q = out_queue

        self._stop = threading.Event()

        self.sock = None
    
        self._gsv_scratch = {}

    def stop(self):
        self._stop.set()
        try:
            if self.sock:
                self.sock.close()
        except OSError:
            pass

    def run(self):
        try:
            self.sock = socket.create_connection((self.host, self.port), timeout=SOCKET_TIMEOUT) #SOCKET
        except Exception as e:
            self.q.put(("error", f"Could not connect to {self.host}:{self.port} -> {e}"))
            return

        self.q.put(("status", f"Connected to {self.host}:{self.port}"))


        try:
            self.sock.sendall(GPSD_WATCH_CMD)
        except OSError:
            pass

        buf = b""
        self.sock.settimeout(1.0)
        while not self._stop.is_set():
            try:
                chunk = self.sock.recv(4096)
                if not chunk:
                    self.q.put(("status", "Connection closed by server"))
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    line = line.strip()
                    if line:
                        self._handle_line(line)
            except socket.timeout:
                continue
            except OSError:
                break

        try:
            self.sock.close()
        except OSError:
            pass

    def _handle_line(self, raw_bytes):
        try:
            text = raw_bytes.decode("ascii", errors="replace")

        except Exception:
            return
        

        self.q.put(("raw", text))

        if text.startswith("{"):
            self._handle_gpsd_json(text)

        elif text.startswith("$"):
            self._handle_nmea(text)




    #gpsd JSON
    def _handle_gpsd_json(self, text):
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            
            return
        
        cls = obj.get("class")
        if cls == "SKY":
            sats = []
            for s in obj.get("satellites", []):

                gnssid = s.get("gnssid")

                sats.append({
                    "prn": s.get("PRN") or s.get("svid"),
                    "az": s.get("az", 0),
                    "el": s.get("el", 0),
                    "snr": s.get("ss", 0) or 0,
                    "used": bool(s.get("used", False)),
                    "gnss": GPSD_GNSSID_NAMES.get(gnssid, ""),
                })
            if sats:
                self.q.put(("satellites", sats))
        elif cls == "TPV":
            fix_word = {0: "unknown", 1: "no fix", 2: "2D", 3: "3D"}.get(obj.get("mode", 0), "?")
            self.q.put(("fix", {
                "fix": fix_word,
                "lat": obj.get("lat"),
                "lon": obj.get("lon"),
                "alt": obj.get("alt"),
            }))

    #raw NMEA
    def _handle_nmea(self, sentence):
        body = sentence.split("*")[0] #checksum
        fields = body.split(",")
        if len(fields) < 1:
            return
        
        tag = fields[0][1:]
        talker = tag[:2]
        stype = tag[2:]


        if stype == "GSV":
            self._handle_gsv(talker, fields)
        elif stype == "GGA":
            self._handle_gga(fields)
        elif stype == "GSA":
            self._handle_gsa(fields)

    def _handle_gsv(self, talker, f):

        # $--GSV,total_msgs,msg_num,num_sats,[prn,el,az,snr]*4,checksum

        try:
            total_msgs = int(f[1])
            msg_num = int(f[2])
        except (ValueError, IndexError):
            return

        if msg_num == 1:
            self._gsv_scratch[talker] = []

        sats = self._gsv_scratch.setdefault(talker, [])
        i = 4

        while i + 3 < len(f) and f[i]:
            try:
                prn = f[i]
                el = int(f[i + 1]) if f[i + 1] else 0
                az = int(f[i + 2]) if f[i + 2] else 0
                snr = int(f[i + 3]) if f[i + 3] else 0
                sats.append({
                    "prn": prn, "az": az, "el": el, "snr": snr,
                    "used": None,
                    "gnss": GNSS_NAMES.get(talker, talker),
                })
            except ValueError:
                pass
            i += 4

        if msg_num == total_msgs:
            merged = []
            for tlist in self._gsv_scratch.values():
                merged.extend(tlist)

            if merged:
                self.q.put(("satellites_nmea", merged))

    def _handle_gsa(self, f):

        # $--GSA,mode1,mode2,sat1..sat12,PDOP,HDOP,VDOP*cc

        try:
            mode2 = f[2]
            fix_word = {"1": "no fix", "2": "2D", "3": "3D"}.get(mode2, "?")

        except IndexError:

            fix_word = "?"
        used_prns = set()
        for v in f[3:15]:
            if v:
                used_prns.add(v)
        self.q.put(("used_prns", used_prns))
        self.q.put(("fix", {"fix": fix_word, "lat": None, "lon": None, "alt": None}))

    def _handle_gga(self, f):
        try:
            num_sats = int(f[7]) if f[7] else None
            lat = self._nmea_to_deg(f[2], f[3]) if f[2] else None
            lon = self._nmea_to_deg(f[4], f[5]) if f[4] else None
            alt = float(f[9]) if f[9] else None
        except (IndexError, ValueError):
            return
        
        self.q.put(("fix", {"fix": None, "lat": lat, "lon": lon, "alt": alt, "num_sats": num_sats}))

    @staticmethod
    def _nmea_to_deg(value, hemi):
        # ddmm.mmmm or dddmm.mmmm -> decimal degrees
        dot = value.find(".")
        deg_len = dot - 2
        deg = float(value[:deg_len])
        minutes = float(value[deg_len:])
        dec = deg + minutes / 60.0
        if hemi in ("S", "W"):
            dec = -dec
        return dec


# GUI
# GUI
# GUI
# GUI 
# GUI
# GUI
class App:
    def __init__(self, root):
        self.root = root
        root.title("GPS Satellite Monitor")
        root.geometry("1150x720")

        self.q = queue.Queue()
        self.reader = None
        self.demo_running = False
        self.satellites = {} #(prn, az, el, snr, used, gnss)
        self.used_prns = set()
        self.raw_lines = deque(maxlen=RAW_LINE_LIMIT)

        self._build_ui()
        self.root.after(150, self._poll_queue)

    # Build for UI
    def _build_ui(self):
        top = ttk.Frame(self.root, padding=8)
        top.pack(side=tk.TOP, fill=tk.X)

        ttk.Label(top, text="Tablet IP:").pack(side=tk.LEFT)
        self.host_var = tk.StringVar(value="192.168.1.100")
        ttk.Entry(top, textvariable=self.host_var, width=16).pack(side=tk.LEFT, padx=(2, 10))

        ttk.Label(top, text="Port:").pack(side=tk.LEFT)
        self.port_var = tk.StringVar(value="2947")
        ttk.Entry(top, textvariable=self.port_var, width=8).pack(side=tk.LEFT, padx=(2, 10))

        self.connect_btn = ttk.Button(top, text="Connect", command=self.on_connect)
        self.connect_btn.pack(side=tk.LEFT, padx=4)
        self.disconnect_btn = ttk.Button(top, text="Disconnect", command=self.on_disconnect, state=tk.DISABLED)
        self.disconnect_btn.pack(side=tk.LEFT, padx=4)
        self.demo_btn = ttk.Button(top, text="Demo Data", command=self.on_toggle_demo)
        self.demo_btn.pack(side=tk.LEFT, padx=(20, 4))

        self.status_var = tk.StringVar(value="Not connected")
        ttk.Label(top, textvariable=self.status_var, foreground="#444").pack(side=tk.LEFT, padx=20)

        self.fix_var = tk.StringVar(value="Fix: -- | Used: 0/0")
        ttk.Label(top, textvariable=self.fix_var, foreground="#006400").pack(side=tk.RIGHT)

        #Middlearea:skyplot(left)+table(middle)+rawdata(right)
        mid = ttk.Frame(self.root)
        mid.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        plot_frame = ttk.Frame(mid)
        plot_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=8, pady=4)

        self.fig = plt.Figure(figsize=(5.5, 5.5))
        self.ax = self.fig.add_subplot(111, projection="polar")
        self._setup_polar_axes()
        self.canvas = FigureCanvasTkAgg(self.fig, master=plot_frame)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

        table_frame = ttk.Frame(mid, width=340)
        table_frame.pack(side=tk.LEFT, fill=tk.BOTH, padx=8, pady=4)
        table_frame.pack_propagate(False)

        cols = ("prn", "gnss", "az", "el", "snr", "used")
        self.tree = ttk.Treeview(table_frame, columns=cols, show="headings", height=20)
        headers = {"prn": "PRN", "gnss": "GNSS", "az": "Az°", "el": "El°", "snr": "SNR", "used": "Used"}
        widths = {"prn": 55, "gnss": 70, "az": 50, "el": 50, "snr": 50, "used": 50}
        for c in cols:
            self.tree.heading(c, text=headers[c])
            self.tree.column(c, width=widths[c], anchor=tk.CENTER)
        self.tree.pack(fill=tk.BOTH, expand=True)

        #Right:rawdatapane
        raw_frame = ttk.LabelFrame(mid, text="Raw data", padding=4, width=340)
        raw_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=False, padx=8, pady=4)
        raw_frame.pack_propagate(False)

        raw_toolbar = ttk.Frame(raw_frame)
        raw_toolbar.pack(side=tk.TOP, fill=tk.X)
        self.autoscroll_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(raw_toolbar, text="Autoscroll", variable=self.autoscroll_var).pack(side=tk.LEFT)
        ttk.Button(raw_toolbar, text="Clear", command=self.on_clear_raw).pack(side=tk.LEFT, padx=6)

        text_container = ttk.Frame(raw_frame)
        text_container.pack(fill=tk.BOTH, expand=True)
        self.raw_text = tk.Text(text_container, wrap="none", font=("Consolas", 9))
        vsb = ttk.Scrollbar(text_container, orient="vertical", command=self.raw_text.yview)
        self.raw_text.configure(yscrollcommand=vsb.set)
        self.raw_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.LEFT, fill=tk.Y)

    def _setup_polar_axes(self):
        self.ax.clear()
        self.ax.set_theta_zero_location("N")
        self.ax.set_theta_direction(-1)
        self.ax.set_rlim(0, 90)
        self.ax.set_rticks([0, 30, 60, 90])
        self.ax.set_yticklabels(["90°", "60°", "30°", "0°"])
        self.ax.set_title("Sky View (N up)", pad=15)
        self.ax.grid(True, alpha=0.4)







#Connection
    def on_connect(self):
        if self.demo_running:
            self.on_toggle_demo()
        host = self.host_var.get().strip()
        try:
            port = int(self.port_var.get().strip())
        except ValueError:
            messagebox.showerror("Invalid port", "Port must be a number.")
            return

        self.status_var.set(f"Connecting to {host}:{port} ...")
        self.reader = GpsReader(host, port, self.q)
        self.reader.start()
        self.connect_btn.config(state=tk.DISABLED)
        self.disconnect_btn.config(state=tk.NORMAL)

    def on_disconnect(self):

        if self.reader:
            self.reader.stop()
            self.reader = None

        self.status_var.set("Disconnected")
        self.connect_btn.config(state=tk.NORMAL)
        self.disconnect_btn.config(state=tk.DISABLED)

    def on_clear_raw(self):

        self.raw_text.delete("1.0", tk.END)
        self.raw_lines.clear()

    def on_toggle_demo(self):

        if self.demo_running:
            self.demo_running = False
            self.demo_btn.config(text="Demo Data")
            self.status_var.set("Demo stopped")


        else:
            if self.reader:
                self.on_disconnect()


            self.demo_running = True
            self.demo_btn.config(text="Stop Demo")
            self.status_var.set("Showing simulated demo data")
            self._demo_tick()


#demo config
    def _demo_tick(self):

        if not self.demo_running:
            return
        random.seed()
        sats = []
        t = time.time()
        for i in range(10):
            prn = str(i + 1)
            az = (i * 37 + t * 3) % 360
            el = 10 + 80 * abs(math.sin(t / 5 + i))
            snr = 15 + 25 * abs(math.sin(t / 3 + i * 0.7))
            sats.append({"prn": prn, "az": az, "el": el, "snr": snr,
                         "used": el > 25, "gnss": "GPS" if i < 7 else "GLONASS"})
        self._update_satellites(sats)
        used = sum(1 for s in sats if s["used"])
        self.fix_var.set(f"Fix: 3D (demo) | Used: {used}/{len(sats)}")
        self._append_raw(f'{{"class":"SKY","note":"demo tick","satellites":{len(sats)}}}')
        self.root.after(1000, self._demo_tick)




# Q polling from the reader thread

    def _poll_queue(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "raw":
                    self._append_raw(payload)

                elif kind == "status":
                    self.status_var.set(payload)

                elif kind == "error":
                    self.status_var.set(payload)
                    self.connect_btn.config(state=tk.NORMAL)
                    self.disconnect_btn.config(state=tk.DISABLED)

                elif kind == "satellites":
                    self._update_satellites(payload)
                elif kind == "satellites_nmea":
                    for s in payload:
                        if s["prn"] in self.used_prns:
                            s["used"] = True
                        elif s["used"] is None:
                            s["used"] = False
                    self._update_satellites(payload)
                elif kind == "used_prns":
                    self.used_prns = payload
                elif kind == "fix":
                    self._update_fix(payload)
        except queue.Empty:
            pass
        self.root.after(150, self._poll_queue)




#RENDER
    def _append_raw(self, text):
        self.raw_lines.append(text)
        self.raw_text.insert(tk.END, text + "\n")


        num_lines = int(self.raw_text.index("end-1c").split(".")[0])
        if num_lines > RAW_LINE_LIMIT:
            self.raw_text.delete("1.0", f"{num_lines - RAW_LINE_LIMIT}.0")

        if self.autoscroll_var.get():
            self.raw_text.see(tk.END)


    def _update_satellites(self, sats):

        for s in sats:
            self.satellites[s["prn"]] = s

        self._redraw_plot()
        self._redraw_table()

    def _update_fix(self, info):

        used = sum(1 for s in self.satellites.values() if s.get("used"))


        total = len(self.satellites)
        fix = info.get("fix") or "--"
        extra = ""
        if info.get("lat") is not None and info.get("lon") is not None:
            extra = f" | {info['lat']:.5f}, {info['lon']:.5f}"
            if info.get("alt") is not None:
                extra += f" @ {info['alt']:.0f}m"
        self.fix_var.set(f"Fix: {fix} | Used: {used}/{total}{extra}")

    def _redraw_plot(self):

        self._setup_polar_axes()
        if not self.satellites:

            self.canvas.draw_idle()
            return
        
        for s in self.satellites.values():
            theta = math.radians(s["az"])
            r = 90 - s["el"]
            snr = s.get("snr") or 0
            size = 40 + min(snr, 50) * 4
            color = "#2ca02c" if s.get("used") else "#999999"
            self.ax.scatter([theta], [r], s=size, c=color, edgecolors="black", linewidths=0.6, zorder=3)
            self.ax.annotate(str(s["prn"]), (theta, r), textcoords="offset points",
                              xytext=(0, 6), ha="center", fontsize=8, zorder=4)
            
        self.canvas.draw_idle()

    def _redraw_table(self):
        self.tree.delete(*self.tree.get_children())
        for s in sorted(self.satellites.values(), key=lambda x: -(x.get("snr") or 0)):
            used_str = "Yes" if s.get("used") else "No"

            self.tree.insert("", tk.END, values=(
                s["prn"], s.get("gnss", ""), f"{s['az']:.0f}", f"{s['el']:.0f}",
                f"{s.get('snr', 0):.0f}", used_str))

    def on_close(self):
        if self.reader:
            self.reader.stop()

        self.root.destroy()




def main():
    root = tk.Tk()
    app = App(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()


if __name__ == "__main__":
    main()