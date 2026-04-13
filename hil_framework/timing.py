#!/usr/bin/env python3 -u
"""
HIL Framework - Hardware Timing Analysis

Parses VCD (Value Change Dump) from sigrok-cli captures to verify
physical-layer UART timing. Flags pulses that deviate from the ideal
bit width by more than a configurable threshold.

Target: 115200 baud 8N1
  - Ideal bit width = 1/115200 = 8.6806 us
  - Default tolerance = 5% → range [8.247, 9.114] us

Usage:
    python3 timing.py capture.sr                  # Analyze channel D0
    python3 timing.py capture.sr --channel D1      # Analyze channel D1
    python3 timing.py capture.sr --tolerance 0.10 # 10% tolerance
    python3 timing.py capture.sr --min-gap 20      # Ignore gaps >20us

Integration (called from run_test.py):
    from timing import analyze_timing
    faults, report = analyze_timing(sr_file, channel=1, baud=115200)
"""
import argparse
import re
import subprocess
import sys
import os
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

# ─── ANSI helpers ──────────────────────────────────────────────────
RST = '\033[0m'
BOLD = '\033[1m'
DIM = '\033[2m'
GRN = '\033[92m'
YLW = '\033[93m'
RED = '\033[91m'
CYN = '\033[96m'
MAG = '\033[95m'


def g(text): return GRN + text + RST
def y(text): return YLW + text + RST
def r(text): return RED + text + RST
def c(text): return CYN + text + RST
def m(text): return MAG + text + RST
def d(text): return DIM + text + RST
def b(text): return BOLD + text + RST


# ─── Timing Constants ───────────────────────────────────────────────
UART_BAUD = 115200
IDEA_US = 1_000_000.0 / UART_BAUD   # 8.6806 us
DEFAULT_TOLERANCE = 0.05            # 5%
DEFAULT_MIN_GAP = 20.0              # us — ignore gaps larger than this (inter-packet)


@dataclass
class Edge:
    """A single edge (state change) in the signal."""
    timestamp_us: float   # microseconds
    value: int            # 0 or 1


@dataclass
class Pulse:
    """A complete pulse: high + low duration in microseconds."""
    high_us: float
    low_us: float
    total_us: float       # high + low
    faults: List[str] = field(default_factory=list)
    is_idle: bool = False


@dataclass
class ByteTiming:
    """Timing info for a single decoded byte."""
    byte_index: int       # position in the decoded stream
    fault_count: int      # how many timing faults fall within this byte
    fault_pct: float      # worst deviation as % of ideal bit
    faulty_bits: List[int] = field(default_factory=list)  # which bit positions are bad


@dataclass
class TimingReport:
    """Full timing analysis result."""
    channel: str
    baud: int
    ideal_us: float
    tolerance: float
    total_edges: int
    total_pulses: int
    fault_count: int
    idle_count: int
    min_us: float
    max_us: float
    mean_us: float
    std_us: float
    faults: List[Tuple[int, str, float]] = field(default_factory=list)
    pulses: List[Pulse] = field(default_factory=list)
    edges: List[Edge] = field(default_factory=list)
    baud_mismatch_pct: float = 0.0  # how much actual baud deviates from declared


# ─── VCD Parser ────────────────────────────────────────────────────
class VCDEdgeParser:
    """
    Parse sigrok-cli VCD output into a list of edges.

    VCD format:
        $timescale 100 ps $end        ← 1 unit = 100 picoseconds
        $var wire 1 ! D0 $end         ← ! maps to D0
        ...
        #0 1! 1" ...                 ← time=0, set D0=1, D1=1, ...
        #335479167 0"                 ← time=33.5479ms, set D1=0
    """

    def __init__(self, timescale_ps: int = 100):
        self.timescale_ps = timescale_ps
        self.timescale_us = timescale_ps / 1_000_000.0
        self.channel_map = {}   # sig_char -> channel name (e.g. '"' -> 'D1')
        self.current_values = {}  # channel -> 0/1
        self.edges = []          # list of (timestamp_us, value, channel)
        self._in_header = True

    def parse_vcd_text(self, vcd_text: str, target_channel: Optional[str] = None) -> List[Edge]:
        """
        Parse VCD text and return edges for the target channel.

        Args:
            vcd_text: Raw VCD output from sigrok-cli
            target_channel: Channel name to extract (e.g. 'D1'). If None, returns all.

        Returns:
            List of Edge objects sorted by timestamp.
        """
        edges = []

        for line in vcd_text.split('\n'):
            line = line.strip()
            if not line:
                continue

            if line.startswith('$timescale'):
                m_ts = re.search(r'\$timescale\s+(\d+)\s+(\w+)\s+\$end', line)
                if m_ts:
                    val, unit = int(m_ts.group(1)), m_ts.group(2)
                    unit_map = {'ps': 1, 'ns': 1000, 'us': 1_000_000, 'ms': 1_000_000_000, 's': 1_000_000_000_000}
                    self.timescale_ps = val * unit_map.get(unit, 100)
                    self.timescale_us = self.timescale_ps / 1_000_000.0

            elif line.startswith('$var wire'):
                # $var wire 1 ! D0 $end
                m_var = re.match(r'\$var wire\s+(\d+)\s+(\S+)\s+(\S+)\s+\$end', line)
                if m_var:
                    width, sig_char, channel_name = m_var.group(1), m_var.group(2), m_var.group(3)
                    self.channel_map[sig_char] = channel_name

            elif line.startswith('$enddefinitions'):
                self._in_header = False

            elif line.startswith('#') and not self._in_header:
                # Transition line: #<timestamp> <value><sig_char> ...
                parts = line.split()
                if not parts:
                    continue
                ts_str = parts[0][1:]  # strip leading '#'
                timestamp_us = int(ts_str) * self.timescale_us

                for part in parts[1:]:
                    if not part:
                        continue
                    # Value is first char, sig_char is the rest
                    value_char = part[0]
                    sig_char = part[1:]
                    if sig_char not in self.channel_map:
                        continue

                    channel = self.channel_map[sig_char]
                    value = 1 if value_char == '1' else 0

                    # Filter to target channel
                    if target_channel is not None and channel != target_channel:
                        continue

                    # Only record edges (changes)
                    prev = self.current_values.get(channel)
                    if prev is None or prev != value:
                        self.current_values[channel] = value
                        edges.append(Edge(timestamp_us=timestamp_us, value=value))

        return edges

    def get_edges(self) -> List[Edge]:
        """Return all parsed edges regardless of channel filter."""
        return list(self.edges)


def export_vcd(sr_file: str, channel: Optional[int] = None) -> str:
    """
    Run sigrok-cli to export a .sr capture file as VCD.

    Args:
        sr_file: Path to .sr capture file
        channel: Channel number (0-7). If provided, targets that channel.
                 Exports all channels so the parser can filter by name.

    Returns:
        VCD text output from sigrok-cli
    """
    cmd = ['sigrok-cli', '-i', sr_file, '-O', 'vcd']

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(f"sigrok-cli VCD export failed: {result.stderr.strip()}")

    return result.stdout


# ─── Timing Analysis ────────────────────────────────────────────────
def analyze_pulses(
    edges: List[Edge],
    ideal_us: float,
    tolerance: float,
    min_gap_us: float,
    baud: int = UART_BAUD,
) -> TimingReport:
    """
    Analyze edge-to-edge transitions for timing faults.

    UART at 115200 baud: ideal bit period = 8.6806 µs.
    Each half-period (edge-to-edge) should be a multiple of the bit period.
    Consecutive same-state bits collapse into one longer interval:
      - 0xFF (11111111): stop bit + 8 data 1s + stop bit = ~86.8 µs (10× ideal)
      - 0x00 (00000000): start bit + 8 data 0s = ~78.1 µs (9× ideal)
      - 0xAA (10101010): alternating = ~8.68 µs per edge

    Thresholds:
      - Bit period range: [ideal*(1-tol), ideal*(1+tol)]
      - Consecutive bits: anything up to a full byte period (~87 µs)
      - Idle gap: anything significantly beyond a byte period (>4× ideal bit)
    """
    if len(edges) < 2:
        return TimingReport(
            channel='D0', baud=115200, ideal_us=ideal_us, tolerance=tolerance,
            total_edges=len(edges), total_pulses=0, fault_count=0, idle_count=0,
            min_us=0, max_us=0, mean_us=0, std_us=0,
        )

    half_periods: List[float] = []
    faults: List[Tuple[int, str, float]] = []
    idle_count = 0

    # Thresholds
    lo = ideal_us * (1 - tolerance)        # lower bound: e.g. 8.25 µs
    hi = ideal_us * (1 + tolerance)        # upper bound for 1× bit: e.g. 9.11 µs
    byte_time = ideal_us * 10              # full byte = 86.8 µs
    gap_threshold = ideal_us * 1.5          # ~13 µs — separates 1-bit from multi-bit

    for i in range(len(edges) - 1):
        e1, e2 = edges[i], edges[i + 1]
        dt_us = e2.timestamp_us - e1.timestamp_us

        # Classify: idle gap (> byte period), consecutive bits, or single bit
        if dt_us > byte_time:
            # Inter-packet idle gap — valid, not a fault
            idle_count += 1
            continue

        if dt_us > gap_threshold:
            # Consecutive same-state bits within a byte (e.g. 2×, 3×, ..., 9×)
            # The interval should be N × ideal_bit ± tolerance, where N is the nearest integer
            num_bits = dt_us / ideal_us
            nearest = round(num_bits)
            deviation = abs(dt_us - nearest * ideal_us)  # µs error from nearest N-bit multiple
            # Fault if deviation > 5% of ideal bit width
            if deviation > tolerance * ideal_us:
                faults.append((i, dt_us, deviation))
            half_periods.append(dt_us)
        else:
            # Single bit period — check against ideal ± tolerance
            if not (lo <= dt_us <= hi):
                faults.append((i, dt_us, abs(dt_us - ideal_us)))
            half_periods.append(dt_us)

    if not half_periods:
        return TimingReport(
            channel='D0', baud=115200, ideal_us=ideal_us, tolerance=tolerance,
            total_edges=len(edges), total_pulses=0, fault_count=0,
            idle_count=idle_count,
            min_us=0, max_us=0, mean_us=0, std_us=0,
        )

    # Statistics
    mean_us = sum(half_periods) / len(half_periods)
    variance = sum((x - mean_us) ** 2 for x in half_periods) / len(half_periods)
    std_us = variance ** 0.5

    # Build pulse objects for visualization
    pulses: List[Pulse] = []
    for i in range(0, len(edges) - 1, 2):
        if i + 1 >= len(edges):
            break
        e1, e2 = edges[i], edges[i + 1]
        dt = e2.timestamp_us - e1.timestamp_us
        if dt > byte_time:
            continue
        high_us = dt if e1.value == 1 else 0.0
        low_us = dt if e1.value == 0 else 0.0
        pulse = Pulse(high_us=high_us, low_us=low_us, total_us=dt)

        if dt > gap_threshold:
            num_bits = dt / ideal_us
            nearest = round(num_bits)
            deviation = abs(dt - nearest * ideal_us)
            if deviation > tolerance * ideal_us:
                deviation_pct = deviation / ideal_us * 100
                pulse.faults.append(f"interval={dt:.2f}us ({nearest} bits, {deviation_pct:+.1f}% off)")
        elif not (lo <= dt <= hi):
            deviation_pct = abs(dt - ideal_us) / ideal_us * 100
            pulse.faults.append(f"half-period={dt:.2f}us ({deviation_pct:+.1f}% off ideal)")

        pulses.append(pulse)

    # Check if observed signal matches the declared baud rate.
    # Focus on SINGLE-bit intervals only (dt <= gap_threshold).
    # Multi-bit intervals compose N×bit-width and distort the median.
    if len(half_periods) >= 5:
        single_bit_intervals = [dt for dt in half_periods if dt <= gap_threshold]
        if len(single_bit_intervals) >= 3:
            sorted_sbi = sorted(single_bit_intervals)
            median_sbi = sorted_sbi[len(sorted_sbi) // 2]
            implied_baud = 1_000_000.0 / median_sbi
            baud_dev_pct = (implied_baud - baud) / baud * 100
            # Flag if baud rate mismatch exceeds 2% (detects 6.9% bug reliably)
            if abs(baud_dev_pct) > 2.0:
                # Deduce the actual baud and recompute thresholds
                actual_baud = int(round(implied_baud / 100) * 100)
                actual_ideal = 1_000_000.0 / actual_baud
                actual_lo = actual_ideal * (1 - tolerance)
                actual_hi = actual_ideal * (1 + tolerance)
                actual_gap = actual_ideal * 1.5
                actual_byte = actual_ideal * 10

                # Re-check all intervals against actual baud
                for dt in half_periods:
                    if dt > actual_byte:
                        continue
                    if dt > actual_gap:
                        nb = dt / actual_ideal
                        nearest = round(nb)
                        dev = abs(dt - nearest * actual_ideal)
                        if dev > tolerance * actual_ideal:
                            faults.append((0, dt, dev))
                    elif not (actual_lo <= dt <= actual_hi):
                        faults.append((0, dt, abs(dt - actual_ideal)))

                # Deduplicate using actual baud
                unique_faults = []
                seen = set()
                for idx, dt_us, deviation_us in faults:
                    num_bits = dt_us / actual_ideal
                    nearest = round(num_bits)
                    key = f"{nearest}:{dt_us:.1f}"
                    if key not in seen:
                        seen.add(key)
                        deviation_pct = deviation_us / actual_ideal * 100
                        unique_faults.append((idx, f"{dt_us:.2f}us ({nearest} bits, {deviation_us:+.4f}us off ideal, {deviation_pct:+.1f}%)", dt_us))
            else:
                baud_dev_pct = 0.0
                # No baud mismatch — use original deduplication
                unique_faults = []
                seen = set()
                for idx, dt_us, deviation_us in faults:
                    num_bits = dt_us / ideal_us
                    nearest = round(num_bits)
                    key = f"{nearest}:{dt_us:.1f}"
                    if key not in seen:
                        seen.add(key)
                        deviation_pct = deviation_us / ideal_us * 100
                        unique_faults.append((idx, f"{dt_us:.2f}us ({nearest} bits, {deviation_us:+.4f}us off ideal, {deviation_pct:+.1f}%)", dt_us))
        else:
            baud_dev_pct = 0.0
            unique_faults = []
            seen = set()
            for idx, dt_us, deviation_us in faults:
                num_bits = dt_us / ideal_us
                nearest = round(num_bits)
                key = f"{nearest}:{dt_us:.1f}"
                if key not in seen:
                    seen.add(key)
                    deviation_pct = deviation_us / ideal_us * 100
                    unique_faults.append((idx, f"{dt_us:.2f}us ({nearest} bits, {deviation_us:+.4f}us off ideal, {deviation_pct:+.1f}%)", dt_us))
    else:
        baud_dev_pct = 0.0
        unique_faults = []
        seen = set()
        for idx, dt_us, deviation_us in faults:
            num_bits = dt_us / ideal_us
            nearest = round(num_bits)
            key = f"{nearest}:{dt_us:.1f}"
            if key not in seen:
                seen.add(key)
                deviation_pct = deviation_us / ideal_us * 100
                unique_faults.append((idx, f"{dt_us:.2f}us ({nearest} bits, {deviation_us:+.4f}us off ideal, {deviation_pct:+.1f}%)", dt_us))

    return TimingReport(
        channel='D0',
        baud=int(1_000_000 / ideal_us),
        ideal_us=ideal_us,
        tolerance=tolerance,
        total_edges=len(edges),
        total_pulses=len(pulses),
        fault_count=len(unique_faults),
        idle_count=idle_count,
        min_us=min(half_periods),
        max_us=max(half_periods),
        mean_us=mean_us,
        std_us=std_us,
        faults=unique_faults,
        pulses=pulses,
        baud_mismatch_pct=baud_dev_pct,
    )


# ─── Visualization ─────────────────────────────────────────────────
def render_timing_sparkline(
    pulses: List[Pulse],
    max_pulses: int = 60,
    width: int = 50,
    ideal_us: float = IDEA_US,
    tolerance: float = DEFAULT_TOLERANCE,
) -> List[str]:
    """
    Render an ASCII sparkline of pulse widths over time.
    Each character represents one edge-to-edge interval, colored by deviation.
    Multi-bit intervals (consecutive same-state bits) are shown as bold repeated chars.
    """
    gap_threshold = ideal_us * 1.5
    lines = []
    line = ""
    count = 0

    for pulse in pulses[:max_pulses]:
        total = pulse.total_us
        deviation = (total - ideal_us) / ideal_us

        # Determine number of visual blocks (chars) for this interval
        num_bits = round(total / ideal_us)
        num_chars = max(1, num_bits)

        if pulse.faults:
            char = r('X')
        elif num_bits > 1:
            # Multi-bit interval — show as a bold repeat
            char = m('═') if not pulse.faults else r('═')
        elif abs(deviation) < 0.02:
            char = g('│')
        elif abs(deviation) < 0.05:
            char = y('│')
        else:
            char = c('│')

        for _ in range(num_chars):
            line += char
            count += 1
            if count >= width:
                lines.append(f"  {d('[')}{line}{d(']')}")
                line = ""
                count = 0

    if line:
        lines.append(f"  {d('[')}{line}{d(']' + ' ' * (width - count))}")

    return lines


def render_timing_histogram(pulses: List[Pulse], bins: int = 30) -> List[Tuple[str, int, str]]:
    """
    Build a histogram of pulse widths bucketed by bit count.
    Each bucket = approximate number of consecutive bit periods in that interval.

    Returns:
        List of (label, count, bar_str) rows, sorted by bit count.
    """
    if not pulses:
        return []

    all_us = [p.total_us for p in pulses]
    # Bucket by approximate number of bit periods
    buckets = {}  # bit_count (int) -> count
    for total in all_us:
        num_bits = round(total / IDEA_US)
        buckets[num_bits] = buckets.get(num_bits, 0) + 1

    if not buckets:
        return []

    max_count = max(buckets.values())
    rows = []

    for bit_count in sorted(buckets.keys()):
        count = buckets[bit_count]
        total_us = bit_count * IDEA_US
        bar_len = int(count / max_count * 35)
        bar = '█' * bar_len

        # Color: green = 1 bit (ideal), magenta = multi-bit (consecutive bits), yellow = large gap
        if bit_count == 1:
            bar_colored = g(bar)
            label = f"1-bit ({total_us:.2f}us)"
        elif bit_count <= 10:
            bar_colored = m(bar)
            label = f"{bit_count}-bit ({total_us:.2f}us)"
        else:
            bar_colored = y(bar)
            label = f"{bit_count}-bit ({total_us:.2f}us)"

        rows.append((label, count, bar_colored))

    return rows


def render_timing_ruler(ideal_us: float, tolerance: float, width: int = 50) -> str:
    """Draw a timing ruler with ideal, min, max markers."""
    lo = ideal_us * (1 - tolerance)
    hi = ideal_us * (1 + tolerance)
    line = "  " + "─" * width

    # Markers
    marks = ""
    for pct in [0, 25, 50, 75, 100]:
        pos = int(width * pct / 100)
        marks += f"  {c('|')}"

    ruler = f"  {d('0%')}{' ' * 17}{d('50%')}{' ' * 17}{d('100%')}\n"
    ruler += f"  {m('▼')}{d('─────')}{m('▼')}{d('─────')}{m('▼')}  "
    ruler += f"  {r('✗')}{d(' ')}{b('IDEAL')}{d(' ')}{r('✗')}\n"
    ruler += f"  {r(f'{lo:.2f}us')}{' ' * (width - 20)}{g(f'{IDEA_US:.2f}us')}{' ' * (width - 20)}{r(f'{hi:.2f}us')}"
    return ruler


# ─── Main Analysis ─────────────────────────────────────────────────
def analyze_timing(
    sr_file: str,
    channel: int = 0,
    baud: int = UART_BAUD,
    tolerance: float = DEFAULT_TOLERANCE,
    min_gap_us: float = DEFAULT_MIN_GAP,
    verbose: bool = True,
) -> Tuple[int, Optional[TimingReport]]:
    """
    Full timing analysis pipeline.

    Args:
        sr_file: Path to .sr capture file
        channel: Probe channel number (0=D0, 1=D1, ...)
        baud: UART baud rate
        tolerance: Acceptable deviation from ideal bit width (0.05 = 5%)
        min_gap_us: Ignore gaps larger than this (inter-packet silence)
        verbose: Print the visual report

    Returns:
        Tuple of (fault_count, TimingReport)
    """
    if not os.path.exists(sr_file):
        return -1, None

    channel_name = f'D{channel}'
    ideal_us = 1_000_000.0 / baud

    if verbose:
        print()
        print(f"  {b('╔' + '═' * 78 + '╗')}")
        print(f"  {b('║')}  {b(m('HARDWARE TIMING ANALYSIS'))}  "
              f"{' ' * 43}{b('║')}")
        print(f"  {b('╠' + '═' * 78 + '╣')}")
        print(f"  {b('║')}  {c('File:')} {d(sr_file)}")
        print(f"  {b('║')}  {c('Channel:')} {channel_name}    "
              f"{c('Baud:')} {baud}    "
              f"{c('Ideal bit:')} {ideal_us:.4f} us")
        print(f"  {b('║')}  {c('Tolerance:')} {tolerance * 100:.1f}%   "
              f"{c('Min gap filter:')} {min_gap_us} us")
        print(f"  {b('╠' + '═' * 78 + '╣')}")

    # ── Step 1: Export VCD ──────────────────────────────────────
    if verbose:
        print(f"  {b('║')}  {d('Exporting VCD from capture file...')}")

    try:
        vcd_text = export_vcd(sr_file, channel=channel)
    except RuntimeError as e:
        if verbose:
            print(f"  {b('║')}  {r(f'ERROR: {e}')}")
            print(f"  {b('╚' + '═' * 78 + '╝')}")
        return -1, None

    # ── Step 2: Parse edges ─────────────────────────────────────
    if verbose:
        print(f"  {b('║')}  {d('Parsing edges...')}")

    parser = VCDEdgeParser()
    edges = parser.parse_vcd_text(vcd_text, target_channel=channel_name)

    if len(edges) < 2:
        if verbose:
            print(f"  {b('║')}  {y(f'Not enough edges ({len(edges)}). Check channel assignment.')}")
            print(f"  {b('╚' + '═' * 78 + '╝')}")
        return 0, TimingReport(
            channel=channel_name, baud=baud, ideal_us=ideal_us, tolerance=tolerance,
            total_edges=len(edges), total_pulses=0, fault_count=0, idle_count=0,
            min_us=0, max_us=0, mean_us=0, std_us=0,
        )

    # ── Step 3: Analyze pulses ───────────────────────────────────
    report = analyze_pulses(edges, ideal_us, tolerance, min_gap_us, baud=baud)
    report.channel = channel_name
    report.edges = edges  # expose for downstream use

    if verbose:
        print(f"  {b('║')}  {c('Edges found:')} {len(edges):,}   "
              f"{c('Pulses analyzed:')} {len(report.pulses)}   "
              f"{c('Idle gaps:')} {report.idle_count}")
        print(f"  {b('╠' + '═' * 78 + '╣')}")

        # ── Step 4: Statistics ───────────────────────────────────
        if report.pulses:
            print(f"  {b('║')}  {c('Pulse width statistics (edge-to-edge):')}")
            print(f"  {b('║')}    {c('Mean:')} {report.mean_us:.4f} us  "
                  f"{c('Std:')} {report.std_us:.4f} us  "
                  f"{c('Min:')} {report.min_us:.4f} us  "
                  f"{c('Max:')} {report.max_us:.4f} us")
            deviation_pct = (report.mean_us - ideal_us) / ideal_us * 100
            dev_str = f"{'+' if deviation_pct >= 0 else ''}{deviation_pct:.2f}%"
            dev_color = g(dev_str) if abs(deviation_pct) < 1 else y(dev_str) if abs(deviation_pct) < 5 else r(dev_str)
            print(f"  {b('║')}    {c('Mean vs ideal:')} {dev_color} ({ideal_us:.4f} us ideal)")
            if abs(report.baud_mismatch_pct) > tolerance * 100:
                implied_baud = round((1_000_000.0 / report.mean_us) / 100) * 100
                mismatch_str = y(f"⚠ BAUD MISMATCH: signal implies {implied_baud} baud (declared {baud}, dev={report.baud_mismatch_pct:+.1f}%)")
                print(f"  {b('║')}    {mismatch_str}")
            print(f"  {b('╠' + '═' * 78 + '╣')}")

        # ── Step 5: Timing Ruler ─────────────────────────────────
        print(f"  {b('║')}  {b('Timing ruler (pulse width distribution):')}")
        ruler = render_timing_ruler(ideal_us, tolerance, width=76)
        for lr in ruler.split('\n'):
            print(f"  {b('║')}  {lr}")
        print(f"  {b('╠' + '─' * 78 + '╣')}")

        # ── Step 6: Histogram ────────────────────────────────────
        print(f"  {b('║')}  {b('Pulse width histogram:')}")
        rows = render_timing_histogram(report.pulses, bins=25)
        if rows:
            max_bar_len = 60
            for label, count, bar_colored in rows:
                bar_display = bar_colored[:max_bar_len] if bar_colored else ''
                count_str = f"({count})"
                print(f"  {b('║')}    {label:>18} │ {bar_display:<{max_bar_len}} {d(count_str)}")
        else:
            print(f"  {b('║')}    {d('No pulses to display')}")
        print(f"  {b('╠' + '─' * 78 + '╣')}")

        # ── Step 7: Sparkline ────────────────────────────────────
        if report.pulses:
            print(f"  {b('║')}  {b('Bit width sparkline:')}  "
                  f"{g('│')}=1-bit ideal  {m('═')}=multi-bit  {r('X')}=fault  {c('│')}=ok")
            for sl in render_timing_sparkline(report.pulses, max_pulses=200, width=76, ideal_us=ideal_us, tolerance=tolerance):
                print(f"  {b('║')}  {sl}")
            print(f"  {b('╠' + '─' * 78 + '╣')}")

        # ── Step 8: Fault details ────────────────────────────────
        print(f"  {b('║')}  {b('Timing faults:')}")
        if report.faults:
            shown = report.faults[:10]
            for idx, msg, dt_us in shown:
                print(f"  {b('║')}    {r('✗')}  {r(f'pulse #{idx}:')} {r(msg)}")
            if len(report.faults) > 10:
                print(f"  {b('║')}    {y(f'... and {len(report.faults) - 10} more faults')}")
        else:
            print(f"  {b('║')}    {g('✓')}  {g('No timing faults detected within tolerance')}")

        print(f"  {b('╠' + '═' * 78 + '╣')}")

        # ── Step 9: Result ───────────────────────────────────────
        if report.fault_count == 0:
            status = f"{g('✓ PASS')}  {g('0 physical timing violations')}"
            print(f"  {b('║')}  {b(status)}")
        else:
            status = f"{r('✗ FAIL')}  {r(f'{report.fault_count} physical timing violation(s)')}"
            print(f"  {b('║')}  {b(status)}")

        print(f"  {b('╚' + '═' * 78 + '╝')}")

    return report.fault_count, report


# ─── Baud Rate Estimation from Raw Samples ─────────────────────────
def estimate_baud_from_samples(
    channel_samples: list,
    sample_rate_hz: int,
    declared_baud: int = 115200,
) -> Tuple[float, float]:
    """
    Estimate actual baud rate from raw sample transitions.

    Returns:
        Tuple of (estimated_baud, deviation_pct)
        deviation_pct > 2 means declared baud doesn't match observed.
    Uses sample-index counting — no VCD quantization errors.
    """
    if not channel_samples or len(channel_samples) < 1000:
        return declared_baud, 0.0

    # Find run-lengths (how many samples each bit level persists)
    runs = []
    current_val = channel_samples[0]
    run_len = 1
    for i in range(1, len(channel_samples)):
        if channel_samples[i] == current_val:
            run_len += 1
        else:
            runs.append((current_val, run_len))
            current_val = channel_samples[i]
            run_len = 1
    runs.append((current_val, run_len))

    # Filter to realistic UART bit run lengths for any common baud (9600-230400+).
    # At 9600: ~1250 samples @ 12MHz. At 115200: ~104 samples @ 12MHz.
    # Use a fixed upper limit that covers the byte period at the lowest baud (9600).
    # At 9600 @ 12MHz: 10 bits = 12500 samples. Use 20000 as fixed cap.
    # Minimum: 50 samples (corresponds to ~240K baud @ 12MHz).
    max_run = max(20000, int(sample_rate_hz / declared_baud * 12))
    active_runs = [rl for _, rl in runs if 50 <= rl <= max_run]

    if not active_runs:
        return declared_baud, 0.0

    # Strategy: use the MODE of runs that correspond to 1-bit periods.
    # Single-bit runs fall in a narrow band: [min_run, min_run * 1.1].
    # Consecutive bits (2×, 3×, ...) are in separate bands.
    min_rl = min(active_runs)
    upper_1bit = min_rl * 1.15  # 15% tolerance for 1-bit runs
    single_bit_runs = [rl for rl in active_runs if rl <= upper_1bit]

    if single_bit_runs:
        # Mode of single-bit runs gives the most robust 1-bit period estimate
        from collections import Counter
        best_rl, _ = Counter(single_bit_runs).most_common(1)[0]
    else:
        # Fall back to minimum run length (works when consecutive bits dominate)
        best_rl = min_rl

    implied_baud = sample_rate_hz / best_rl
    deviation_pct = (implied_baud - declared_baud) / declared_baud * 100

    return implied_baud, deviation_pct


# ─── Byte-to-Timing Mapping ───────────────────────────────────────
def byte_timing_map(
    sr_file: str,
    channel: int = 0,
    decoded_bytes: Optional[List[int]] = None,
    channel_samples: Optional[List[int]] = None,
    sample_rate_hz: int = 12_000_000,
    baud: int = UART_BAUD,
    tolerance: float = DEFAULT_TOLERANCE,
) -> List[ByteTiming]:
    """
    Map timing faults to individual decoded bytes.

    The decoder samples at specific sample indices. We find the sample index
    range for each byte, then find which VCD edges fall in that range.

    Args:
        sr_file: Path to .sr capture file (for VCD export)
        channel: Probe channel (0=D0, ...)
        decoded_bytes: List of decoded byte values (from decoder)
        channel_samples: List of raw 0/1 samples for the channel
        sample_rate_hz: Sample rate in Hz
        baud: UART baud rate
        tolerance: Timing fault threshold

    Returns:
        List of ByteTiming objects, one per decoded byte.
        ByteTiming.fault_count > 0 means that byte has timing warnings.
    """
    if decoded_bytes is None:
        return []
    if not decoded_bytes:
        return []

    # ── Get edges from VCD ───────────────────────────────────────
    try:
        vcd_text = export_vcd(sr_file, channel=channel)
    except RuntimeError:
        return []

    parser = VCDEdgeParser()
    channel_name = f'D{channel}'
    edges = parser.parse_vcd_text(vcd_text, target_channel=channel_name)

    if len(edges) < 2:
        return []

    # ── Map decoded bytes to sample index ranges ────────────────
    # UART byte timing: 10 bits (1 start + 8 data + 1 stop) at given baud
    # Each byte takes: 10 / baud seconds
    bytes_per_sec = baud / 10  # bytes/sec
    samples_per_byte = sample_rate_hz / bytes_per_sec  # ~1042 samples @ 12MHz, 115200 baud

    # Find where each byte starts in the sample stream.
    # We align by finding the first start bit (falling edge from idle=1 to start=0).
    if channel_samples is None:
        # Fall back: evenly distribute across the capture window
        total_samples = edges[-1].timestamp_us * sample_rate_hz / 1_000_000
        samples_per_byte = total_samples / max(len(decoded_bytes), 1)
    else:
        # Find first start bit (1→0 transition) in the samples
        start_idx = None
        for i in range(1, len(channel_samples)):
            if channel_samples[i] == 0 and channel_samples[i - 1] == 1:
                start_idx = i
                break
        if start_idx is None:
            total_samples = edges[-1].timestamp_us * sample_rate_hz / 1_000_000
            samples_per_byte = total_samples / max(len(decoded_bytes), 1)
        else:
            samples_per_byte = sample_rate_hz / bytes_per_sec

    # ── Map VCD edges to byte indices ───────────────────────────
    # VCD times are in microseconds. We build a timeline of edge times.
    edge_times_us = [e.timestamp_us for e in edges]

    result: List[ByteTiming] = []
    samples_per_us = sample_rate_hz / 1_000_000

    for byte_idx in range(len(decoded_bytes)):
        # Sample range for this byte
        if channel_samples is not None and start_idx is not None:
            byte_start_sample = int(start_idx + byte_idx * samples_per_byte)
            byte_end_sample = int(byte_start_sample + samples_per_byte)
        else:
            # Evenly spaced fallback
            byte_start_sample = int(byte_idx * samples_per_byte)
            byte_end_sample = int((byte_idx + 1) * samples_per_byte)

        byte_start_us = byte_start_sample / samples_per_us
        byte_end_us = byte_end_sample / samples_per_us

        # Find edges within this byte's time window
        byte_edges = [
            (e.timestamp_us, e.value)
            for e in edges
            if byte_start_us <= e.timestamp_us < byte_end_us
        ]

        if not byte_edges:
            result.append(ByteTiming(byte_index=byte_idx, fault_count=0, fault_pct=0.0))
            continue

        # Check each edge-to-edge interval for timing faults
        faults_this_byte = []
        worst_pct = 0.0

        for i in range(len(byte_edges) - 1):
            t1, v1 = byte_edges[i]
            t2, v2 = byte_edges[i + 1]
            dt_us = t2 - t1

            ideal_us = 1_000_000.0 / baud
            lo = ideal_us * (1 - tolerance)
            hi = ideal_us * (1 + tolerance)
            gap_threshold = ideal_us * 1.5
            byte_time = ideal_us * 10

            if dt_us > byte_time:
                continue  # idle gap, skip

            if dt_us > gap_threshold:
                num_bits = dt_us / ideal_us
                nearest = round(num_bits)
                deviation = abs(dt_us - nearest * ideal_us)
                if deviation > tolerance * ideal_us:
                    pct = deviation / ideal_us * 100
                    faults_this_byte.append(pct)
                    if pct > worst_pct:
                        worst_pct = pct
            elif not (lo <= dt_us <= hi):
                pct = abs(dt_us - ideal_us) / ideal_us * 100
                faults_this_byte.append(pct)
                if pct > worst_pct:
                    worst_pct = pct

        result.append(ByteTiming(
            byte_index=byte_idx,
            fault_count=len(faults_this_byte),
            fault_pct=round(worst_pct, 2),
            faulty_bits=[],
        ))

    return result


# ─── CLI ───────────────────────────────────────────────────────────
def main():
    p = argparse.ArgumentParser(description='Hardware Timing Analysis — VCD-based UART timing verifier')
    p.add_argument('sr_file', help='.sr capture file from sigrok-cli')
    p.add_argument('--channel', type=int, default=0, help='Channel number 0-7 (default: 0 = D0)')
    p.add_argument('--baud', type=int, default=UART_BAUD, help=f'Baud rate (default: {UART_BAUD})')
    p.add_argument('--tolerance', type=float, default=DEFAULT_TOLERANCE,
                   help=f'Timing tolerance as fraction (default: {DEFAULT_TOLERANCE} = 5%%)')
    p.add_argument('--min-gap', type=float, default=DEFAULT_MIN_GAP,
                   help=f'Min gap to filter idle (default: {DEFAULT_MIN_GAP} us)')
    p.add_argument('-q', '--quiet', action='store_true', help='Suppress visual output')
    args = p.parse_args()

    faults, report = analyze_timing(
        sr_file=args.sr_file,
        channel=args.channel,
        baud=args.baud,
        tolerance=args.tolerance,
        min_gap_us=args.min_gap,
        verbose=not args.quiet,
    )

    if faults < 0:
        sys.exit(2)  # Error
    elif faults == 0:
        print(f"\n  PASS: 0 physical timing violations found")
        sys.exit(0)
    else:
        print(f"\n  FAIL: {faults} physical timing violation(s) found")
        sys.exit(1)


if __name__ == '__main__':
    main()
