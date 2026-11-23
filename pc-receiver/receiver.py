import asyncio
import ctypes
from ctypes import wintypes
import os
import socket
import subprocess
import time
from aiohttp import web

PORT = 8765
ADB_PATH = r"C:\Users\PC\AppData\Local\Android\Sdk\platform-tools\adb.exe"

latest_jpeg = None
frame_event = None
frame_count = 0
audio_packets = 0
connected_clients = 0
last_fps = 0.0
_fps_counter = 0
_fps_time = time.time()

WHDR_DONE = 0x00000001


class WAVEFORMATEX(ctypes.Structure):
    _fields_ = [
        ("wFormatTag", wintypes.WORD),
        ("nChannels", wintypes.WORD),
        ("nSamplesPerSec", wintypes.DWORD),
        ("nAvgBytesPerSec", wintypes.DWORD),
        ("nBlockAlign", wintypes.WORD),
        ("wBitsPerSample", wintypes.WORD),
        ("cbSize", wintypes.WORD),
    ]


class WAVEHDR(ctypes.Structure):
    _fields_ = [
        ("lpData", ctypes.c_void_p),
        ("dwBufferLength", wintypes.DWORD),
        ("dwBytesRecorded", wintypes.DWORD),
        ("dwUser", ctypes.c_void_p),
        ("dwFlags", wintypes.DWORD),
        ("dwLoops", wintypes.DWORD),
        ("lpNext", ctypes.c_void_p),
        ("reserved", ctypes.c_void_p),
    ]


class WinMMAudioPlayer:
    """64-bit safe Windows PCM audio player using winmm.dll."""

    def __init__(self, sample_rate=16000, channels=1, bits=16):
        self.winmm = ctypes.windll.winmm
        self.hwave = wintypes.HANDLE()
        self.opened = False
        self._ring = []

        self.winmm.waveOutOpen.argtypes = [
            ctypes.POINTER(wintypes.HANDLE),
            wintypes.UINT,
            ctypes.POINTER(WAVEFORMATEX),
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        self.winmm.waveOutOpen.restype = wintypes.UINT

        self.winmm.waveOutPrepareHeader.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(WAVEHDR),
            wintypes.UINT,
        ]
        self.winmm.waveOutPrepareHeader.restype = wintypes.UINT

        self.winmm.waveOutWrite.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(WAVEHDR),
            wintypes.UINT,
        ]
        self.winmm.waveOutWrite.restype = wintypes.UINT

        self.winmm.waveOutUnprepareHeader.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(WAVEHDR),
            wintypes.UINT,
        ]
        self.winmm.waveOutUnprepareHeader.restype = wintypes.UINT

        wfx = WAVEFORMATEX()
        wfx.wFormatTag = 1  # WAVE_FORMAT_PCM
        wfx.nChannels = channels
        wfx.nSamplesPerSec = sample_rate
        wfx.wBitsPerSample = bits
        wfx.nBlockAlign = (channels * bits) // 8
        wfx.nAvgBytesPerSec = sample_rate * wfx.nBlockAlign
        wfx.cbSize = 0

        res = self.winmm.waveOutOpen(
            ctypes.byref(self.hwave),
            wintypes.UINT(0xFFFFFFFF),  # WAVE_MAPPER
            ctypes.byref(wfx),
            None,
            None,
            0,
        )
        if res == 0:
            self.opened = True

    def play_pcm(self, pcm_bytes: bytes):
        if not self.opened or not pcm_bytes:
            return
        try:
            # Reclaim completed buffers
            still_active = []
            for old_hdr, old_buf in self._ring:
                if old_hdr.dwFlags & WHDR_DONE:
                    self.winmm.waveOutUnprepareHeader(
                        self.hwave, ctypes.byref(old_hdr), ctypes.sizeof(WAVEHDR)
                    )
                else:
                    still_active.append((old_hdr, old_buf))
            self._ring = still_active

            if len(self._ring) > 32:
                return

            buf = ctypes.create_string_buffer(pcm_bytes, len(pcm_bytes))
            hdr = WAVEHDR()
            hdr.lpData = ctypes.cast(buf, ctypes.c_void_p)
            hdr.dwBufferLength = len(pcm_bytes)
            hdr.dwFlags = 0
            hdr.dwLoops = 0

            if (
                self.winmm.waveOutPrepareHeader(
                    self.hwave, ctypes.byref(hdr), ctypes.sizeof(WAVEHDR)
                )
                == 0
            ):
                if (
                    self.winmm.waveOutWrite(
                        self.hwave, ctypes.byref(hdr), ctypes.sizeof(WAVEHDR)
                    )
                    == 0
                ):
                    self._ring.append((hdr, buf))
        except Exception:
            pass


audio_player = None
vcam = None


def try_init_virtual_cam(width=640, height=480):
    global vcam
    if vcam is not None:
        return
    try:
        import pyvirtualcam
        vcam = pyvirtualcam.Camera(width=width, height=height, fps=25)
        print(f"[VirtualCam] Active: {vcam.device}")
    except Exception:
        vcam = False


def push_virtual_cam(jpeg_bytes: bytes):
    global vcam
    if vcam is False:
        return
    try:
        import cv2
        import numpy as np
        arr = np.frombuffer(jpeg_bytes, dtype=np.uint8)
        frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if frame is None:
            return
        h, w, _ = frame.shape
        if vcam is None:
            try_init_virtual_cam(w, h)
        if vcam and vcam is not False:
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            if (w, h) != (vcam.width, vcam.height):
                rgb = cv2.resize(rgb, (vcam.width, vcam.height))
            vcam.send(rgb)
    except Exception:
        pass


def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


async def adb_reverse_loop():
    while True:
        if os.path.exists(ADB_PATH):
            try:
                subprocess.run(
                    [ADB_PATH, "reverse", f"tcp:{PORT}", f"tcp:{PORT}"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=4,
                )
            except Exception:
                pass
        await asyncio.sleep(4)


async def ws_handler(request):
    global latest_jpeg, frame_count, audio_packets, connected_clients
    global last_fps, _fps_counter, _fps_time

    ws = web.WebSocketResponse(max_msg_size=4 * 1024 * 1024)
    await ws.prepare(request)
    connected_clients += 1
    peer = request.remote
    print(f"[DirectCam] Phone connected from {peer}", flush=True)

    try:
        async for msg in ws:
            if msg.type == web.WSMsgType.BINARY and len(msg.data) > 1:
                tag = msg.data[0]
                payload = msg.data[1:]
                if tag == 0x01:  # Video JPEG frame
                    latest_jpeg = payload
                    frame_count += 1
                    _fps_counter += 1
                    now = time.time()
                    if now - _fps_time >= 1.0:
                        last_fps = round(_fps_counter / (now - _fps_time), 1)
                        _fps_counter = 0
                        _fps_time = now
                    if frame_event:
                        frame_event.set()
                        frame_event.clear()
                    push_virtual_cam(payload)
                elif tag == 0x02:  # 16kHz 16-bit mono PCM audio
                    audio_packets += 1
                    if audio_player:
                        audio_player.play_pcm(payload)
    finally:
        connected_clients = max(0, connected_clients - 1)
        print(f"[DirectCam] Phone disconnected ({peer})", flush=True)

    return ws


async def mjpeg_handler(request):
    response = web.StreamResponse(
        status=200,
        reason="OK",
        headers={
            "Content-Type": "multipart/x-mixed-replace; boundary=frame",
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
        },
    )
    await response.prepare(request)
    try:
        while True:
            if latest_jpeg:
                await response.write(
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n\r\n" + latest_jpeg + b"\r\n"
                )
            await asyncio.sleep(0.04)
    except Exception:
        pass
    return response


async def snapshot_handler(request):
    if not latest_jpeg:
        return web.Response(status=204)
    return web.Response(body=latest_jpeg, content_type="image/jpeg")


async def status_handler(request):
    return web.json_response(
        {
            "connected": connected_clients > 0,
            "clients": connected_clients,
            "frames": frame_count,
            "audio_packets": audio_packets,
            "fps": last_fps,
            "has_frame": latest_jpeg is not None,
            "frame_bytes": len(latest_jpeg) if latest_jpeg else 0,
            "local_ip": get_local_ip(),
            "port": PORT,
        }
    )


MONITOR_HTML = """<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="UTF-8">
<title>DirectCam Receiver</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; border-radius: 0 !important; font-family: Tahoma, Arial, sans-serif; }
  html, body { width: 100%; height: 100%; background: #0a0a0a; color: #d4d4d4; display: flex; flex-direction: column; overflow: hidden; }
  #stage { flex: 1; background: #000; display: flex; align-items: center; justify-content: center; overflow: hidden; }
  #streamImg { width: 100%; height: 100%; object-fit: contain; display: block; }
  #bar { height: 42px; background: #111; border-top: 1px solid #2b2b2b; display: grid; grid-template-columns: repeat(4, 1fr); font-size: 13px; }
  .cell { display: flex; align-items: center; padding: 0 14px; border-left: 1px solid #2b2b2b; color: #b5b5b5; }
  .cell:last-child { border-left: none; direction: ltr; }
</style>
</head>
<body>
  <div id="stage">
    <img id="streamImg" src="/stream.mjpg" alt="">
  </div>
  <div id="bar">
    <div class="cell" id="stConn">الحالة: بانتظار الهاتف</div>
    <div class="cell" id="stFps">الإطارات: 0 FPS</div>
    <div class="cell" id="stAud">حزم الصوت: 0</div>
    <div class="cell" id="stAddr">127.0.0.1:8765</div>
  </div>
  <script>
    setInterval(async () => {
      try {
        const r = await fetch('/status');
        const d = await r.json();
        document.getElementById('stConn').textContent = d.connected ? 'الحالة: متصل ويبث' : 'الحالة: بانتظار الهاتف';
        document.getElementById('stFps').textContent = 'السرعة: ' + d.fps + ' FPS (' + d.frames + ')';
        document.getElementById('stAud').textContent = 'الصوت: ' + d.audio_packets;
        document.getElementById('stAddr').textContent = d.local_ip + ':' + d.port;
      } catch (e) {}
    }, 1000);
  </script>
</body>
</html>
"""


async def index_handler(request):
    return web.Response(text=MONITOR_HTML, content_type="text/html")


async def on_startup(app):
    global frame_event, audio_player
    frame_event = asyncio.Event()
    audio_player = WinMMAudioPlayer(sample_rate=16000, channels=1, bits=16)
    app["adb_task"] = asyncio.create_task(adb_reverse_loop())


async def on_cleanup(app):
    app["adb_task"].cancel()


def main():
    ip = get_local_ip()
    print("==========================================", flush=True)
    print(" DirectCam PC Receiver", flush=True)
    print(f" USB Address   : 127.0.0.1:{PORT}", flush=True)
    print(f" Wi-Fi Address : {ip}:{PORT}", flush=True)
    print(f" Monitor URL   : http://127.0.0.1:{PORT}/", flush=True)
    print(f" MJPEG Stream  : http://127.0.0.1:{PORT}/stream.mjpg", flush=True)
    print("==========================================", flush=True)

    app = web.Application()
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    app.router.add_get("/", index_handler)
    app.router.add_get("/ws", ws_handler)
    app.router.add_get("/stream.mjpg", mjpeg_handler)
    app.router.add_get("/snapshot.jpg", snapshot_handler)
    app.router.add_get("/status", status_handler)
    web.run_app(app, host="0.0.0.0", port=PORT, print=None)


if __name__ == "__main__":
    main()
