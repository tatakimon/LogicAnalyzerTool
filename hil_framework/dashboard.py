#!/usr/bin/env python3 -u
"""
HIL Framework - Live Visual Dashboard

Real-time visualization of logic analyzer captures and UART signals.
Animated terminal UI with ANSI colors and box-drawing characters.

Usage:
    python3 dashboard.py                    # Live from logic analyzer
    python3 dashboard.py --vcp            # Live from VCP serial
    python3 dashboard.py --duration 3     # 3 second capture
    python3 dashboard.py --channel 1     # Channel 1 (PD8 on your probe)
"""
import argparse
import sys
import os
import time
import serial
import re

# Add parent dir for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    from hil_framework.capture import quick_capture
    from hil_framework.decoder import UARTDecoder
    from hil_framework.validator import TestValidator
    from hil_framework.timing import estimate_baud_from_samples, byte_timing_map, TimingReport
except ImportError:
    from capture import quick_capture
    from decoder import UARTDecoder
    from validator import TestValidator
    from timing import estimate_baud_from_samples, byte_timing_map, TimingReport

# ─── ANSI Escape Codes ───────────────────────────────────────────
RST = '\033[0m'
BOLD = '\033[1m'
DIM = '\033[2m'
GRN = '\033[92m'
YLW = '\033[93m'
BLU = '\033[94m'
MAG = '\033[95m'
CYN = '\033[96m'
WHT = '\033[97m'
RED = '\033[91m'

# Box drawing
TL, H, TR, V, BL, BR = '╔', '═', '╗', '║', '╚', '╝'
MID_H = '╪'
SP = ' '


def c(text, color):
    return color + text + RST


def bold(text):
    return BOLD + text + RST


def dim(text):
    return DIM + text + RST


def green(text):
    return GRN + text + RST


def yellow(text):
    return YLW + text + RST


def blue(text):
    return BLU + text + RST


def cyan(text):
    return CYN + text + RST


def magenta(text):
    return MAG + text + RST


def red(text):
    return RED + text + RST


def clear():
    print('\033[2J\033[H', end='', flush=True)


def pos(line, col=1):
    print(f'\033[{line};{col}H', end='', flush=True)


def box_line(content, width=80):
    """Draw a full-width box line with V borders."""
    padded = content.ljust(width - 2)
    return V + ' ' + padded + ' ' + V


def box_bar(width=80):
    """Draw a horizontal bar."""
    return TL + H * (width - 2) + TR


def box_sep(width=80):
    """Draw a separator."""
    return V + H * (width - 2) + V


def box_bottom(width=80):
    return BL + H * (width - 2) + BR


# ─── Byte Visualization ────────────────────────────────────────────
def waveform_bar(bval, width=16):
    """ASCII waveform for a byte: start + 8 data + stop bits."""
    bits = [(bval >> i) & 1 for i in range(8)]
    # Use block chars for nice visualization
    lo = '_'
    hi = '▄'
    s = lo + ''.join(hi if b else lo for b in bits) + hi
    return s[:width].ljust(width)


def waveform_colored(bval, timing_info=None, col_fn=None):
    """
    Build waveform string with per-character ANSI coloring.
    timing_info: ByteTiming object (or None)
    col_fn: base color function to apply to healthy bits
    If timing_info has faults, the relevant bits turn red.
    Returns: tuple (waveform_str, has_fault)
    """
    bits = [(bval >> i) & 1 for i in range(8)]
    lo_char = '_'
    hi_char = '▄'

    if timing_info is not None and timing_info.fault_count > 0:
        has_fault = True
        waveform = RED + lo_char  # start bit red
        for b in bits:
            waveform += RED + (hi_char if b else lo_char)
        waveform += RED + lo_char + RST  # stop bit red
    else:
        has_fault = False
        base = col_fn(lo_char) if col_fn else lo_char
        waveform = base
        for b in bits:
            ch = col_fn(hi_char) if col_fn else hi_char
            waveform += ch
        waveform += base
    return waveform, has_fault


def byte_color(bval):
    """Color function based on byte category."""
    if bval == 0x55:
        return green
    elif bval == 0xAA:
        return yellow
    elif bval == 0xFF:
        return red
    elif bval == 0x00:
        return dim
    elif 32 <= bval < 127:
        return cyan
    else:
        return magenta


def fmt_hex(bval):
    return f"0x{bval:02X}"


def fmt_bin(bval):
    return f"{bval:08b}"


def fmt_chr(bval):
    if 32 <= bval < 127:
        return f"'{chr(bval)}'"
    elif bval == 0:
        return "'.'"
    else:
        return "'?'"


# ─── Parse Lines ──────────────────────────────────────────────────
def parse_decoded_line(text):
    """Parse a decoded pattern line like '[0x55] 55 01010101 ...'"""
    text = text.strip()
    if not text:
        return None
    # Extract hex value after pattern marker
    m = re.search(r'\[(0x\d+|CNT|ASCII)\](?:\s+(\S{2}))?', text)
    if m:
        tag = m.group(1)
        hex_val = m.group(2)
        if hex_val:
            try:
                return int(hex_val, 16)
            except ValueError:
                pass
        if tag == 'CNT' or tag == 'ASCII':
            return None  # These are handled differently
    return None


# ─── Histogram ────────────────────────────────────────────────────
def histogram(bytes_data, max_bars=24):
    """Build a simple ASCII histogram."""
    buckets = [0] * 16  # 16 buckets of 16 values each
    for b in bytes_data:
        buckets[b // 16] += 1
    max_count = max(buckets) if buckets else 1
    rows = []
    for i, count in enumerate(buckets):
        bar_len = int(count / max_count * max_bars) if max_count > 0 else 0
        bar = green('█' * bar_len) if count > 0 else ''
        rows.append((f"0x{i*16:02X}-0x{i*16+15:02X}", bar, count))
    return rows


# ─── Dashboard Draw Functions ──────────────────────────────────────
def draw(width=80):
    """Draw the static parts of the dashboard frame."""
    clear()

    # Title
    print(box_bar(width))
    title = ' LOGIC ANALYZER - LIVE DASHBOARD '
    print(V + '  ' + bold(magenta(title.center(width - 4))))  # pad to width-4
    print(V + H * (width - 2) + V)
    print(box_line(f"  {cyan('Saleae Logic')} @ 12MHz   |   {cyan('115200 8N1')}   |   {cyan('CH1 = PD8 (USART3 TX)')}", width))
    print(V + H * 32 + MID_H + H * 46 + V)


def draw_patterns(bytes_data, timing_map=None, baud_mismatch_pct=0.0, width=80):
    """Draw the recent decoded patterns with timing awareness."""
    print(V + H * 32 + MID_H + H * 46 + V)
    header = '  RECENTLY DECODED PATTERNS '
    if timing_map:
        warn_count = sum(1 for bt in timing_map if bt.fault_count > 0)
        if warn_count > 0:
            header += '  ' + red(f'[{warn_count} TIMING WARN]')
    if abs(baud_mismatch_pct) > 2.0:
        header += '  ' + red('[BAUD MISMATCH]')
    print(box_line(bold(header), width))

    step = len(bytes_data)
    for b in reversed(bytes_data[-30:]):
        step -= 1
        col_fn = byte_color(b)

        # Get timing info for this byte if available
        has_fault = False
        if timing_map:
            byte_idx = len(bytes_data) - 1 - step
            timing_info = timing_map[byte_idx] if byte_idx < len(timing_map) else None
            wf_plain, has_fault = waveform_colored(b, timing_info=timing_info, col_fn=col_fn)
        else:
            wf_plain = col_fn(waveform_bar(b, 14))

        pat = col_fn(f"[0x{b:02X}]")
        hx = col_fn(fmt_hex(b)).ljust(6)
        bn = col_fn(fmt_bin(b)).ljust(10)
        ch = col_fn(fmt_chr(b)).ljust(4)

        # Step counter coloring
        st = f"#{step:04d}"
        if step < 100:
            st = green(st)
        elif step < 500:
            st = yellow(st)
        else:
            st = cyan(st)

        # Timing warning badge
        warn_str = red(' [TIMING WARN]') if has_fault else ''

        inner = f"  {pat}  {hx}  {bn}  {ch}  {wf_plain}  {st}{warn_str}  "
        print(box_line(inner, width))

    print(V + H * 32 + MID_H + H * 46 + V)


def draw_histogram(bytes_data, width=80):
    """Draw byte histogram."""
    print(box_bar(width))
    print(box_line(bold('  BYTE DISTRIBUTION'), width))
    print(box_line('', width))

    rows = histogram(bytes_data)
    for label, bar, count in rows:
        if count > 0:
            bar_colored = green(bar[:24])
            line = f"  {label}  |  {bar_colored}  ({count})"
            print(box_line(line, width))

    print(box_bottom(width))


def draw_footer(width=80, status='', total_bytes=0, elapsed=0.0, timing_map=None, baud_mismatch_pct=0.0, implied_baud=115200):
    """Draw footer with status and timing info."""
    print(box_bar(width))
    elapsed_str = f"{elapsed:.1f}s"
    footer = f"  {green('● LIVE')}  |  {cyan(f'Decoded: {total_bytes} bytes')}  |  {yellow(f'{elapsed_str}')}"
    print(box_line(footer, width))

    if timing_map or abs(baud_mismatch_pct) > 2.0:
        warn_count = sum(1 for bt in timing_map if bt.fault_count > 0) if timing_map else 0
        if warn_count > 0:
            timing_line = f"  {red('● TIMING WARN')}: {warn_count}/{len(timing_map)} bytes out of tolerance"
        elif abs(baud_mismatch_pct) > 2.0:
            timing_line = f"  {red('● BAUD MISMATCH')}: ~{int(implied_baud)} baud vs 115200 declared"
        else:
            timing_line = f"  {green('● TIMING OK')}: all {len(timing_map)} bytes within 5% tolerance"
        print(box_line(timing_line, width))

    print(box_bottom(width))


# ─── Main: Logic Analyzer Mode ────────────────────────────────────
def main_la(duration=3.0, channel=1, width=80):
    """Capture from logic analyzer and display."""
    print(f"\n  {cyan('Starting logic analyzer capture...')}")
    print(f"  {cyan('Duration:')} {duration}s  {cyan('Channel:')} D{channel}  {cyan('Rate:')} 12MHz\n")

    t0 = time.time()
    result = quick_capture(duration_s=duration, sample_rate='12M', channel=channel, baud=115200)
    elapsed = time.time() - t0

    if not result['success']:
        print(f"\n  {red('ERROR:')} Capture failed: {result.get('error', 'unknown')}\n")
        return

    decoded = result['raw_bytes']
    text = result['text']

    # ── Get timing map and baud mismatch detection ─────────────────
    timing_map = []
    baud_mismatch_pct = 0.0
    implied_baud = 115200
    if result.get('channel_samples') is not None:
        # Use sample-based baud estimation (no VCD needed)
        implied_baud, baud_mismatch_pct = estimate_baud_from_samples(
            result['channel_samples'],
            result.get('sample_rate_hz', 12_000_000),
            declared_baud=115200,
        )
        if result.get('sr_filepath'):
            timing_map = byte_timing_map(
                sr_file=result['sr_filepath'],
                channel=channel,
                decoded_bytes=decoded,
                channel_samples=result['channel_samples'],
                sample_rate_hz=result.get('sample_rate_hz', 12_000_000),
                baud=115200,
                tolerance=0.05,
            )

    # ── Draw Dashboard ─────────────────────────────────────────
    clear()
    print(box_bar(width))
    print(V + '  ' + bold(magenta('LOGIC ANALYZER - LIVE DASHBOARD'.center(width - 4))))
    print(V + H * (width - 2) + V)
    print(box_line(f"  {cyan('Saleae Logic')} @ 12MHz   |   {cyan('115200 8N1')}   |   {cyan(f'CH{channel} = PD8 (USART3 TX)')}", width))

    # Timing summary row
    if timing_map or abs(baud_mismatch_pct) > 2.0:
        warn_count = sum(1 for bt in timing_map if bt.fault_count > 0) if timing_map else 0
        if warn_count > 0:
            timing_row = f"  {red('● TIMING ISSUES')}: {warn_count}/{len(timing_map)} bytes faulted"
        elif abs(baud_mismatch_pct) > 2.0:
            timing_row = f"  {red('● BAUD MISMATCH')}: signal ~{int(implied_baud)} baud vs 115200 declared"
        else:
            timing_row = f"  {green('● TIMING OK')}: {len(timing_map)}/{len(timing_map)} bytes healthy"
        print(box_line(timing_row, width))
    print(box_bar(width))

    draw_patterns(decoded, timing_map=timing_map, baud_mismatch_pct=baud_mismatch_pct, width=width)
    draw_histogram(decoded, width)
    draw_footer(width, status='LIVE', total_bytes=len(decoded), elapsed=elapsed, timing_map=timing_map, baud_mismatch_pct=baud_mismatch_pct, implied_baud=implied_baud)

    # ── Validate ────────────────────────────────────────────────
    print()
    validator = TestValidator('USART3 Patterns')
    for p in ['[0x55]', '[0xAA]', '[0xFF]', '[0x00]', '[CNT]', '[ASCII]']:
        validator.expect_pattern(p)
    test_result = validator.validate(text, decoded)
    passed = sum(1 for v in test_result.validations if v.passed)
    total = len(test_result.validations)

    # Print validation results
    print(box_bar(width))
    print(box_line(bold('  VALIDATION RESULTS'), width))
    for v in test_result.validations:
        mark = green('✓') if v.passed else red('✗')
        status = green('PASS') if v.passed else red('FAIL')
        print(box_line(f"  {mark}  {status}  {v.name}", width))

    print(box_bottom(width))
    print()
    print(f"  {green('HIL RESULT:')} {green(f'{passed}/{total} PASSED')}")
    print(f"  {cyan('Bytes decoded:')} {len(decoded)}")
    if decoded:
        print(f"  {cyan('First bytes:')}  " + '  '.join(f"0x{b:02X}" for b in decoded[:10]))
    print()


# ─── Main: VCP Live Mode ────────────────────────────────────────
def main_vcp(port='/dev/ttyACM0', baud=115200, duration=10.0, width=80):
    """Live display reading from VCP."""
    try:
        ser = serial.Serial(port, baud, timeout=0.5)
    except Exception as e:
        print(f"\n  {red('ERROR:')} Cannot open {port}: {e}\n")
        return

    print(f"\n  {cyan('Connecting to VCP:')} {port} @ {baud} baud")
    print(f"  {dim('Press Ctrl+C to stop...')}\n")
    time.sleep(0.5)
    ser.reset_input_buffer()

    all_bytes = []
    all_text = []
    all_lines = []
    start = time.time()

    try:
        while time.time() - start < duration:
            if ser.in_waiting > 0:
                raw = ser.read(ser.in_waiting)
                try:
                    text = raw.decode('utf-8', errors='replace')
                except:
                    text = ''

                for line in text.split('\n'):
                    line = line.strip()
                    if line:
                        all_lines.append(line)
                        # Try to extract hex byte from VCP line
                        m = re.search(r'\[(0x\d+)\](?:\s+(\S{2}))?', line)
                        if m:
                            try:
                                b = int(m.group(2), 16)
                                all_bytes.append(b)
                            except (ValueError, TypeError):
                                pass
                        elif '[CNT]' in line:
                            m2 = re.search(r'\[CNT\]\s+(\S+)', line)
                            if m2:
                                try:
                                    b = int(m2.group(1), 16)
                                    all_bytes.append(b)
                                except ValueError:
                                    pass

            # Redraw every 0.3s
            if len(all_text) != len(all_lines):
                all_text = list(all_lines)
                elapsed = time.time() - start
                clear()

                print(box_bar(width))
                print(V + '  ' + bold(magenta('VCP LIVE MONITOR'.center(width - 4))))
                print(V + H * (width - 2) + V)
                print(box_line(f"  {green('● LIVE')}   {cyan(port)} @ {baud}   |   {yellow(f'{elapsed:.1f}s elapsed')}", width))
                print(box_bar(width))
                draw_patterns(all_bytes, width)
                draw_footer(width, status='LIVE', total_bytes=len(all_bytes), elapsed=elapsed)

            time.sleep(0.1)

    except KeyboardInterrupt:
        print()
    finally:
        ser.close()

    print(f"\n  {green('Done:')} {len(all_bytes)} bytes decoded in {time.time() - start:.1f}s\n")


# ─── Entry Point ────────────────────────────────────────────────
if __name__ == '__main__':
    p = argparse.ArgumentParser(description='HIL Visual Dashboard')
    p.add_argument('--vcp', action='store_true', help='Live from VCP serial port')
    p.add_argument('--duration', type=float, default=3.0, help='Capture duration (default: 3.0s)')
    p.add_argument('--channel', type=int, default=1, help='Logic analyzer channel (default: 1)')
    p.add_argument('--width', type=int, default=80, help='Terminal width (default: 80)')
    p.add_argument('--port', type=str, default='/dev/ttyACM0', help='VCP serial port')
    args = p.parse_args()

    if args.vcp:
        main_vcp(port=args.port, baud=115200, duration=args.duration, width=args.width)
    else:
        main_la(duration=args.duration, channel=args.channel, width=args.width)
