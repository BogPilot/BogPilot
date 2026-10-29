# Remote Screen Viewer (Tesla MCU2 browser)

Branch `bogpilot-mcu2-ui`. A view-only copy of the comma screen, served by the Qt UI on port **8888**,
for watching openpilot on the Tesla center screen. Written from scratch for BogPilot. No code from
other screen-streaming projects is used.

## What it serves
| URL | What |
|---|---|
| `http://<comma-ip>:8888/` | Viewer page: black background, 2:1 picture scaled to fit the screen |
| `/frame.jpg` | The next screen frame as one JPEG |
| `/stream` | `multipart/x-mixed-replace` MJPEG, for other browsers and players |
| `/stats` | JSON: viewers, fps, grab and encode time, frame size, bandwidth |

- **View-only.** There are no touch, input or write endpoints. Anything other than GET/HEAD gets
  `405`, and the only paths are the four above. There's no login because nothing can be changed.
- Frames are 1080x540 (half of the 2160x1080 panel), JPEG quality 70, at most 10 fps. The constants
  are at the top of `frogpilot/ui/screenstream/screen_stream.h`.
- At most 4 connections at once. A fifth gets `503`.

## Page design
The page fetches `/frame.jpg` in a loop and draws each frame on a `<canvas>`. The MCU2 browser
(Chrome 136) pauses `<video>` while in Drive but keeps painting canvas. `fetch` +
`createImageBitmap` needs no WebRTC, WebCodecs or MSE. Each `/frame.jpg` request waits for the next
captured frame, so the loop paces itself to the capture rate and the Wi-Fi link without queueing.
`/stream` is there for direct use (VLC or another browser).

## Cost
- **No viewer = no capture.** The server only listens on the socket. Capture starts when a page or
  stream connects and stops about 2 s after the last one leaves.
- The UI thread does one thing per frame: render the window into a 1080x540 image. JPEG encoding,
  HTTP and sockets run on a separate low-priority thread (nice 10).
- If grabs get slow, the capture rate drops on its own so grabs use at most 15% of the UI thread.

## On/off
Settings → Device → Screen Settings → **Remote Screen Viewer** (on by default).

It's file-backed like the other BogPilot toggles: the comma's prebuilt `params_pyx.so` can't store
new Params keys, and `Params::clearAll` would delete an unknown key. The file is
`/data/params_bogpilot/RemoteUIStream`. No file means on.
```
echo 0 > /data/params_bogpilot/RemoteUIStream   # off (the server closes within 2 s)
echo 1 > /data/params_bogpilot/RemoteUIStream   # on
```

## Tesla browser: private addresses are blocked
The Tesla browser refuses private IP addresses (`10.x`, `172.16-31.x`, `192.168.x`, and possibly
`100.64.x`). It checks the address a hostname points to, so a local hostname that points to a
`192.168.x` address is blocked too.

Proof-of-concept plan (travel router in the car, not connected to the internet):
1. Have the router's DHCP hand out a **non-private** range. Use `198.18.0.0/15` (reserved for test
   networks, so nothing on the internet uses it), for example router `198.18.0.1` with clients in
   `198.18.0.100-198.18.0.200`. Avoid `100.64.0.0/10`; the Tesla browser may block it too.
2. Join both the comma and the Tesla to that Wi-Fi.
3. Find the comma's address (Settings → Network, or the router's client list) and open
   `http://<comma-ip>:8888` in the Tesla browser.

## Getting it onto the comma
The comma runs the **prebuilt** UI binary (`selfdrive/ui/ui`, built in July). The `prebuilt` file
makes it skip compiling, so this source change does nothing until the UI is rebuilt on the comma.
Rebuilding also picks up the other UI source changes made since that prebuilt binary
(`settings.cc`, `software_settings.cc`, `alerts.cc`, `util.cc`, `theme_settings.cc`).
