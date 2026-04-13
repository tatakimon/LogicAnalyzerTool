#!/usr/bin/env python3
"""
HIL Framework - Logic Analyzer Capture Module

Wraps sigrok-cli for hardware capture from Saleae Logic analyzers.
Auto-detects devices, handles sample rates, manages capture files.
"""
import subprocess
import os
import time
import tempfile
import shutil
import zipfile
from dataclasses import dataclass
from typing import Optional


@dataclass
class DeviceInfo:
    """Discovered logic analyzer device."""
    name: str
    driver: str
    connection: str
    channels: list
    max_sample_rate_hz: int
    available_rates: list


@dataclass
class CaptureResult:
    """Result of a logic analyzer capture."""
    success: bool
    filepath: str
    duration_s: float
    sample_rate_hz: int
    channel_samples: dict  # {channel: list of 0/1 samples}
    error: Optional[str] = None


class LogicAnalyzerCapture:
    """
    Logic analyzer capture via sigrok-cli.
    Auto-detects Saleae devices and handles the full capture pipeline.

    Example:
        cap = LogicAnalyzerCapture()
        devices = cap.list_devices()
        print(devices)

        result = cap.capture(duration_s=2, sample_rate='12M', channel=0)
        print(f"Captured {len(result.channel_samples[0])} samples")
    """

    # Saleae supported sample rates (Hz)
    SALEAE_RATES = {
        '20K': 20_000, '25K': 25_000, '50K': 50_000,
        '100K': 100_000, '200K': 200_000, '250K': 250_000,
        '500K': 500_000, '1M': 1_000_000, '2M': 2_000_000,
        '3M': 3_000_000, '4M': 4_000_000, '6M': 6_000_000,
        '8M': 8_000_000, '12M': 12_000_000, '16M': 16_000_000,
        '24M': 24_000_000, '48M': 48_000_000,
    }

    def __init__(self):
        self._devices: list[DeviceInfo] = []
        self._sigrok_path = shutil.which('sigrok-cli')
        if not self._sigrok_path:
            raise RuntimeError("sigrok-cli not found. Install: apt install sigrok sigrok-cli")
        self._temp_dir = tempfile.mkdtemp(prefix='hil_capture_')
        self._last_capture: Optional[CaptureResult] = None
        self._sr_filepath = ''    # persisted path — survives __del__ cleanup
        self._cleanup_ok = False  # suppress __del__ once we copy to /tmp

    def __del__(self):
        if hasattr(self, '_cleanup_ok') and self._cleanup_ok:
            return  # /tmp copy is the canonical path now
        if hasattr(self, '_temp_dir') and os.path.exists(self._temp_dir):
            shutil.rmtree(self._temp_dir, ignore_errors=True)

    def list_devices(self) -> list[DeviceInfo]:
        """
        Scan for available logic analyzer devices.

        Returns:
            List of DeviceInfo for each discovered device.
        """
        result = subprocess.run(
            ['sigrok-cli', '--scan'],
            capture_output=True, text=True, timeout=10
        )
        output = result.stdout + result.stderr

        devices = []
        for line in output.strip().split('\n'):
            line = line.strip()
            if not line or line.startswith('sigrok-cli'):
                continue

            # Parse lines like: "fx2lafw:conn=1.9 - Saleae Logic [...] with 8 channels: D0 D1 ..."
            if ':' in line and '-' in line:
                try:
                    conn_part, rest = line.split(' - ', 1)
                    driver = conn_part.split(':')[0]
                    conn = conn_part.split('=', 1)[1] if '=' in conn_part else ''

                    name_part, channels_part = rest.rsplit(' with ', 1)
                    name = name_part.split('[')[0].strip()
                    channels = channels_part.replace('channels: ', '').split()

                    # Get device details
                    dev_result = subprocess.run(
                        ['sigrok-cli', '-d', conn_part, '--show'],
                        capture_output=True, text=True, timeout=10
                    )
                    available_rates = []
                    max_rate = 0
                    for sline in dev_result.stdout.split('\n'):
                        if 'samplerate' in sline and 'supported' in sline:
                            # Extract Hz value
                            for rate_name, rate_hz in self.SALEAE_RATES.items():
                                if rate_name in sline or str(rate_hz) in sline:
                                    if rate_hz not in available_rates:
                                        available_rates.append(rate_hz)
                                    if rate_hz > max_rate:
                                        max_rate = rate_hz

                    # Fallback for demo device
                    if 'demo' in driver.lower():
                        name = "Demo Device"
                        channels = ['D0', 'D1', 'D2', 'D3', 'D4', 'D5', 'D6', 'D7', 'A0', 'A1', 'A2', 'A3', 'A4']

                    devices.append(DeviceInfo(
                        name=name.strip(),
                        driver=driver,
                        connection=conn,
                        channels=channels,
                        max_sample_rate_hz=max_rate,
                        available_rates=sorted(available_rates),
                    ))
                except (ValueError, IndexError):
                    continue

        self._devices = devices
        return devices

    def best_sample_rate(self, desired_hz: Optional[int] = None) -> int:
        """
        Get the best available sample rate for the detected device.

        Args:
            desired_hz: Desired sample rate in Hz. If None, uses max available.

        Returns:
            Available sample rate in Hz (best match for desired).
        """
        if not self._devices:
            self.list_devices()

        for dev in self._devices:
            if dev.available_rates:
                if desired_hz and desired_hz in dev.available_rates:
                    return desired_hz
                if not desired_hz:
                    return dev.max_sample_rate_hz
                # Find closest available
                closest = min(dev.available_rates, key=lambda r: abs(r - desired_hz))
                return closest

        # Fallback
        return 12_000_000

    def capture(
        self,
        duration_s: float = 2.0,
        sample_rate: Optional[str] = None,
        sample_rate_hz: Optional[int] = None,
        channel: int = 0,
        use_device: Optional[int] = None,
    ) -> CaptureResult:
        """
        Capture samples from the logic analyzer.

        Args:
            duration_s: Capture duration in seconds
            sample_rate: Sample rate as string (e.g. '12M', '1M'). Overrides sample_rate_hz.
            sample_rate_hz: Sample rate in Hz (e.g. 12_000_000)
            channel: Channel number to capture (0=D0, 1=D1, etc.)
            use_device: Device index to use (from list_devices())

        Returns:
            CaptureResult with channel_samples dict {channel: [0/1 samples]}
        """
        # Re-scan to ensure device list is current
        self.list_devices()

        if not self._devices:
            return CaptureResult(
                success=False,
                filepath='',
                duration_s=0,
                sample_rate_hz=0,
                channel_samples={},
                error="No logic analyzer devices found",
            )

        # Select device - clamp to valid range
        if use_device is None:
            use_device = 0
        use_device = max(0, min(use_device, len(self._devices) - 1))
        dev = self._devices[use_device]
        conn_str = f"{dev.driver}:conn={dev.connection}" if dev.connection else dev.driver

        # Determine sample rate
        rate_hz = sample_rate_hz
        if rate_hz is None and sample_rate:
            rate_hz = self.SALEAE_RATES.get(sample_rate.upper(), 12_000_000)
        elif rate_hz is None:
            rate_hz = dev.max_sample_rate_hz

        # Clamp to available rates
        if rate_hz not in dev.available_rates:
            rate_hz = self.best_sample_rate(rate_hz)

        # Output file
        output_file = os.path.join(self._temp_dir, 'capture.sr')
        duration_ms = int(duration_s * 1000)

        print(f"  Capturing at {rate_hz/1e6:.0f}MHz for {duration_s:.1f}s...")

        cmd = [
            'sigrok-cli',
            '-d', conn_str,
            '-c', f'samplerate={rate_hz}',
            '--time', str(duration_ms),
            '-o', output_file,
        ]

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=int(duration_s) + 10
            )
            if result.returncode != 0:
                return CaptureResult(
                    success=False, filepath='', duration_s=0,
                    sample_rate_hz=0, channel_samples={},
                    error=f"sigrok-cli failed: {result.stderr}"
                )

            if not os.path.exists(output_file) or os.path.getsize(output_file) == 0:
                return CaptureResult(
                    success=False, filepath='', duration_s=0,
                    sample_rate_hz=0, channel_samples={},
                    error="No capture file generated"
                )

        except subprocess.TimeoutExpired:
            return CaptureResult(
                success=False, filepath='', duration_s=0,
                sample_rate_hz=0, channel_samples={},
                error="Capture timed out"
            )
        except Exception as e:
            return CaptureResult(
                success=False, filepath='', duration_s=0,
                sample_rate_hz=0, channel_samples={},
                error=str(e)
            )

        # Extract and parse the .sr ZIP file
        channel_samples = self._extract_channels(output_file, rate_hz, channel)

        self._sr_filepath = output_file  # persist path before temp dir cleanup

        self._last_capture = CaptureResult(
            success=True,
            filepath=output_file,
            duration_s=duration_s,
            sample_rate_hz=rate_hz,
            channel_samples=channel_samples,
        )
        return self._last_capture

    def _extract_channels(
        self, sr_file: str, sample_rate_hz: int, target_channel: Optional[int] = None
    ) -> dict:
        """
        Extract sample data from .sr (ZIP) file.
        Returns dict of {channel: [0/1 samples]}.
        """
        extract_dir = os.path.join(self._temp_dir, 'extracted')
        os.makedirs(extract_dir, exist_ok=True)

        with zipfile.ZipFile(sr_file, 'r') as zf:
            zf.extractall(extract_dir)

        # Read metadata
        metadata_file = os.path.join(extract_dir, 'metadata')
        samples_per_file = 0
        num_files = 0

        if os.path.exists(metadata_file):
            with open(metadata_file) as f:
                for line in f:
                    if 'total probes' in line:
                        parts = line.strip().split('=')
                        if len(parts) > 1:
                            pass  # not directly used
                    if 'samplerate' in line:
                        parts = line.strip().split('=')
                        if len(parts) > 1:
                            pass  # already have rate_hz

        # Find all logic files
        logic_files = sorted(
            [f for f in os.listdir(extract_dir) if f.startswith('logic-')],
            key=lambda x: int(x.split('-')[-1])
        )

        if not logic_files:
            return {}

        # Determine samples per file from first file size
        first_file = os.path.join(extract_dir, logic_files[0])
        samples_per_byte = 8  # 8 channels packed into 1 byte
        samples_per_file = os.path.getsize(first_file) * samples_per_byte

        num_files = len(logic_files)
        total_samples = samples_per_file * num_files
        duration_s = total_samples / sample_rate_hz

        print(f"  Extracted: {num_files} files, {total_samples/1e6:.1f}M samples, {duration_s:.2f}s @ {sample_rate_hz/1e6:.0f}MHz")

        # Extract requested channel(s)
        channels_to_extract = [target_channel] if target_channel is not None else list(range(8))

        channel_samples = {}
        for ch in channels_to_extract:
            samples = []
            for lf_name in logic_files:
                lf_path = os.path.join(extract_dir, lf_name)
                with open(lf_path, 'rb') as f:
                    data = f.read()
                for byte in data:
                    samples.append((byte >> ch) & 1)
            channel_samples[ch] = samples

        return channel_samples

    def get_last_capture(self) -> Optional[CaptureResult]:
        """Return the last successful capture result."""
        return self._last_capture


def quick_capture(
    duration_s: float = 2.0,
    sample_rate: str = '12M',
    channel: int = 0,
    baud: int = 115200,
) -> dict:
    """
    One-shot HIL capture and decode.

    Args:
        duration_s: Capture duration in seconds
        sample_rate: Sample rate (e.g. '12M', '1M')
        channel: Probe channel (0=D0, 1=D1)
        baud: Expected UART baud rate for decoding

    Returns:
        Dict with 'success', 'bytes_decoded', 'text', 'channel_info'
    """
    try:
        from .decoder import UARTDecoder
    except ImportError:
        from decoder import UARTDecoder

    print(f"[HIL] Starting capture: {duration_s}s @ {sample_rate}, channel D{channel}")

    cap = LogicAnalyzerCapture()
    devices = cap.list_devices()

    if not devices:
        return {'success': False, 'error': 'No devices found'}

    # Pick first non-demo device if available
    dev_idx = None
    for i, d in enumerate(devices):
        if 'demo' not in d.driver.lower():
            dev_idx = i
            break
    if dev_idx is None:
        dev_idx = 0

    print(f"[HIL] Using device: {devices[dev_idx].name} @ {sample_rate}")
    print(f"[HIL] Channels: {', '.join(devices[dev_idx].channels)}")

    result = cap.capture(
        duration_s=duration_s,
        sample_rate=sample_rate,
        channel=channel,
        use_device=dev_idx,
    )

    if not result.success:
        return {'success': False, 'error': result.error}

    # Persist the .sr filepath to a stable location before temp dir cleanup.
    # The capture object's __del__ will wipe _temp_dir, so we copy to /tmp.
    sr_filepath = ''
    if result.filepath:
        # Keep the original path and suppress cleanup so it stays valid.
        # _cleanup_ok on the cap object prevents __del__ from removing the temp dir.
        sr_filepath = result.filepath
        cap._cleanup_ok = True

    # Check for activity
    active_channels = []
    for ch, samples in result.channel_samples.items():
        transitions = sum(1 for i in range(1, len(samples)) if samples[i] != samples[i-1])
        active_channels.append((ch, transitions))

    print(f"[HIL] Channel activity: {[(f'D{ch}', t) for ch, t in active_channels]}")

    # Decode UART
    decoder = UARTDecoder(baud=baud)
    all_decoded = {}  # channel -> list of DecodedFrame
    all_bytes = {}   # channel -> list of byte values
    for ch, samples in result.channel_samples.items():
        transitions = sum(1 for i in range(1, len(samples)) if samples[i] != samples[i-1])
        if transitions > 10:  # Only decode channels with significant activity
            frames = decoder.decode_stream(samples, result.sample_rate_hz)
            all_decoded[ch] = frames
            all_bytes[ch] = [f.byte_value for f in frames]
            print(f"[HIL] D{ch}: {len(frames)} bytes decoded")

    # Build result
    primary_frames = all_decoded.get(channel, [])
    primary_bytes = all_bytes.get(channel, [])
    text = ''.join(
        chr(b) if 32 <= b < 127 else f'[{b:02X}]'
        for b in primary_bytes
    )

    return {
        'success': len(primary_bytes) > 0,
        'bytes_decoded': len(primary_bytes),
        'text': text,
        'raw_bytes': primary_bytes,
        'all_channels': {f'D{ch}': [f.byte_value for f in frames]
                         for ch, frames in all_decoded.items()},
        'channel_info': active_channels,
        'sample_rate_hz': result.sample_rate_hz,
        'duration_s': result.duration_s,
        'channel_samples': result.channel_samples.get(channel, []),
        'sr_filepath': sr_filepath,
    }
