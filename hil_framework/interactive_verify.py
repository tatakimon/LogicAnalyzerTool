#!/usr/bin/env python3
"""
Interactive Accelerometer Verifier — Textual TUI

Streams live VCP data from the board and lets the user visually confirm
whether the accelerometer is working correctly before the copilot proceeds.

Usage:
    python3 hil_framework/interactive_verify.py
    python3 hil_framework/interactive_verify.py --port /dev/ttyACM1 --baud 9600

How it fits into the copilot flow:
    1. Firmware is already flashed and streaming AX=... AY=... AZ=... via VCP
    2. Run this script — it shows live data and asks "Does this look correct?"
    3. User confirms yes/no
    4. Copilot receives result and continues (or retries)
"""
from __future__ import annotations

import re
import sys
import time
from datetime import datetime

import serial
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Button
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Header, Log, Static, Label, Footer
from textual import on


SERIAL_PORT = "/dev/ttyACM0"
BAUD_RATE = 115200
READ_TIMEOUT_S = 1.0
BASELINE_DURATION_S = 2.0   # seconds to capture baseline before tilt prompt
COUNTDOWN_DURATION_S = 3.0  # countdown seconds before tilt
STREAM_AFTER_TILT_S = 6.0  # how long to stream after tilt before YES/NO


def parse_accel(line: str) -> tuple[int, int, int] | None:
    """
    Parse accelerometer values from a VCP line.

    Accepts formats:
      AX=-168 Y=-2635 Z=-16023
      AX=-168 AY=-2635 AZ=-16023
      ax=-168 ay=-2635 az=-16023
      ACCEL X=-168 Y=-2635 Z=-16023
    Returns (x, y, z) as ints, or None if no match.
    """
    m = re.search(
        r"[Aa][Cc][Cc][Ee][Ll]\s+[Xx]=(-?\d+)\s+[Yy]=(-?\d+)\s+[Zz]=(-?\d+)",
        line,
    )
    if m:
        return int(m.group(1)), int(m.group(2)), int(m.group(3))

    m = re.search(
        r"[Aa][Xx][^0-9-]*(-?\d+)[^0-9-]*"
        r"[Aa][Yy][^0-9-]*(-?\d+)[^0-9-]*"
        r"[Aa][Zz][^0-9-]*(-?\d+)",
        line,
    )
    if m:
        return int(m.group(1)), int(m.group(2)), int(m.group(3))

    # Fallback: AX=... Y=... Z=... (no A prefix on Y/Z)
    m = re.search(r"AX=(-?\d+)[^0-9-]*Y=(-?\d+)[^0-9-]*Z=(-?\d+)", line)
    if m:
        return int(m.group(1)), int(m.group(2)), int(m.group(3))

    return None


class InteractiveVerifyApp(App):
    """Textual app for interactive sensor verification."""

    CSS = """
    Screen {
        background: #0d1117;
    }

    Header {
        background: #161b22;
    }

    #title {
        height: 3;
        background: #161b22;
        color: #f0883e;
        content-align: center middle;
        text-style: bold;
    }

    #info-bar {
        height: 3;
        background: #1f2937;
        color: #9ca3af;
        content-align: center middle;
    }

    #data-log {
        color: #3fb950;
        background: #0d1117;
        border: solid #30363d;
        padding: 0 1;
    }

    #summary-bar {
        height: 3;
        background: #161b22;
        color: #58a6ff;
        content-align: center middle;
    }

    #prompt-container {
        background: #1a2332;
        border: solid #f0883e;
        padding: 1 2;
        content-align: center middle;
    }

    #prompt-text {
        color: #f0883e;
        text-style: bold;
    }

    #btn-row {
        height: 3;
        align: center middle;
    }

    #btn-yes {
        background: #238636;
        color: #ffffff;
    }

    #btn-no {
        background: #da3633;
        color: #ffffff;
    }

    #btn-tilt {
        background: #1f6feb;
        color: #ffffff;
    }

    #result-banner {
        height: 5;
        background: #161b22;
        content-align: center middle;
    }

    Footer {
        background: #161b22;
        color: #8b949e;
    }
    """

    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit", priority=True),
        Binding("y", "confirm_yes", "Yes — looks correct"),
        Binding("n", "confirm_no", "No —有问题"),
        Binding("ctrl+c", "quit", "Quit"),
    ]

    def __init__(self, port: str = SERIAL_PORT, baud: int = BAUD_RATE,
                 baseline_s: float = BASELINE_DURATION_S,
                 countdown_s: float = COUNTDOWN_DURATION_S,
                 stream_s: float = STREAM_AFTER_TILT_S):
        super().__init__()
        self.port = port
        self.baud = baud
        self.baseline_s = baseline_s
        self.countdown_s = countdown_s
        self.stream_s = stream_s
        self._user_result: bool | None = None
        self._reading_count = 0
        self._baseline_x = self._baseline_y = self._baseline_z = 0
        self._has_baseline = False
        self._phase = "baseline"   # "baseline" | "countdown" | "stream" | "done"
        self._countdown_ticks: list[str] = []

    # ── Compose ─────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(
            "INTERACTIVE SENSOR VERIFIER  —  Verify Live Accelerometer Data",
            id="title",
        )
        yield Static(
            f"  {self.port} @ {self.baud} baud  |  baseline→countdown→stream→YES/NO",
            id="info-bar",
        )
        yield Log(id="data-log")
        yield Static("", id="summary-bar")
        with Container(id="prompt-container"):
            yield Label(
                "Waiting for data stream from board...",
                id="prompt-text",
            )
            with Horizontal(id="btn-row"):
                yield Button("YES — Looks Correct", id="btn-yes", variant="success")
                yield Button("NO — Something Wrong", id="btn-no", variant="error")
                yield Button("TILT: Tilt the board now!", id="btn-tilt", variant="primary")
        yield Static("", id="result-banner")
        yield Footer()

    # ── Mount ────────────────────────────────────────────────────────────

    def on_mount(self) -> None:
        self.title_log = self.query_one("#data-log", Log)
        self.title_log.auto_scroll = True
        self.summary_bar = self.query_one("#summary-bar", Static)
        self.prompt_text = self.query_one("#prompt-text", Label)
        self.result_banner = self.query_one("#result-banner", Static)

        self.title_log.write(
            "[dim]═══════════════════════════════════════════════════════[/dim]\n"
        )
        self.title_log.write(
            "  PHASE 1 — Baseline\n"
            "  Hold the board still. Capturing 2s of rest values.\n"
        )
        self.title_log.write(
            "[dim]═══════════════════════════════════════════════════════[/dim]\n\n"
        )
        self.summary_bar.update("[cyan]PHASE 1: Baseline capture — hold still...[/cyan]")

        self._serial_task = self.set_interval(0.05, self._read_serial)
        # After baseline: start countdown
        self._countdown_task = self.set_timer(self.baseline_s, self._start_countdown)

    def _start_countdown(self) -> None:
        self._phase = "countdown"
        self.title_log.write(
            "\n[dim]═══════════════════════════════════════════════════════[/dim]\n"
        )
        self.title_log.write(
            "  PHASE 2 — Countdown\n"
            "  Get ready to tilt the board!\n"
        )
        self.title_log.write(
            "[dim]═══════════════════════════════════════════════════════[/dim]\n\n"
        )
        self.summary_bar.update("[yellow]PHASE 2: GET READY to tilt...[/yellow]")
        self._ticks_left = int(self.countdown_s)
        self._countdown_tick_task = self.set_interval(1.0, self._countdown_tick)

    def _countdown_tick(self) -> None:
        if self._ticks_left > 0:
            self.title_log.write(
                f"[bold yellow]>>> GET READY! TILT IN {self._ticks_left}... {self._ticks_left-1}... {self._ticks_left-2}... <<<[/bold yellow]\n"
            )
            self._ticks_left -= 1
        else:
            self._countdown_tick_task.stop()
            self._start_tilt_phase()

    def _start_tilt_phase(self) -> None:
        self._phase = "stream"
        self.title_log.write(
            "\n[bold red blink]>>> TILT NOW! PICK UP THE BOARD! <<<[/bold red blink]\n"
            "[dim]Watch X/Y/Z change — then press YES if values moved.[/dim]\n\n"
        )
        self.summary_bar.update("[red]PHASE 3: Tilt the board — then press YES![/red]")
        self._tilt_stream_task = self.set_timer(self.stream_s, self._on_stream_done)

    # ── Serial read loop ─────────────────────────────────────────────────

    def _read_serial(self) -> None:
        """Poll VCP and update the log + summary."""
        try:
            ser = serial.Serial(self.port, self.baud, timeout=READ_TIMEOUT_S)
        except serial.SerialException as e:
            self.title_log.write(f"[red]Cannot open {self.port}: {e}[/red]\n")
            self.summary_bar.update(f"[red]ERROR: Cannot open {self.port}[/red]")
            return

        try:
            while ser.in_waiting:
                line_bytes = ser.readline()
                if not line_bytes:
                    break
                line = line_bytes.decode("utf-8", errors="replace").strip()
                if not line:
                    continue

                self._process_line(line)
        finally:
            ser.close()

    def _process_line(self, line: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        accel = parse_accel(line)

        # Phase label
        phase_label = {
            "baseline": "[cyan]BASE ",
            "countdown": "[yellow]CDN  ",
            "stream":    "[red]TILT ",
            "done":      "[dim]DONE ",
        }.get(self._phase, "[dim]     ")

        if accel is not None:
            x, y, z = accel
            self._reading_count += 1

            # Establish baseline from first reading
            if not self._has_baseline:
                self._baseline_x, self._baseline_y, self._baseline_z = x, y, z
                self._has_baseline = True
                self.title_log.write(
                    f"\n[dim]Baseline captured: X={x} Y={y} Z={z}[/dim]\n"
                )
                self.summary_bar.update(
                    f"Baseline: X={x}  Y={y}  Z={z}  |  0 readings"
                )

            # Compute deltas from baseline
            dx = abs(x - self._baseline_x)
            dy = abs(y - self._baseline_y)
            dz = abs(z - self._baseline_z)
            max_delta = max(dx, dy, dz)

            # Colour by movement level
            if max_delta < 200:
                colour = "#3fb950"  # green — at rest
            elif max_delta < 1000:
                colour = "#f0c040"  # amber — light tilt
            else:
                colour = "#f04040"  # red — strong motion

            self.title_log.write(
                f"[{ts}] {phase_label}X={x:+6d}[/]  "
                f"[bold {colour}]Y={y:+6d}[/bold {colour}]  "
                f"[bold {colour}]Z={z:+6d}[/bold {colour}]  "
                f"[dim]Δ={max_delta}[/dim]\n"
            )

            self.summary_bar.update(
                f"Baseline X={self._baseline_x} Y={self._baseline_y} Z={self._baseline_z}  |  "
                f"{self._reading_count} readings  "
                f"[{'[red]' if max_delta >= 1000 else '[amber]' if max_delta >= 200 else '[green]'}]"
                f"Δ={max_delta}"
            )
        else:
            # Non-accel line — echo in dim colour
            self.title_log.write(f"[dim][{ts}] {line[:80]}[/dim]\n")

    # ── Stream finished — prompt user ───────────────────────────────────

    def _on_stream_done(self) -> None:
        if self._phase != "stream":
            return  # was called by baseline timer, not the tilt timer
        self._phase = "done"
        self._serial_task.stop()

        if self._reading_count == 0:
            self.title_log.write(
                "\n[red]No accelerometer data received![/red]\n"
                "Is the firmware printing AX=... AY=... AZ=... on VCP?\n"
            )
            self.prompt_text.update("[red]No data received — press N to report failure[/red]")
            return

        # Count A vs B tier (strong motion vs rest)
        self.title_log.write(
            f"\n[dim]═══════════════════════════════════════════════════════[/dim]\n"
            f"  PHASE 4 — Verdict\n"
            f"  Got {self._reading_count} readings.\n"
            f"  Baseline: X={self._baseline_x}  Y={self._baseline_y}  Z={self._baseline_z}\n"
            f"[dim]═══════════════════════════════════════════════════════[/dim]\n"
        )
        self.summary_bar.update("[green bold]PHASE 4: Did X/Y/Z change when you tilted?[/bold green]")
        self.prompt_text.update(
            "[bold gold1]Did the accelerometer respond to tilting?[/bold gold1]\n"
            "  [green]YES[/green]  = X/Y/Z values changed when you tilted the board\n"
            "  [red]NO[/red]  = Values stayed at baseline — sensor not working\n"
        )

    # ── Button / key actions ─────────────────────────────────────────────

    @on(Button.Pressed, "#btn-yes")
    def confirm_yes(self) -> None:
        self._user_result = True
        self._finish()

    @on(Button.Pressed, "#btn-no")
    def confirm_no(self) -> None:
        self._user_result = False
        self._finish()

    @on(Button.Pressed, "#btn-tilt")
    def prompt_tilt(self) -> None:
        self.title_log.write(
            "\n[bold cyan]→ Now tilt/shake the board and watch X/Y/Z change![/bold cyan]\n"
        )
        self.prompt_text.update(
            "[bold gold1]Does the live data look correct?[/bold gold1]\n"
            "  [green]Y / YES[/green]  = X≈0, Y≈0, Z≈±1000 mg when flat — values changed when tilted\n"
            "  [red]N / NO[/red]  = Data is wrong / stuck / missing\n"
        )

    def action_confirm_yes(self) -> None:
        self._user_result = True
        self._finish()

    def action_confirm_no(self) -> None:
        self._user_result = False
        self._finish()

    def _finish(self) -> None:
        for name in ['_serial_task', '_countdown_task', '_countdown_tick_task', '_tilt_stream_task']:
            task = getattr(self, name, None)
            if task is not None:
                try:
                    task.stop()
                except Exception:
                    pass

        if self._user_result is True:
            self.title_log.write(
                "\n[green]✓ User confirmed: data looks correct![/green]\n"
            )
            self.result_banner.update(
                "[green bold]✓ VALIDATED — tilt data confirmed correct[/green bold]"
            )
        else:
            self.title_log.write(
                "\n[red]✗ User reported: data is incorrect.[/red]\n"
                "  Copilot will attempt to diagnose and retry.\n"
            )
            self.result_banner.update(
                "[red bold]✗ FAILED — user reported incorrect data[/red bold]"
            )

        # Write result to a well-known path so the copilot can pick it up
        result_file = "/tmp/hil_interactive_verify_result.txt"
        with open(result_file, "w") as f:
            f.write("PASS\n" if self._user_result else "FAIL\n")

        self.title_log.write(
            f"\n[dim]Result written to {result_file} — copilot will pick this up.[/dim]\n"
        )
        self.prompt_text.update("[dim]Press Ctrl+Q to close.[/dim]")


# ── Standalone entry point ────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Interactive accelerometer verifier")
    parser.add_argument("--port", default=SERIAL_PORT, help="VCP serial port")
    parser.add_argument("--baud", type=int, default=BAUD_RATE, help="Baud rate")
    parser.add_argument("--baseline", type=float, default=BASELINE_DURATION_S,
                        help="Seconds to capture baseline")
    parser.add_argument("--countdown", type=float, default=COUNTDOWN_DURATION_S,
                        help="Countdown seconds before tilt")
    parser.add_argument("--stream", type=float, default=STREAM_AFTER_TILT_S,
                        help="Seconds to stream after tilt")
    args = parser.parse_args()

    app = InteractiveVerifyApp(
        port=args.port, baud=args.baud,
        baseline_s=args.baseline, countdown_s=args.countdown, stream_s=args.stream
    )
    app.run()
