#!/usr/bin/env python3
"""
VCP Serial Feed — streams live UART data from /dev/ttyACM0 to stdout.

Designed for:  tail -f .pane3_output
Meant to run in a dedicated terminal pane during HITL verification.

Usage:
    python3 hil_framework/vcp_feed.py                       # defaults: /dev/ttyACM0 @ 115200
    python3 hil_framework/vcp_feed.py --port /dev/ttyACM1 --baud 9600

Kill: pkill -f vcp_feed.py
"""
from __future__ import annotations

import argparse
import serial
import sys
import time


def main() -> None:
    parser = argparse.ArgumentParser(description="VCP Serial Feed — tail-friendly live UART stream")
    parser.add_argument("--port", default="/dev/ttyACM0", help="Serial port (default: /dev/ttyACM0)")
    parser.add_argument("--baud", type=int, default=115200, help="Baud rate (default: 115200)")
    args = parser.parse_args()

    try:
        ser = serial.Serial(args.port, args.baud, timeout=1)
        ser.reset_input_buffer()
    except serial.SerialException as e:
        sys.stderr.write(f"[vcp_feed] ERROR: Cannot open {args.port}: {e}\n")
        sys.exit(1)

    sys.stderr.write(f"[vcp_feed] Streaming {args.port} @ {args.baud} baud... (kill with Ctrl+C)\n")
    sys.stderr.flush()

    try:
        while True:
            if ser.in_waiting:
                line_bytes = ser.readline()
                if line_bytes:
                    line = line_bytes.decode("utf-8", errors="replace").strip()
                    if line:
                        ts = time.strftime("%H:%M:%S")
                        sys.stdout.write(f"[{ts}] {line}\n")
                        sys.stdout.flush()
            else:
                time.sleep(0.05)
    except KeyboardInterrupt:
        sys.stderr.write("\n[vcp_feed] Stopped.\n")
    finally:
        ser.close()


if __name__ == "__main__":
    main()
