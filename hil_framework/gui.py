#!/usr/bin/env python3
"""
HIL Framework - Interactive GUI Dashboard

A graphical alternative to dashboard.py with:
- Live waveform plotting via matplotlib
- Real-time byte decoding table
- Baud mismatch detection (sample-based, no VCD needed)
- Timing analysis integration
- Pattern validation with pass/fail badges

Requires: python3-tk, python3-matplotlib
  sudo apt install python3-tk python3-matplotlib

Or if pip is needed:
  pip3 install --break-system-packages matplotlib Pillow

Usage:
    python3 hil_framework/gui.py                    # Live capture
    python3 hil_framework/gui.py --duration 3       # 3s capture
    python3 hil_framework/gui.py --channel 1        # Channel 1 (PD8)
"""
import importlib
import argparse
import sys
import os
import time
import threading
import queue
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
from collections import deque

# Add parent dir for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    from capture import quick_capture
    from decoder import UARTDecoder
    from validator import TestValidator
    from timing import estimate_baud_from_samples
except ImportError:
    from .capture import quick_capture
    from .decoder import UARTDecoder
    from .validator import TestValidator
    from .timing import estimate_baud_from_samples

# ─── Byte categorization ───────────────────────────────────────────
def byte_category(b):
    """Return category name and hex string for styling."""
    if b == 0x55: return 'pattern_55', f'0x55'
    if b == 0xAA: return 'pattern_AA', f'0xAA'
    if b == 0xFF: return 'pattern_FF', f'0xFF'
    if b == 0x00: return 'pattern_00', f'0x00'
    if 32 <= b < 127: return 'ascii', f'0x{b:02X}'
    return 'binary', f'0x{b:02X}'


# ─── Waveform Plotter ───────────────────────────────────────────────
class WaveformPlotter:
    """matplotlib-based waveform with byte-color overlays."""

    def __init__(self, parent):
        self.parent = parent
        mpl = importlib.import_module('matplotlib')
        mpl.use('Agg')
        self.plt = importlib.import_module('matplotlib.pyplot')
        self.FigureCanvasTk = None
        self._last_samples = None

        # Try to import tkinter-based canvas (will be None without python3-tk)
        try:
            mod = importlib.import_module('matplotlib.backends.backend_tkagg')
            self.FigureCanvasTk = mod.FigureCanvasTkAgg
        except (ImportError, AttributeError):
            pass

        # Colors per byte value category
        self.byte_colors = {
            0x55: '#00cc44',  # green
            0xAA: '#ffcc00',  # yellow
            0xFF: '#ff3333',  # red
            0x00: '#888888',  # grey/dim
            'ascii': '#00aaff',  # cyan
            'binary': '#cc66ff',  # magenta
        }

        self._fig = None
        self._ax = None
        self._canvas = None
        self._img_label = None
        self._headless = self.FigureCanvasTk is None
        self._current_img = None  # PhotoImage cache

    def setup(self):
        if self._headless:
            lbl = tk.Label(self.parent, text='[Waveform will appear here after capture]',
                           bg='#1e1e1e', fg='#888888', font=('Courier', 10))
            lbl.pack(fill=tk.BOTH, expand=True)
            self._img_label = lbl
            return

        self._fig, self._ax = self.plt.subplots(figsize=(10, 3))
        self._fig.patch.set_facecolor('#1e1e1e')
        self._ax.set_facecolor('#1e1e1e')
        self._ax.tick_params(colors='#cccccc', labelsize=8)
        self._ax.xaxis.label.set_color('#cccccc')
        self._ax.yaxis.label.set_color('#cccccc')
        for spine in self._ax.spines.values():
            spine.set_color('#444444')

        self._canvas = self.FigureCanvasTk(self._fig, master=self.parent)
        self._canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

    def plot(self, samples, decoded_bytes, timing_map=None):
        if not samples or len(samples) < 10:
            return

        if self._headless:
            self._plot_headless(samples, decoded_bytes, timing_map)
            return

        self._ax.clear()
        self._ax.set_facecolor('#1e1e1e')
        self._ax.tick_params(colors='#cccccc', labelsize=8)
        for spine in self._ax.spines.values():
            spine.set_color('#444444')
        self._ax.set_title('UART Signal - CH1 (PD8)', color='#cccccc', fontsize=10)

        # Downsample for display: at 12MHz, show max 5000 points
        max_points = 5000
        if len(samples) > max_points:
            step = len(samples) // max_points
            display_samples = [samples[i] for i in range(0, len(samples), step)]
            # Extend last point to cover the remainder
            display_samples.append(samples[-1])
            t = [i * step for i in range(len(display_samples))]
        else:
            display_samples = samples
            t = list(range(len(samples)))

        # Step plot
        t_ext = []
        val_ext = []
        for i, (tt, v) in enumerate(zip(t, display_samples)):
            t_ext.append(tt)
            val_ext.append(v)
            if i < len(t) - 1:
                t_ext.append(tt + 1)
                val_ext.append(v)

        self._ax.plot(t_ext, val_ext, color='#00aaff', linewidth=0.5, drawstyle='steps-post')

        # Color regions by byte
        if decoded_bytes:
            samples_per_bit = len(samples) / (len(decoded_bytes) * 10)
            for i, b in enumerate(decoded_bytes[:100]):  # limit to first 100 for performance
                cat, _ = byte_category(b)
                color = self.byte_colors.get(b, self.byte_colors.get(cat, '#cc66ff'))
                start = int(i * 10 * samples_per_bit)
                end = int((i + 1) * 10 * samples_per_bit)
                if end > len(samples):
                    end = len(samples)
                if start < len(t_ext):
                    self._ax.axvspan(start, min(end, len(t_ext) - 1), alpha=0.05, color=color)

        self._ax.set_xlabel('Sample Index', color='#cccccc', fontsize=8)
        self._ax.set_ylabel('Level', color='#cccccc', fontsize=8)
        self._ax.set_ylim(-0.3, 1.3)
        self._ax.set_yticks([0, 1])
        self._ax.set_yticklabels(['LOW', 'HIGH'])
        self._ax.grid(True, alpha=0.2, color='#444444')
        self._fig.tight_layout()
        self._canvas.draw()
        self._canvas.get_tk_widget().update()

    def _plot_headless(self, samples, decoded_bytes, timing_map=None):
        """Generate waveform image for display in Label (no tkinter canvas)."""
        if self._img_label is None:
            return

        max_points = 3000
        if len(samples) > max_points:
            step = len(samples) // max_points
            display_samples = [samples[i] for i in range(0, len(samples), step)]
            display_samples.append(samples[-1])
        else:
            display_samples = samples

        fig, ax = self.plt.subplots(figsize=(10, 3))
        fig.patch.set_facecolor('#1e1e1e')
        ax.set_facecolor('#1e1e1e')
        ax.tick_params(colors='#cccccc', labelsize=8)
        for spine in ax.spines.values():
            spine.set_color('#444444')

        t = list(range(len(display_samples)))
        t_ext, val_ext = [], []
        for i, (tt, v) in enumerate(zip(t, display_samples)):
            t_ext.append(tt)
            val_ext.append(v)
            if i < len(t) - 1:
                t_ext.append(tt + 1)
                val_ext.append(v)

        ax.plot(t_ext, val_ext, color='#00aaff', linewidth=0.5, drawstyle='steps-post')
        ax.set_title('UART Signal - CH1 (PD8)', color='#cccccc', fontsize=10)
        ax.set_xlabel('Sample Index', color='#cccccc', fontsize=8)
        ax.set_ylabel('Level', color='#cccccc', fontsize=8)
        ax.set_ylim(-0.3, 1.3)
        ax.set_yticks([0, 1])
        ax.set_yticklabels(['LOW', 'HIGH'])
        ax.grid(True, alpha=0.2, color='#444444')
        fig.tight_layout()

        # Save to temp file and load as PhotoImage
        tmp = '/tmp/hil_waveform.png'
        fig.savefig(tmp, dpi=80, bbox_inches='tight', facecolor=fig.get_facecolor())
        self.plt.close(fig)

        try:
            img = tk.PhotoImage(file=tmp)
            self._current_img = img  # keep reference alive
            self._img_label.configure(image=img, text='', bg='#1e1e1e')
        except Exception as e:
            self._img_label.configure(text=f'Waveform: {len(samples)} samples (error: {e})',
                                      bg='#1e1e1e', fg='#888888')


# ─── Main Application ───────────────────────────────────────────────
class HILApp:
    def __init__(self, root, duration=3.0, channel=1, baud=115200):
        self.root = root
        self.duration = duration
        self.channel = channel
        self.baud = baud
        self.sample_rate = '12M'

        self.capturing = False
        self.captured_data = []  # list of decoded bytes
        self.channel_samples = []
        self.text_output = ''
        self.baud_implied = baud
        self.baud_dev_pct = 0.0

        self.stop_event = threading.Event()
        self.data_queue = queue.Queue()
        self.capture_thread = None

        self._build_ui()

    def _build_ui(self):
        self.root.title('HIL Logic Analyzer - Interactive Dashboard')
        self.root.configure(bg='#1e1e1e')
        self.root.geometry('1200x800')

        style = ttk.Style()
        try:
            style.theme_use('clam')
        except:
            pass
        style.configure('.', background='#1e1e1e', foreground='#cccccc',
                        fieldbackground='#2d2d2d', troughcolor='#333333')
        style.configure('TFrame', background='#1e1e1e')
        style.configure('TLabelframe', background='#1e1e1e', foreground='#cccccc',
                       bordercolor='#444444')
        style.configure('TLabelframe.Label', background='#1e1e1e', foreground='#00aaff')
        style.configure('TButton', background='#2d4d2d', foreground='#00cc44')
        style.configure('TLabel', background='#1e1e1e', foreground='#cccccc')
        style.configure('Header.TLabel', background='#1e1e1e', foreground='#cc66ff',
                        font=('Courier', 14, 'bold'))

        # ── Title Bar ─────────────────────────────────────────────
        title_frame = tk.Frame(self.root, bg='#111122', height=40)
        title_frame.pack(fill=tk.X, padx=0, pady=0)
        title_frame.pack_propagate(False)

        title_label = tk.Label(title_frame,
                               text='  HIL LOGIC ANALYZER  —  INTERACTIVE DASHBOARD  ',
                               bg='#111122', fg='#cc66ff',
                               font=('Courier', 14, 'bold'))
        title_label.pack(side=tk.LEFT, pady=8)

        self.status_label = tk.Label(title_frame, text='IDLE',
                                    bg='#111122', fg='#888888',
                                    font=('Courier', 10))
        self.status_label.pack(side=tk.RIGHT, padx=12, pady=8)

        # ── Control Panel ─────────────────────────────────────────
        ctrl_frame = tk.Frame(self.root, bg='#1e1e1e', pady=10)
        ctrl_frame.pack(fill=tk.X, padx=10)

        self.capture_btn = ttk.Button(ctrl_frame, text='▶  CAPTURE',
                                       command=self._start_capture)
        self.capture_btn.pack(side=tk.LEFT, padx=5)

        self.stop_btn = ttk.Button(ctrl_frame, text='■  STOP',
                                   command=self._stop_capture, state='disabled')
        self.stop_btn.pack(side=tk.LEFT, padx=5)

        tk.Label(ctrl_frame, text='Duration (s):', bg='#1e1e1e',
                 fg='#888888').pack(side=tk.LEFT, padx=(20, 5))
        self.duration_var = tk.StringVar(value=str(self.duration))
        duration_entry = ttk.Entry(ctrl_frame, textvariable=self.duration_var, width=5)
        duration_entry.pack(side=tk.LEFT)

        tk.Label(ctrl_frame, text='  Channel:', bg='#1e1e1e',
                 fg='#888888').pack(side=tk.LEFT, padx=(20, 5))
        self.channel_var = tk.IntVar(value=self.channel)
        channel_spin = ttk.Spinbox(ctrl_frame, from_=0, to=7,
                                    textvariable=self.channel_var, width=5)
        channel_spin.pack(side=tk.LEFT)

        tk.Label(ctrl_frame, text='  Baud:', bg='#1e1e1e',
                 fg='#888888').pack(side=tk.LEFT, padx=(20, 5))
        self.baud_var = tk.IntVar(value=self.baud)
        baud_entry = ttk.Entry(ctrl_frame, textvariable=self.baud_var, width=8)
        baud_entry.pack(side=tk.LEFT)

        tk.Label(ctrl_frame, text=f'  [{self.sample_rate} @ 12MHz | 8N1]',
                 bg='#1e1e1e', fg='#444444').pack(side=tk.LEFT, padx=10)

        # ── Main Content (Plot + Table) ────────────────────────────
        content = tk.PanedWindow(self.root, bg='#1e1e1e')
        content.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

        # Waveform plot frame
        plot_frame = tk.LabelFrame(content, text=' WAVEFORM ', bg='#1e1e1e',
                                    fg='#00aaff', font=('Courier', 10, 'bold'))
        content.add(plot_frame, width=900)

        self.plotter = WaveformPlotter(plot_frame)
        self.plotter.setup()

        # Right panel: Timing + Validation
        right_panel = tk.PanedWindow(content, bg='#1e1e1e', orient=tk.VERTICAL)
        content.add(right_panel, width=280)

        # ── Timing Panel ──────────────────────────────────────────
        self.timing_frame = tk.LabelFrame(right_panel, text=' TIMING ANALYSIS ',
                                          bg='#1e1e1e', fg='#00aaff',
                                          font=('Courier', 10, 'bold'))
        right_panel.add(self.timing_frame, height=200)

        self.timing_text = tk.Text(self.timing_frame, height=9, width=32,
                                    bg='#1e1e1e', fg='#888888',
                                    font=('Courier', 9), relief=tk.FLAT,
                                    state='disabled', wrap=tk.WORD)
        self.timing_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # ── Decoded Table Frame ─────────────────────────────────────
        table_frame = tk.LabelFrame(right_panel, text=' DECODED BYTES ',
                                     bg='#1e1e1e', fg='#00aaff',
                                     font=('Courier', 10, 'bold'))
        right_panel.add(table_frame, height=300)

        columns = ('#', 'Hex', 'Bin', 'Char')
        self.table = ttk.Treeview(table_frame, columns=columns, show='tree headings',
                                   height=12)
        self.table.heading('#', text='#')
        self.table.heading('Hex', text='Hex')
        self.table.heading('Bin', text='Binary')
        self.table.heading('Char', text='Char')
        self.table.column('#', width=50, anchor=tk.E)
        self.table.column('Hex', width=60, anchor=tk.E)
        self.table.column('Bin', width=90, anchor=tk.E)
        self.table.column('Char', width=50, anchor=tk.E)
        self.table.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # Scrollbar for table
        scrollbar = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self.table.yview)
        self.table.configure(yscrollcommand=scrollbar.set)

        # ── Validation Frame ───────────────────────────────────────
        self.val_frame = tk.LabelFrame(self.root, text=' VALIDATION RESULTS ',
                                       bg='#1e1e1e', fg='#00aaff',
                                       font=('Courier', 10, 'bold'))
        self.val_frame.pack(fill=tk.X, padx=10, pady=(0, 10))

        self.val_labels = {}
        self.validator = TestValidator('USART3 Patterns')
        for p in ['[0x55]', '[0xAA]', '[0xFF]', '[0x00]', '[CNT]', '[ASCII]']:
            self.validator.expect_pattern(p)
            self.val_labels[p] = tk.Label(self.val_frame, text=f'  {p}  —  WAITING',
                                            bg='#1e1e1e', fg='#888888',
                                            font=('Courier', 9))
            self.val_labels[p].pack(side=tk.LEFT, padx=8, pady=5)

        self.hil_result = tk.Label(self.val_frame, text='  HIL RESULT: —/6  ',
                                   bg='#1e1e1e', fg='#888888',
                                   font=('Courier', 10, 'bold'))
        self.hil_result.pack(side=tk.RIGHT, padx=15, pady=5)

        # Initial timing display
        self._update_timing_display()

    def _update_timing_display(self, baud_implied=None, dev_pct=None, n_bytes=0):
        self.timing_text.configure(state='normal')
        self.timing_text.delete('1.0', tk.END)

        lines = [
            f'  Declared Baud: {self.baud}',
            f'  Implied Baud:  {"—" if baud_implied is None else int(baud_implied)}',
            f'  Deviation:     {"—" if dev_pct is None else f"{dev_pct:+.1f}%"}',
            f'  Bytes:         {n_bytes}',
            f'  Sample Rate:   {self.sample_rate}',
            '',
        ]

        if dev_pct is not None:
            if abs(dev_pct) <= 2.0:
                lines.append('  [OK] Baud within tolerance')
            else:
                lines.append('  [BAUD MISMATCH]')
                lines.append('  Signal baud does not match declared!')
        else:
            lines.append('  [AWAITING CAPTURE]')

        for line in lines:
            color = '#00cc44' if '[OK]' in line else \
                    '#ff3333' if '[BAUD MISMATCH]' in line else \
                    '#cccccc'
            self.timing_text.insert(tk.END, line + '\n')
            # Simple tag approach: just use the last fg color
        self.timing_text.configure(state='disabled')

    def _update_validation(self, text, bytes_data):
        results = self.validator.validate(text, bytes_data)
        passed = sum(1 for v in results.validations if v.passed)
        total = len(results.validations)

        for v in results.validations:
            p = v.passed
            lbl = self.val_labels.get(v.name, None)
            if lbl:
                color = '#00cc44' if p else '#ff3333'
                mark = '✓' if p else '✗'
                status = 'PASS' if p else 'FAIL'
                lbl.configure(text=f' {mark} {status} {v.name} ',
                                bg='#1e1e1e', fg=color, font=('Courier', 9, 'bold'))

        hil_color = '#00cc44' if passed == total else '#ff3333'
        self.hil_result.configure(text=f'  HIL RESULT: {passed}/{total} PASSED  ',
                                   bg='#1e1e1e', fg=hil_color,
                                   font=('Courier', 10, 'bold'))

    def _add_byte_to_table(self, byte_val, step):
        cat, hex_str = byte_category(byte_val)
        bin_str = f'{byte_val:08b}'
        chr_str = chr(byte_val) if 32 <= byte_val < 127 else '.'

        # Insert at top
        self.table.insert('', 0, values=(f'#{step:04d}', hex_str, bin_str, f"'{chr_str}'"))

        # Keep max 500 rows
        children = self.table.get_children('')
        if len(children) > 500:
            for item in children[500:]:
                self.table.delete(item)

    def _start_capture(self):
        try:
            d = float(self.duration_var.get())
            ch = int(self.channel_var.get())
            baud = int(self.baud_var.get())
        except ValueError:
            messagebox.showerror('Invalid Input', 'Duration, Channel, and Baud must be numeric.')
            return

        self.duration = d
        self.channel = ch
        self.baud = baud
        self.captured_data = []
        self.text_output = ''
        self.stop_event.clear()
        self.capturing = True

        # Clear table
        for item in self.table.get_children(''):
            self.table.delete(item)

        # Reset validation
        for p, lbl in self.val_labels.items():
            lbl.configure(text=f'  {p}  —  WAITING', bg='#1e1e1e', fg='#888888',
                           font=('Courier', 9))
        self.hil_result.configure(text='  HIL RESULT: —/6  ',
                                   bg='#1e1e1e', fg='#888888', font=('Courier', 10, 'bold'))

        self.capture_btn.configure(state='disabled')
        self.stop_btn.configure(state='normal')
        self.status_label.configure(text='CAPTURING...', fg='#ffcc00')

        self.capture_thread = threading.Thread(target=self._capture_worker, daemon=True)
        self.capture_thread.start()

        # Polling loop for UI updates
        self._poll_updates()

    def _poll_updates(self):
        if not self.capturing:
            return
        try:
            while True:
                item = self.data_queue.get_nowait()
                if item is None:  # capture done
                    self.capturing = False
                    self.status_label.configure(text='DONE', fg='#00cc44')
                    self.capture_btn.configure(state='normal')
                    self.stop_btn.configure(state='disabled')
                    self._finalize_capture()
                    return
                else:
                    byte_val, step = item
                    self.captured_data.append(byte_val)
                    self._add_byte_to_table(byte_val, step)
        except queue.Empty:
            pass

        self.root.after(200, self._poll_updates)

    def _capture_worker(self):
        """Background thread: runs capture and posts bytes to queue."""
        try:
            result = quick_capture(
                duration_s=self.duration,
                sample_rate=self.sample_rate,
                channel=self.channel,
                baud=self.baud,
            )
        except Exception as e:
            self.data_queue.put(None)
            self.root.after(0, lambda: messagebox.showerror('Capture Error', str(e)))
            return

        if not result['success']:
            self.data_queue.put(None)
            self.root.after(0, lambda: messagebox.showerror('Capture Error',
                                                            result.get('error', 'Unknown error')))
            return

        raw_bytes = result.get('raw_bytes', [])
        text = result.get('text', '')
        channel_samples = result.get('channel_samples', [])

        # Baud estimation
        if channel_samples:
            implied_baud, dev_pct = estimate_baud_from_samples(
                channel_samples, result.get('sample_rate_hz', 12_000_000),
                declared_baud=self.baud
            )
            self.baud_implied = implied_baud
            self.baud_dev_pct = dev_pct
            self.root.after(0, lambda: self._update_timing_display(
                implied_baud, dev_pct, len(raw_bytes)))
        else:
            self.root.after(0, lambda: self._update_timing_display(n_bytes=len(raw_bytes)))

        # Plot waveform
        if channel_samples:
            self.root.after(0, lambda: self.plotter.plot(
                channel_samples, raw_bytes, None))

        # Post bytes one by one for live table
        for i, b in enumerate(raw_bytes):
            self.data_queue.put((b, i))
            time.sleep(0.001)  # small delay to prevent queue flooding

        # Update validation after all bytes
        self.root.after(0, lambda: self._update_validation(text, raw_bytes))

        self.data_queue.put(None)  # signal done

    def _stop_capture(self):
        self.stop_event.set()
        self.capturing = False
        self.status_label.configure(text='STOPPED', fg='#ff3333')
        self.capture_btn.configure(state='normal')
        self.stop_btn.configure(state='disabled')

    def _finalize_capture(self):
        if self.captured_data:
            self._update_validation(self.text_output, self.captured_data)


# ─── Entry Point ───────────────────────────────────────────────────
def main():
    root = tk.Tk()
    app = HILApp(root, duration=3.0, channel=1, baud=115200)
    root.mainloop()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='HIL Interactive GUI Dashboard')
    parser.add_argument('--duration', type=float, default=3.0, help='Capture duration (default: 3.0s)')
    parser.add_argument('--channel', type=int, default=1, help='Logic analyzer channel (default: 1)')
    parser.add_argument('--baud', type=int, default=115200, help='Expected UART baud rate')
    args = parser.parse_args()

    root = tk.Tk()
    app = HILApp(root, duration=args.duration, channel=args.channel, baud=args.baud)
    root.mainloop()
