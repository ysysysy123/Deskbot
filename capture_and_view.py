#!/usr/bin/env python3
"""
Autonomous Camera Frame Capture and Decoder for Deskbot (SZPI-ESP32S3).
Communicates with the board over USB CDC (/dev/ttyACM0) using DUMP_FRAME.
"""

import base64
import os
import re
import sys
import time
import numpy as np
from PIL import Image
import serial

def capture_frame(port="/dev/ttyACM0", baudrate=115200, out_path="captured_frame.png"):
    print(f"Connecting to {port}...")
    s = None
    for _ in range(15):
        try:
            s = serial.Serial(port, baudrate, timeout=1)
            break
        except Exception:
            time.sleep(0.2)
    if not s:
        print(f"Error: Unable to open {port}")
        return False

    time.sleep(0.5)
    s.reset_input_buffer()
    s.write(b"DUMP_FRAME\r\n")
    s.flush()

    raw_lines = []
    cam_info = ""
    recording = False
    start = time.time()
    while time.time() - start < 4.0:
        l = s.readline().decode("utf-8", errors="ignore").strip()
        if "CAM_INFO:" in l:
            cam_info = l
            print(f"Sensor Info: {l}")
        if "===BEGIN_FRAME" in l:
            recording = True
            continue
        if "===END_FRAME===" in l:
            break
        if recording:
            raw_lines.append(l)

    s.close()
    chunks = [base64.b64decode(l) for l in raw_lines if re.match(r"^[A-Za-z0-9+/=]+$", l)]
    data = b"".join(chunks)

    if len(data) != 153600:
        print(f"Error: expected 153600 bytes, got {len(data)}")
        return False

    # Decode RGB565 Big-Endian to 320x240 RGB888 PNG
    u16 = np.frombuffer(data, dtype=">u2")
    r = ((u16 >> 11) & 0x1F) * 255 // 31
    g = ((u16 >> 5) & 0x3F) * 255 // 63
    b = (u16 & 0x1F) * 255 // 31
    rgb = np.stack([r, g, b], axis=-1).reshape((240, 320, 3)).astype(np.uint8)

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    Image.fromarray(rgb).save(out_path)
    print(f"Successfully saved {out_path} ({320}x{240})")
    return True

if __name__ == "__main__":
    out_file = sys.argv[1] if len(sys.argv) > 1 else "captured_frame.png"
    capture_frame(out_path=out_file)
