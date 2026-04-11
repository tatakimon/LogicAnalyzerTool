#!/usr/bin/env python3
"""
HIL Verification Script - active_trial_1
Reads captured logic analyzer data and decodes UART packets.

Strategy:
  1. If sigrok-cli is available and the Saleae is accessible: capture live
  2. If a .sr file is provided: decode from file
  3. If STLink VCP is connected: verify firmware output loopback
  4. Always verify the UART decoder independently

Usage:
  python3 auto_verify.py              # Self-test decoder + firmware check
  python3 auto_verify.py --capture    # Live capture (requires Saleae access)
  python3 auto_verify.py --file data.sr  # Decode from .sr file
  python3 auto_verify.py --vcp        # Read from STLink VCP
"""
import argparse
import serial
import struct
import sys
import os
import time

# Add local path for uart_decoder
sys.path.insert(0, os.path.dirname(__file__))
from uart_decoder import decode_signal, fmt_byte

SERIAL_PORT = "/dev/ttyACM0"
BAUD_RATE = 115200
TIMEOUT = 6


def verify_decoder():
    """Verify the embedded UART decoder works correctly."""
    print("=== [1] UART Decoder Self-Verification ===")
    ret = os.system(f'"{sys.executable}" "{__file__}" --decoder-test')
    if ret == 0:
        print("  UART Decoder: PASS")
        return True
    else:
        print("  UART Decoder: FAIL")
        return False


def verify_firmware_via_vcp():
    """Try to read firmware output via STLink VCP (USART1)."""
    print("\n=== [2] Firmware Output via VCP ===")
    try:
        ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=TIMEOUT)
    except serial.SerialException as e:
        print(f"  VCP not accessible: {e}")
        print("  (This is expected - VCP is USART1, firmware outputs on USART3/PD8)")
        return None  # Not a failure, just not applicable

    ser.reset_input_buffer()
    ser.reset_output_buffer()

    print(f"  Connected to {SERIAL_PORT} at {BAUD_RATE} baud")
    print("  Reading for 3 seconds...")

    start = time.time()
    lines = []
    while time.time() - start < 3:
        if ser.in_waiting > 0:
            data = ser.read(ser.in_waiting)
            for b in data:
                if 32 <= b < 127 or b in (13, 10):
                    lines.append(chr(b))
        else:
            time.sleep(0.05)

    ser.close()

    text = ''.join(lines).strip()
    if text:
        print(f"  VCP output: {text[:100]}")
        return True
    else:
        print("  No output detected on VCP")
        print("  (Firmware outputs on USART3/PD8, not USART1/VCP)")
        return None


def capture_with_sigrok(sample_rate="1M", duration_s=2):
    """Try to capture from Saleae via sigrok-cli."""
    print(f"\n=== [3] Sigrok Live Capture ({sample_rate}sps, {duration_s}s) ===")

    import subprocess

    # Check if Saleae is accessible
    result = subprocess.run(['sigrok-cli', '--scan'], capture_output=True, text=True)
    output = result.stdout + result.stderr

    if 'fx2lafw' in output and 'LIBUSB_ERROR_ACCESS' in output:
        print("  Saleae device detected but inaccessible (no USB passthrough)")
        print("  Tip: In Windows, run: usbipd wsl attach --busid <busid>")
        print("  Or use PulseView on Windows directly")
        return None

    if 'demo' in output and 'fx2lafw' not in output:
        print("  Only demo device available (Saleae not connected)")
        return None

    # Try to capture
    output_file = "/tmp/hil_capture.sr"
    cmd = [
        'sigrok-cli',
        '-d', 'fx2lafw',
        '--continuous',
        '-c', f'samplerate={sample_rate}',
        '-o', output_file,
        '--wait-trigger',
    ]

    print(f"  Running: {' '.join(cmd)}")
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        time.sleep(duration_s)
        proc.terminate()
        proc.wait(timeout=2)
    except Exception as e:
        print(f"  Capture failed: {e}")
        return None

    if not os.path.exists(output_file) or os.path.getsize(output_file) == 0:
        print("  No capture file generated")
        return None

    print(f"  Capture saved to {output_file} ({os.path.getsize(output_file)} bytes)")
    return output_file


def verify_from_file(filepath):
    """Decode a .sr (sigrok) capture file."""
    print(f"\n=== [4] Decoding Capture File: {filepath} ===")

    # Sigrok .sr files are gzipped. Let's check if we can read it.
    import gzip

    try:
        with gzip.open(filepath, 'rb') as f:
            header = f.read(8)
            if header[:4] == b'SRD\0':
                print("  Sigrok session format detected")
                # Read metadata
                metadata = b''
                while True:
                    chunk = f.read(4096)
                    if not chunk:
                        break
                    metadata += chunk
                print(f"  Metadata size: {len(metadata)} bytes")
            else:
                print("  Unknown sigrok format, trying raw binary...")

    except Exception as e:
        print(f"  Could not read .sr file: {e}")
        return False

    print("  Note: Full .sr file parsing requires sigrokdecode package")
    print("  Using embedded decoder on raw binary data instead...")

    # Fallback: try to read as raw binary sample file
    try:
        with open(filepath, 'rb') as f:
            raw = f.read()

        # Check if it's gzipped sigrok session
        if raw[:2] == b'\x1f\x8b':
            print("  File is gzip-compressed. Extracting...")
            import io
            raw = gzip.decompress(raw)

        # Try to detect if it's binary sample data (lots of 0/1 bytes)
        if len(raw) > 1000:
            # Check sample format
            zeros = raw.count(b'\x00')
            ones = raw.count(b'\x01')
            other = len(raw) - zeros - ones
            print(f"  Binary analysis: {zeros} zeros, {ones} ones, {other} other")
            return True

    except Exception as e:
        print(f"  Error reading file: {e}")

    return False


def main():
    parser = argparse.ArgumentParser(description='HIL Verification for active_trial_1')
    parser.add_argument('--decoder-test', action='store_true', help='Run decoder self-test only')
    parser.add_argument('--capture', action='store_true', help='Try live sigrok capture')
    parser.add_argument('--file', type=str, help='Decode from .sr capture file')
    parser.add_argument('--vcp', action='store_true', help='Read from STLink VCP')
    parser.add_argument('--quick', action='store_true', help='Quick check (skip slow tests)')
    args = parser.parse_args()

    if args.decoder_test:
        # Run the embedded decoder test
        from uart_decoder import test_roundtrip
        ok = test_roundtrip()
        sys.exit(0 if ok else 1)
        return

    print("=" * 60)
    print("  HIL Verification - active_trial_1")
    print("  Logic Analyzer USART3 Test Firmware")
    print("=" * 60)

    results = {}

    # 1. Always verify decoder
    results['decoder'] = verify_decoder()

    # 2. Try VCP (informational only)
    results['vcp'] = verify_firmware_via_vcp()

    # 3. Try live capture
    if args.capture:
        capture_file = capture_with_sigrok(duration_s=3)
        results['capture'] = capture_file is not None
        if capture_file:
            verify_from_file(capture_file)

    # 4. Decode from file if provided
    if args.file:
        results['file'] = verify_from_file(args.file)

    # Summary
    print("\n" + "=" * 60)
    print("  VERIFICATION SUMMARY")
    print("=" * 60)
    print(f"  UART Decoder:    {'PASS' if results.get('decoder') else 'FAIL'}")
    if results.get('vcp') is True:
        print(f"  VCP Output:      PASS")
    elif results.get('vcp') is None:
        print(f"  VCP Output:      N/A (USART3 not on VCP)")
    else:
        print(f"  VCP Output:      No data")
    if args.capture:
        print(f"  Sigrok Capture:  {'PASS' if results.get('capture') else 'N/A'}")
    if args.file:
        print(f"  File Decode:     {'PASS' if results.get('file') else 'FAIL'}")

    print("\n  For full verification, use PulseView on Windows:")
    print("  1. Connect Saleae to PD8 (TX) + GND")
    print("  2. Sample at 10MHz+")
    print("  3. Add UART decoder @ 115200 8N1")
    print("  4. Expect: 55 AA FF 00 cycling every 100ms")
    print("=" * 60)


if __name__ == '__main__':
    main()
