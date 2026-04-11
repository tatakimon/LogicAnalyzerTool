#!/usr/bin/env python3
"""
HIL Framework - UART Decoder Module

Pure-Python UART signal decoder. No numpy, no external dependencies.
Decodes from binary sample streams (0/1 per sample) at any sample rate.

Supports:
  - 5-9 data bits
  - No / odd / even / zero / one parity
  - 1-2 stop bits
  - LSB-first transmission
  - Majority voting for noise immunity
  - Multiple sample rates (1MHz - 48MHz)
"""
import struct
from dataclasses import dataclass
from typing import Optional


@dataclass
class DecodedFrame:
    """A single decoded UART frame."""
    byte_value: int
    start_sample: int
    data_bits: list
    parity_ok: Optional[bool] = None
    frame_ok: bool = True

    def __repr__(self):
        if 32 <= self.byte_value < 127:
            return f"'{chr(self.byte_value)}' (0x{self.byte_value:02X})"
        return f"0x{self.byte_value:02X}"


class UARTDecoder:
    """
    Decode UART frames from a sampled signal stream.

    Handles arbitrary sample rates and uses majority voting for robustness.
    Designed for logic analyzer captures but works with any 0/1 sample stream.

    Example:
        decoder = UARTDecoder(baud=115200, databits=8, parity='N', stopbits=1)
        samples = [1,1,1,0,0,0,...]  # 12MHz sampled signal
        frames = decoder.decode_stream(samples, sample_rate=12_000_000)
        bytes_data = [f.byte_value for f in frames]
    """

    def __init__(
        self,
        baud: int = 115200,
        databits: int = 8,
        parity: str = 'N',  # N=none, O=odd, E=even, Z=zero, O=one
        stopbits: int = 1,
        invert: bool = False,
    ):
        self.baud = baud
        self.databits = databits
        self.parity = parity.upper() if isinstance(parity, str) else 'N'
        self.stopbits = stopbits
        self.invert = invert

        # Compute bit timing
        self.bit_us = 1_000_000.0 / baud

    def _fmt_byte(self, b: int) -> str:
        """Format byte for display."""
        if 32 <= b < 127:
            return f"'{chr(b)}'"
        names = {0: 'NUL', 13: 'CR', 10: 'LF', 27: 'ESC', 9: 'TAB'}
        if b in names:
            return names[b]
        return f"0x{b:02X}"

    def _parity_ok(self, data: int, parity_bit: int, databits: int) -> bool:
        """Check if parity bit matches expected parity."""
        if self.parity == 'N' or self.parity == 'IGNORE':
            return True
        if self.parity == 'Z':  # Parity bit always 0
            return parity_bit == 0
        if self.parity == 'O':  # Parity bit always 1
            return parity_bit == 1
        # Count 1s in data
        ones = bin(data & ((1 << databits) - 1)).count('1') + parity_bit
        if self.parity == 'E':  # Even
            return ones % 2 == 0
        if self.parity == 'ODD':  # Odd
            return ones % 2 == 1
        return True

    def decode_stream(
        self,
        samples: list,
        sample_rate: int,
        stop_on_error: bool = False,
    ) -> list[DecodedFrame]:
        """
        Decode UART frames from a sample stream.

        Args:
            samples: List of 0/1 sample values (one per time step)
            sample_rate: Sample rate in Hz
            stop_on_error: If True, stop on first decode error

        Returns:
            List of DecodedFrame objects
        """
        spb = self.bit_us / (1_000_000.0 / sample_rate)  # samples per bit
        half_spb = spb / 2.0
        idle_threshold = round(spb * 1.5)
        n = len(samples)
        results = []

        i = 0
        frame_num = 0
        while i < n:
            # Fast-forward through idle line (high = idle for non-inverted)
            if samples[i] == (0 if self.invert else 1):
                run_start = i
                while i < n and samples[i] == (0 if self.invert else 1):
                    i += 1
                continue

            # Found potential start bit (low for non-inverted)
            start_sample = i
            zero_run = 0
            while i < n and samples[i] == (1 if self.invert else 0):
                zero_run += 1
                i += 1

            # Must be low for at least half a bit period
            if zero_run < round(half_spb):
                continue

            # Sample data bits at bit centers.
            # Use cumulative integer sample counts to avoid drift from rounding.
            cumulative_samples = 0  # samples from start of byte
            data_bits = []

            # Start bit: samples 0 through round(spb)-1
            cumulative_samples += round(spb)

            for k in range(self.databits):
                sample_idx = start_sample + cumulative_samples + round(spb // 2)
                if sample_idx < n:
                    window = [samples[sample_idx + o]
                              for o in range(-3, 4) if 0 <= sample_idx + o < n]
                    vote = 1 if sum(window) >= len(window) / 2 else 0
                    data_bits.append(vote)
                else:
                    data_bits.append(1 if self.invert else 0)
                cumulative_samples += round(spb)

            # Assemble byte LSB-first
            byte_val = sum(data_bits[k] << k for k in range(self.databits))

            # Check stop bits
            stop_ok = True
            for sb in range(self.stopbits):
                stop_idx = start_sample + cumulative_samples + round(spb // 2)
                if stop_idx < n:
                    window = [samples[stop_idx + o]
                              for o in range(-3, 4) if 0 <= stop_idx + o < n]
                    stop_ok = sum(window) >= 4
                    if not stop_ok:
                        break
                cumulative_samples += round(spb)

            # Check parity if enabled
            parity_ok = True
            if self.parity not in ('N',):
                parity_bit = 1 if self.invert else 0
                parity_sample_idx = start_sample + cumulative_samples + round(spb // 2)
                if parity_sample_idx < n:
                    window = [samples[parity_sample_idx + o]
                              for o in range(-3, 4) if 0 <= parity_sample_idx + o < n]
                    parity_bit = 1 if sum(window) >= len(window) / 2 else 0
                parity_ok = self._parity_ok(byte_val, parity_bit, self.databits)

            # Build frame
            frame_ok = stop_ok and parity_ok
            if not frame_ok and stop_on_error:
                break

            frame = DecodedFrame(
                byte_value=byte_val,
                start_sample=start_sample,
                data_bits=data_bits,
                parity_ok=parity_ok if self.parity not in ('N',) else None,
                frame_ok=frame_ok,
            )
            results.append(frame)

            # Skip to end of frame to avoid double-decode
            # Use cumulative samples: start bit + data bits + stop bits
            total_bits = 1 + self.databits + self.stopbits
            if self.parity not in ('N',):
                total_bits += 1
            byte_samples = total_bits * round(spb)
            i = max(i, start_sample + byte_samples)
            frame_num += 1

        return results

    def decode_bytes(
        self,
        samples: list,
        sample_rate: int,
    ) -> list[int]:
        """
        Decode a sample stream, returning raw byte values.

        Args:
            samples: List of 0/1 sample values
            sample_rate: Sample rate in Hz

        Returns:
            List of decoded byte values
        """
        frames = self.decode_stream(samples, sample_rate)
        return [f.byte_value for f in frames]

    def decode_text(self, samples: list, sample_rate: int, max_chars: int = 200) -> str:
        """
        Decode a sample stream, returning printable text.

        Non-printable bytes are shown as [XX] hex.

        Args:
            samples: List of 0/1 sample values
            sample_rate: Sample rate in Hz
            max_chars: Maximum characters to return

        Returns:
            String with decoded bytes (printable chars or [XX] hex)
        """
        frames = self.decode_stream(samples, sample_rate)
        result = []
        for f in frames:
            result.append(self._fmt_byte(f.byte_value))
        return ''.join(result[:max_chars])

    def verify_pattern(
        self,
        samples: list,
        sample_rate: int,
        expected: list[int],
    ) -> tuple[bool, list[int], int]:
        """
        Verify that a sample stream contains expected byte values.

        Args:
            samples: List of 0/1 sample values
            sample_rate: Sample rate in Hz
            expected: List of expected byte values

        Returns:
            (match: bool, decoded: list[int], first_mismatch_index: int)
        """
        decoded = self.decode_bytes(samples, sample_rate)

        for i, (got, exp) in enumerate(zip(decoded, expected)):
            if got != exp:
                return False, decoded, i

        if len(decoded) < len(expected):
            return False, decoded, len(decoded)

        return True, decoded, -1


class Encoder:
    """
    Encode bytes into UART signal samples.
    Inverse of UARTDecoder.decode_stream().
    """

    def __init__(
        self,
        baud: int = 115200,
        databits: int = 8,
        parity: str = 'N',
        stopbits: int = 1,
    ):
        self.baud = baud
        self.databits = databits
        self.parity = parity.upper()
        self.stopbits = stopbits

    def encode_byte(
        self,
        byte_val: int,
        sample_rate: int,
    ) -> list[int]:
        """Generate sampled signal for one UART byte."""
        bit_us = 1_000_000.0 / self.baud
        sample_us = 1_000_000.0 / sample_rate
        spb = bit_us / sample_us

        signal = []

        # Start bit (0)
        for _ in range(round(spb)):
            signal.append(0)

        # Data bits LSB-first
        for bit in range(self.databits):
            val = (byte_val >> bit) & 1
            for _ in range(round(spb)):
                signal.append(val)

        # Parity bit
        if self.parity not in ('N',):
            if self.parity == 'E':
                ones = bin(byte_val & ((1 << self.databits) - 1)).count('1')
                parity_val = 1 if ones % 2 == 0 else 0
            elif self.parity == 'O':
                ones = bin(byte_val & ((1 << self.databits) - 1)).count('1')
                parity_val = 1 if ones % 2 == 1 else 0
            else:
                parity_val = 0
            for _ in range(round(spb)):
                signal.append(parity_val)

        # Stop bit(s) (1)
        for _ in range(round(spb) * self.stopbits):
            signal.append(1)

        return signal

    def encode_string(
        self,
        text: str,
        sample_rate: int,
        inter_byte_gap_samples: int = 0,
    ) -> list[int]:
        """Encode a string into sampled signal."""
        signal = []
        for i, ch in enumerate(text):
            signal.extend(self.encode_byte(ord(ch), sample_rate))
            if inter_byte_gap_samples > 0:
                signal.extend([1] * inter_byte_gap_samples)
        return signal

    def encode_pattern(
        self,
        pattern: list[int],
        sample_rate: int,
        inter_byte_gap_samples: int = 0,
    ) -> list[int]:
        """Encode a list of byte values into sampled signal."""
        signal = []
        for byte in pattern:
            signal.extend(self.encode_byte(byte, sample_rate))
            if inter_byte_gap_samples > 0:
                signal.extend([1] * inter_byte_gap_samples)
        return signal


def self_test() -> bool:
    """Verify encoder/decoder roundtrip for all 256 byte values."""
    print("=== UART Decoder Self-Test ===")

    decoder = UARTDecoder(baud=115200, databits=8)
    encoder = Encoder(baud=115200, databits=8)

    for rate_name, rate_hz in [('1M', 1_000_000), ('10M', 10_000_000), ('12M', 12_000_000)]:
        errors = 0
        for val in range(256):
            sig = encoder.encode_byte(val, rate_hz)
            decoded = decoder.decode_bytes(sig, rate_hz)
            if not decoded or decoded[0] != val:
                errors += 1
                if errors <= 3:
                    print(f"  FAIL {rate_name}: 0x{val:02X} -> {decoded[0] if decoded else 'EMPTY'}")

        status = "PASS" if errors == 0 else f"FAIL ({errors} errors)"
        print(f"  Roundtrip at {rate_name}: {status}")

    # Pattern test
    print("\n  Pattern test [0x55, 0xAA, 0xFF, 0x00] at 12MHz...")
    pattern = [0x55, 0xAA, 0xFF, 0x00]
    sig = encoder.encode_pattern(pattern, 12_000_000)
    decoded = decoder.decode_bytes(sig, 12_000_000)
    if decoded == pattern:
        print(f"  Pattern PASS ({len(sig)} samples -> {len(decoded)} bytes)")
    else:
        print(f"  Pattern FAIL: expected {pattern}, got {decoded}")
        return False

    # Counter 0-255
    print("\n  Counter test 0x00-0xFF at 12MHz...")
    counter = list(range(256))
    sig = encoder.encode_pattern(counter, 12_000_000)
    decoded = decoder.decode_bytes(sig, 12_000_000)
    if decoded == counter:
        print(f"  Counter PASS ({len(sig)} samples -> {len(decoded)} bytes)")
    else:
        print(f"  Counter FAIL: got {len(decoded)} bytes, first error at byte {next((i for i,(a,b) in enumerate(zip(decoded, counter)) if a != b), -1)}")
        return False

    return True


if __name__ == '__main__':
    import sys
    ok = self_test()
    print(f"\n  Overall: {'PASS' if ok else 'FAIL'}")
    sys.exit(0 if ok else 1)
