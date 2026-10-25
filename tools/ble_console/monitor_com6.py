import serial
import time
import sys

while True:
    try:
        s = serial.Serial('COM6', 115200, timeout=1)
        print("COM6 Serial connected successfully", flush=True)
        while True:
            line = s.readline()
            if line:
                try:
                    sys.stdout.write(line.decode('utf-8', errors='ignore'))
                    sys.stdout.flush()
                except Exception:
                    pass
    except Exception as e:
        time.sleep(1)
