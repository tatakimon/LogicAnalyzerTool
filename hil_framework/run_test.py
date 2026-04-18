#!/usr/bin/env python3 -u
"""
HIL Framework - Test Runner

Combines capture + decode + validate for end-to-end firmware verification.

Usage:
    python3 run_test.py                           # Quick test
    python3 run_test.py --duration 3              # 3 second capture
    python3 run_test.py --rate 12M --channel 0    # 12MHz, CH0
    python3 run_test.py --baud 115200 --patterns   # Check patterns
    python3 run_test.py --flash firmware.bin      # Flash + verify
"""
import argparse
import sys
import time
import os

# Add hil_framework to path
# hil_framework is relative to the parent project directory

from capture import LogicAnalyzerCapture
from decoder import UARTDecoder
from validator import TestValidator
from hardware import BoardHardware
from timing import analyze_timing

def r(s): return f"\033[31m{s}\033[0m"
def g(s): return f"\033[32m{s}\033[0m"
def y(s): return f"\033[33m{s}\033[0m"


def main():
    parser = argparse.ArgumentParser(description='HIL Test Runner')
    parser.add_argument('--duration', type=float, default=2.0,
                        help='Capture duration in seconds (default: 2.0)')
    parser.add_argument('--rate', type=str, default='12M',
                        help='Sample rate (default: 12M). Options: 1M, 2M, 6M, 8M, 12M, 24M')
    parser.add_argument('--channel', type=int, default=0,
                        help='Probe channel 0-7 (default: 0)')
    parser.add_argument('--baud', type=int, default=115200,
                        help='UART baud rate (default: 115200)')
    parser.add_argument('--patterns', action='store_true',
                        help='Check for firmware test patterns')
    parser.add_argument('--validate', type=str,
                        help='Validation profile: uart_test, sensor_test, etc.')
    parser.add_argument('--flash', type=str,
                        help='Flash firmware .bin before testing')
    parser.add_argument('--device', type=int, default=None,
                        help='Device index (default: first non-demo)')
    parser.add_argument('--quick', action='store_true',
                        help='Skip detailed analysis, just pass/fail')
    args = parser.parse_args()

    print("=" * 60)
    print("  HIL Test Runner")
    print("=" * 60)

    # Step 1: Flash if requested
    if args.flash:
        print(f"\n[1] Flashing: {args.flash}")
        hw = BoardHardware()
        success, msg = hw.flash(args.flash)
        if success:
            print(f"    {msg}")
            time.sleep(1)
            hw.reset()
            print("    Board reset")
        else:
            print(f"    FAIL: {msg}")
            sys.exit(1)
    else:
        print("\n[1] Skip flash (use --flash <file.bin> to flash)")

    # Step 2: Capture
    print(f"\n[2] Logic Analyzer Capture")
    print(f"    Duration: {args.duration}s, Rate: {args.rate}, Channel: D{args.channel}")

    cap = LogicAnalyzerCapture()
    devices = cap.list_devices()

    if not devices:
        print("    ERROR: No devices found")
        sys.exit(1)

    # Pick first non-demo device
    dev_idx = args.device
    if dev_idx is None:
        for i, d in enumerate(devices):
            print(f"    [{i}] {d.name} ({d.driver})")
        for i, d in enumerate(devices):
            if 'demo' not in d.driver.lower():
                dev_idx = i
                break
        if dev_idx is None:
            dev_idx = 0
            print(f"    WARNING: No real device found, falling back to demo")

    dev = devices[dev_idx]
    print(f"    Device: {dev.name} ({dev.driver})")
    print(f"    Max rate: {dev.max_sample_rate_hz / 1e6:.0f}MHz")

    print(f"    Capturing...")
    cap_result = cap.capture(
        duration_s=args.duration,
        sample_rate=args.rate,
        channel=args.channel,
        use_device=dev_idx,
    )

    if not cap_result.success:
        print(f"    ERROR: {cap_result.error}")
        sys.exit(1)

    # Step 3: Analyze channel activity
    print(f"\n[3] Channel Analysis")
    active_channels = []
    for ch, samples in cap_result.channel_samples.items():
        transitions = sum(1 for i in range(1, len(samples)) if samples[i] != samples[i-1])
        active_channels.append((ch, transitions, len(samples)))
        if transitions > 0:
            print(f"    D{ch}: {transitions:,} transitions, {len(samples):,} samples")

    # Step 4: Decode
    print(f"\n[4] UART Decode @ {args.baud} baud")

    decoded_all = {}
    for ch, samples in cap_result.channel_samples.items():
        transitions = sum(1 for i in range(1, len(samples)) if samples[i] != samples[i-1])
        if transitions > 10:
            decoder = UARTDecoder(baud=args.baud)
            decoded = decoder.decode_bytes(samples, cap_result.sample_rate_hz)
            decoded_all[ch] = decoded
            text = decoder.decode_text(samples, cap_result.sample_rate_hz, max_chars=100)
            print(f"    D{ch}: {len(decoded)} bytes decoded")
            if not args.quick:
                print(f"    Text: {text[:80]}")

    if not decoded_all:
        print("    WARNING: No UART frames decoded. Check probe connection and channel.")
        sys.exit(1)

    # Primary channel
    primary = decoded_all.get(args.channel, list(decoded_all.values())[0])
    decoder = UARTDecoder(baud=args.baud)
    text = decoder.decode_text(primary, cap_result.sample_rate_hz, max_chars=200)

    # Step 5: Validate
    print(f"\n[5] Validation")

    validator = TestValidator("USART3 Test Patterns")

    if args.patterns or args.validate == 'uart_test':
        validator.expect_pattern('[0x55]', 'Pattern 0x55')
        validator.expect_pattern('[0xAA]', 'Pattern 0xAA')
        validator.expect_pattern('[0xFF]', 'Pattern 0xFF')
        validator.expect_pattern('[0x00]', 'Pattern 0x00')
        validator.expect_pattern('[CNT]', 'Counter pattern')
        validator.expect_pattern('[ASCII]', 'ASCII pattern')

    if args.validate == 'sensor_test':
        validator.expect_no_zeros('Non-zero sensor data')
        validator.expect_byte_range(10, 200, 'Realistic sensor values')

    # Default: basic checks
    if not args.patterns and not args.validate:
        validator.expect_no_zeros('Contains real data')

    result = validator.validate(text, primary, duration_s=args.duration)
    result.print_report()

    # Summary
    print(f"\n  HIL Result: {'PASS' if result.passed else 'FAIL'}")
    print(f"  Channel: D{args.channel} @ {args.rate} sample rate")
    print(f"  Decoded: {len(primary)} bytes")

    # Step 6: Hardware Timing Analysis
    if cap_result.filepath and os.path.exists(cap_result.filepath):
        print(f"\n[6] Hardware Timing Analysis")
        print(f"    Analyzing: {os.path.basename(cap_result.filepath)}")
        print(f"    Channel: D{args.channel} @ {args.baud} baud")
        faults, timing_report = analyze_timing(
            sr_file=cap_result.filepath,
            channel=args.channel,
            baud=args.baud,
            tolerance=0.05,
            min_gap_us=20.0,
            verbose=False,
        )
        if faults < 0:
            print(f"    {y('WARNING:')} Timing analysis unavailable (sigrok-cli error)")
        elif faults == 0:
            print(f"    {g('✓ PASS:')} 0 physical timing violations found")
        else:
            print(f"    {r('✗ FAIL:')} {faults} physical timing violation(s) found")

    print("=" * 60)

    sys.exit(0 if result.passed else 1)


if __name__ == '__main__':
    main()
