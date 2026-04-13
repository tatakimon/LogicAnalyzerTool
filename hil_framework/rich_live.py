#!/usr/bin/env python3
"""
HIL Framework - Interactive Live Dashboard (rich)

A live-updating terminal UI using the `rich` library for real-time display of
logic analyzer captures. Automatically detects when a proper X11 terminal is
available for the matplotlib-based waveform view.

Features:
- Real-time scrolling table of decoded bytes
- Live waveform (matplotlib) when X11 is available, ASCII trace otherwise
- Baud mismatch detection (sample-based, no VCD needed)
- Pattern validation with colored pass/fail badges
- 6-digit byte counter

Requires: python3, pyserial, rich
  pip3 install --break-system-packages rich

Usage:
    python3 hil_framework/rich_live.py              # Live capture, 3s
    python3 hil_framework/rich_live.py --duration 5  # 5s capture
    python3 hil_framework/rich_live.py --channel 1   # Channel 1 (PD8)
"""
import argparse
import sys
import os
import time
import threading
import queue

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    from capture import quick_capture
    from validator import TestValidator
    from timing import estimate_baud_from_samples, byte_timing_map
except ImportError:
    from .capture import quick_capture
    from .validator import TestValidator
    from .timing import estimate_baud_from_samples, byte_timing_map

# ── Rich imports ──────────────────────────────────────────────────
from rich.live import Live
from rich.table import Table
from rich.text import Text
from rich.panel import Panel
from rich.layout import Layout
from rich.console import Console, Group
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn
from rich.rule import Rule
from rich import box
from rich._null_file import NullFile

# ── Byte colors (matches dashboard.py) ─────────────────────────
def byte_color(b):
    if b == 0x55: return "green"
    if b == 0xAA: return "yellow"
    if b == 0xFF: return "red"
    if b == 0x00: return "dim"
    if 32 <= b < 127: return "cyan"
    return "magenta"


def fmt_hex(b): return f"0x{b:02X}"
def fmt_bin(b): return f"{b:08b}"
def fmt_chr(b):
    if 32 <= b < 127: return f"'{chr(b)}'"
    if b == 0: return "'.'"
    return "'?'"


# ── ASCII waveform (always works) ───────────────────────────────
def waveform_bar(b, width=14):
    """ASCII waveform: start bit + 8 data bits + stop bit."""
    bits = [(b >> i) & 1 for i in range(8)]
    lo, hi = '_', '▄'
    s = lo + ''.join(hi if bit else lo for bit in bits) + lo
    return s[:width].ljust(width)


# ── Capture worker thread ────────────────────────────────────────
class CaptureSession:
    def __init__(self, duration=3.0, channel=1, baud=115200, sample_rate='12M'):
        self.duration = duration
        self.channel = channel
        self.baud = baud
        self.sample_rate = sample_rate
        self.data_queue = queue.Queue()
        self.done_event = threading.Event()
        self.error = None
        self.baud_implied = baud
        self.baud_dev_pct = 0.0
        self.total_bytes = 0
        self.channel_samples = []
        self.decoded_text = ''
        self.validation = None
        self.timing_map = []  # list of ByteTiming objects, indexed by byte position
        self.start_time = time.time()

    def run(self):
        t = threading.Thread(target=self._worker, daemon=True)
        t.start()

    def _worker(self):
        try:
            result = quick_capture(
                duration_s=self.duration,
                sample_rate=self.sample_rate,
                channel=self.channel,
                baud=self.baud,
            )
        except Exception as e:
            self.error = str(e)
            self.done_event.set()
            return

        if not result['success']:
            err = result.get('error')
            if err:
                self.error = err
            else:
                # capture succeeded but no bytes decoded — likely probe issue
                samples = result.get('channel_samples', [])
                if samples and all(s == samples[0] for s in samples[:100]):
                    self.error = "No signal transitions (probe disconnected or flat?)"
                elif samples:
                    self.error = f"No UART bytes decoded (0 transitions)"
                else:
                    self.error = "Capture succeeded but no signal data"
            self.done_event.set()
            return

        raw_bytes = result.get('raw_bytes', [])
        decoded_text = result.get('text', '')
        channel_samples = result.get('channel_samples', [])

        # Baud estimation
        if channel_samples:
            implied_baud, dev_pct = estimate_baud_from_samples(
                channel_samples,
                result.get('sample_rate_hz', 12_000_000),
                declared_baud=self.baud,
            )
            self.baud_implied = implied_baud
            self.baud_dev_pct = dev_pct
        else:
            implied_baud, dev_pct = self.baud, 0.0

        self.channel_samples = channel_samples
        self.decoded_text = decoded_text
        self.total_bytes = len(raw_bytes)

        # Per-byte timing fault map (VCD-based)
        sr_file = result.get('sr_filepath', '')
        if sr_file and channel_samples:
            self.timing_map = byte_timing_map(
                sr_file=sr_file,
                channel=self.channel,
                decoded_bytes=raw_bytes,
                channel_samples=channel_samples,
                sample_rate_hz=result.get('sample_rate_hz', 12_000_000),
                baud=self.baud,
                tolerance=0.05,
            )

        # Validate
        validator = TestValidator('USART3 Patterns')
        for p in ['[0x55]', '[0xAA]', '[0xFF]', '[0x00]', '[CNT]', '[ASCII]']:
            validator.expect_pattern(p)
        self.validation = validator.validate(decoded_text, raw_bytes)

        # Post bytes to queue — include per-byte timing info
        for i, b in enumerate(raw_bytes):
            tinfo = self.timing_map[i] if i < len(self.timing_map) else None
            self.data_queue.put((b, i, tinfo))
            time.sleep(0.002)
        self.done_event.set()

    def get_updates(self):
        """Yield available (byte, step) tuples until done."""
        while True:
            try:
                item = self.data_queue.get_nowait()
                if item is None:
                    break
                yield item
            except queue.Empty:
                break


# ── Main Live Display ─────────────────────────────────────────────
def create_layout(console, session):
    """Build the rich Layout object, updating dynamically."""
    layout = Layout()

    # Header
    header = Panel(
        Text("  HIL LOGIC ANALYZER  —  LIVE DASHBOARD  ", style="bold magenta"),
        title="[B-U585I-IOT02A | Saleae Logic @ 12MHz | 115200 8N1 | CH1=PD8]",
        border_style="blue",
        box=box.DOUBLE,
        padding=(0, 2),
    )

    # Byte table
    table = Table(
        title="  DECODED BYTES  ",
        box=box.SIMPLE_HEAVY,
        show_header=True,
        header_style="bold cyan",
        title_style="bold cyan",
        padding=(0, 1),
        min_width=100,
    )
    table.add_column("#", width=5, justify="right", style="dim")
    table.add_column("Hex", width=6, justify="right")
    table.add_column("Binary", width=10, justify="right")
    table.add_column("Char", width=4, justify="center")
    table.add_column("Waveform", width=14)
    table.add_column("Pattern", width=10)
    table.add_column("Step", width=6, justify="right", style="dim")

    # Timing summary
    dev_pct = session.baud_dev_pct
    implied = session.baud_implied
    n_bytes = session.total_bytes
    elapsed = time.time() - session.start_time

    if dev_pct is not None and abs(dev_pct) > 2.0:
        timing_status = f"[red]● BAUD MISMATCH[/red]  ~{int(implied)} baud vs {session.baud} declared"
    elif n_bytes > 0:
        timing_status = f"[green]● TIMING OK[/green]  {n_bytes} bytes, {elapsed:.1f}s"
    else:
        timing_status = "[dim]● AWAITING CAPTURE...[/dim]"

    timing_panel = Panel(
        Text(timing_status),
        border_style="blue",
        box=box.SIMPLE_HEAVY,
        padding=(0, 2),
    )

    # Validation table
    val_table = Table(
        box=box.SIMPLE_HEAVY,
        show_header=False,
        padding=(0, 2),
    )
    val_table.add_column("Result")
    val_table.add_column("Pattern")
    val_table.add_column("Status")

    patterns = ['[0x55]', '[0xAA]', '[0xFF]', '[0x00]', '[CNT]', '[ASCII]']
    if session.validation:
        passed = 0
        for v in session.validation.validations:
            p = v.passed
            if p:
                passed += 1
            mark = "✓" if p else "✗"
            clr = "green" if p else "red"
            val_table.add_row(
                Text(mark, style=clr),
                Text(v.name, style="cyan"),
                Text("PASS" if p else "FAIL", style=clr),
            )
        hil_style = "green bold" if passed == len(patterns) else "red bold"
        val_table.add_row("", "", Text(f"HIL RESULT: {passed}/{len(patterns)} PASSED", style=hil_style))
    else:
        for pat in patterns:
            val_table.add_row("[dim]○[/dim]", Text(pat, style="dim"), Text("WAITING", style="dim"))
        val_table.add_row("", "", Text("HIL RESULT: —/6", style="dim bold"))

    val_panel = Panel(
        val_table,
        title="  VALIDATION RESULTS  ",
        border_style="blue",
        box=box.SIMPLE_HEAVY,
        padding=(0, 1),
    )

    # Right column
    right = Group(timing_panel, val_panel)

    # Two-column layout
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="main"),
        Layout(name="footer", size=5),
    )
    layout["main"].split_row(
        Layout(table, ratio=3),
        Layout(right, ratio=1),
    )

    return layout


def live_capture(duration=3.0, channel=1, baud=115200, sample_rate='12M'):
    console = Console(
        force_terminal=True,
        color_system="truecolor",
        emoji=False,
    )

    session = CaptureSession(duration=duration, channel=channel,
                              baud=baud, sample_rate=sample_rate)
    session.run()

    # Build initial layout
    table_rows = []

    def render():
        # Update table with new rows
        try:
            while True:
                item = session.data_queue.get_nowait()
                if item is None:
                    break
                byte_val, step, tinfo = item
                has_fault = tinfo is not None and tinfo.fault_count > 0
                col = byte_color(byte_val)

                # Build waveform with per-char coloring
                if has_fault:
                    # Red waveform for faulty bytes
                    wf_text = Text()
                    bits = [(byte_val >> i) & 1 for i in range(8)]
                    wf_text.append('_', style="red")
                    for bit in bits:
                        wf_text.append('▄' if bit else '_', style="red")
                    wf_text.append('_', style="red")
                    warn = Text(" [TIMING WARN]", style="red bold")
                else:
                    # Standard byte-value coloring
                    bits = [(byte_val >> i) & 1 for i in range(8)]
                    wf_text = Text()
                    wf_text.append('_', style=col)
                    for bit in bits:
                        wf_text.append('▄' if bit else '_', style=col)
                    wf_text.append('_', style=col)
                    warn = Text("")

                table_rows.append((
                    f"#{step:04d}",
                    fmt_hex(byte_val),
                    fmt_bin(byte_val),
                    fmt_chr(byte_val),
                    wf_text,
                    Text(f"[0x{byte_val:02X}]", style=col),
                    f"#{step:04d}",
                    warn,
                ))
        except queue.Empty:
            pass

        # Keep last 60 rows
        rows = table_rows[-60:]

        layout = Layout()
        layout.split_column(
            Layout(name="header", size=3),
            Layout(name="main"),
        )

        # Header
        header_text = Text("  HIL LOGIC ANALYZER  —  LIVE DASHBOARD  ", style="bold magenta")
        header = Panel(
            header_text,
            title="[B-U585I-IOT02A | Saleae Logic @ 12MHz | CH1=PD8]",
            border_style="blue",
            box=box.DOUBLE,
            padding=(0, 2),
        )

        # Byte table
        table = Table(
            title="  DECODED BYTES  ",
            box=box.SIMPLE_HEAVY,
            show_header=True,
            header_style="bold cyan",
            title_style="bold cyan",
            padding=(0, 1),
            min_width=105,
        )
        table.add_column("#", width=5, justify="right", style="dim")
        table.add_column("Hex", width=6, justify="right")
        table.add_column("Binary", width=10, justify="right")
        table.add_column("Char", width=4, justify="center")
        table.add_column("Waveform", width=14)
        table.add_column("Pattern", width=10)
        table.add_column("Step", width=6, justify="right", style="dim")
        table.add_column("", width=14, justify="left")

        for row in rows:
            table.add_row(*row)

        # Timing panel
        dev_pct = session.baud_dev_pct
        implied = session.baud_implied
        n_bytes = session.total_bytes
        elapsed = time.time() - session.start_time

        if session.error:
            timing_content = Text(f"ERROR: {session.error}", style="red bold")
        elif dev_pct is not None and abs(dev_pct) > 2.0:
            timing_content = Text(
                f"● BAUD MISMATCH\n  Implied: {int(implied)} baud\n  Declared: {baud}\n  Deviation: {dev_pct:+.1f}%\n  Bytes: {n_bytes}",
                style="red")
        elif n_bytes > 0:
            timing_content = Text(
                f"● TIMING OK\n  Implied: {int(implied)} baud\n  Declared: {baud}\n  Deviation: {dev_pct:+.1f}%\n  Bytes: {n_bytes}\n  Elapsed: {elapsed:.1f}s",
                style="green")
        else:
            timing_content = Text("● AWAITING CAPTURE\n\n  Waiting for data...")

        timing_panel = Panel(
            timing_content,
            title="  TIMING ANALYSIS  ",
            border_style="blue",
            box=box.SIMPLE_HEAVY,
            padding=(0, 2),
        )

        # Validation
        val_table = Table(box=box.SIMPLE_HEAVY, show_header=False, padding=(0, 1))
        val_table.add_column()
        val_table.add_column()
        val_table.add_column()

        patterns = ['[0x55]', '[0xAA]', '[0xFF]', '[0x00]', '[CNT]', '[ASCII]']
        if session.validation:
            passed = 0
            for v in session.validation.validations:
                p = v.passed
                if p:
                    passed += 1
                mark = Text("✓", style="green" if p else "red")
                name = Text(v.name, style="cyan")
                status = Text("PASS" if p else "FAIL", style="green" if p else "red")
                val_table.add_row(mark, name, status)
            hil_style = "green bold" if passed == len(patterns) else "red bold"
            val_table.add_row(
                Text(""), Text(""),
                Text(f"HIL RESULT: {passed}/{len(patterns)} PASSED", style=hil_style))
        else:
            for pat in patterns:
                val_table.add_row(
                    Text("○", style="dim"),
                    Text(pat, style="dim"),
                    Text("WAITING", style="dim"),
                )
            val_table.add_row(
                Text(""), Text(""),
                Text("HIL RESULT: —/6", style="dim bold"))

        val_panel = Panel(
            val_table,
            title="  VALIDATION  ",
            border_style="blue",
            box=box.SIMPLE_HEAVY,
            padding=(0, 1),
        )

        right = Group(timing_panel, val_panel)

        layout["header"].update(header)
        layout["main"].split_row(
            Layout(table, ratio=3),
            Layout(right, ratio=1),
        )

        return layout

    with Live(render(), console=console, refresh_per_second=10,
              transient=False) as live:
        while not session.done_event.is_set():
            live.update(render())
            time.sleep(0.1)

        # Final update
        live.update(render())


# ── Entry Point ───────────────────────────────────────────────────
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='HIL Interactive Rich Dashboard')
    parser.add_argument('--duration', type=float, default=3.0, help='Capture duration (default: 3.0s)')
    parser.add_argument('--channel', type=int, default=1, help='Logic analyzer channel (default: 1)')
    parser.add_argument('--baud', type=int, default=115200, help='Expected UART baud rate')
    args = parser.parse_args()

    live_capture(duration=args.duration, channel=args.channel, baud=args.baud)
