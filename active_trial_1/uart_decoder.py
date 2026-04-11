#!/usr/bin/env python3
"""
Embedded UART Decoder - Pure Python, no numpy needed
Decodes UART signal from binary sample stream (0/1 per bit).
"""
import sys

# ---- Bit-level UART Signal Generation ----

def samples_for_byte(byte, baud=115200, sample_rate=10_000_000):
    """Generate sampled signal for one byte: start(0) + 8 data LSB-first + stop(1)."""
    bit_us = 1_000_000.0 / baud
    sample_us = 1_000_000.0 / sample_rate
    spb = bit_us / sample_us  # samples per bit (float)

    signal = []
    # Start bit = 0
    for _ in range(round(spb)):
        signal.append(0)
    # Data bits LSB-first
    for bit in range(8):
        val = (byte >> bit) & 1
        for _ in range(round(spb)):
            signal.append(val)
    # Stop bit = 1
    for _ in range(round(spb)):
        signal.append(1)

    return signal


def generate_pattern(baud=115200, sample_rate=10_000_000):
    """Generate sampled signal for test patterns."""
    pattern = [0x55, 0xAA, 0xFF, 0x00]
    signal = []
    for byte in pattern:
        signal.extend(samples_for_byte(byte, baud, sample_rate))
    # Add idle line (1) for some time after
    for _ in range(round(sample_rate * 0.050)):  # 50ms idle
        signal.append(1)
    return signal


# ---- UART Signal Decoder ----

def decode_signal(samples, baud=115200, sample_rate=10_000_000):
    """
    Decode UART signal from list of 0/1 samples.
    Uses edge-based detection for start bits (more robust).
    Returns list of decoded bytes.
    """
    bit_us = 1_000_000.0 / baud
    sample_us = 1_000_000.0 / sample_rate
    spb = bit_us / sample_us  # samples per bit (float)

    results = []
    n = len(samples)
    i = 0

    # Precompute idle threshold: if line is high for >1.5 bit periods, we're idle
    idle_threshold = round(spb * 1.5)

    while i < n:
        # Fast-forward through idle line
        if samples[i] == 1:
            # Count consecutive 1s
            run_start = i
            while i < n and samples[i] == 1:
                i += 1
            run_len = i - run_start
            if run_len < idle_threshold:
                # Not a full idle period - might be stop bit followed by data
                pass
            continue

        # Found a 0 -> potential start bit
        # Verify it's a start bit: line must be 0 for at least spb/2 samples
        start_sample = i
        zero_run = 0
        while i < n and samples[i] == 0:
            zero_run += 1
            i += 1

        if zero_run < round(spb * 0.5):
            continue  # Too short to be a start bit

        # We have a valid start bit. Now sample at bit centers.
        # Bit 0 (start) center: start_sample + spb/2
        # Bit 1..8 (data) center: start_sample + (k + 0.5) * spb  for k=1..8
        # Stop bit center: start_sample + 9.5 * spb

        frame_data = []
        for k in range(1, 9):  # bits 1-8 (data)
            sample_idx = round(start_sample + (k + 0.5) * spb)
            if sample_idx < n:
                # Majority vote in a 7-sample window around the center
                window_vals = []
                for offset in range(-3, 4):
                    idx = sample_idx + offset
                    if 0 <= idx < n:
                        window_vals.append(samples[idx])
                vote = 1 if sum(window_vals) >= len(window_vals) / 2 else 0
                frame_data.append(vote)

        # Verify stop bit (bit 9)
        stop_idx = round(start_sample + 9.5 * spb)
        stop_ok = True
        if stop_idx < n:
            window_vals = []
            for offset in range(-3, 4):
                idx = stop_idx + offset
                if 0 <= idx < n:
                    window_vals.append(samples[idx])
            stop_ok = sum(window_vals) >= 4  # majority

        if stop_ok:
            # Assemble byte LSB-first
            byte_val = 0
            for bit_idx, bit in enumerate(frame_data):
                byte_val |= (bit << bit_idx)
            results.append(byte_val)

            # Skip past this frame: next byte starts at the exact byte boundary
            byte_samples = round(spb * 10)
            i = max(i, start_sample + byte_samples)

    return results


def fmt_byte(b):
    """Format byte for display."""
    if 32 <= b < 127:
        return f"'{chr(b)}'"
    names = {0: 'NUL', 13: 'CR', 10: 'LF', 27: 'ESC'}
    if b in names:
        return names[b]
    return f"0x{b:02X}"


# ---- Self-Verification ----

def test_roundtrip():
    """Verify encoder+decoder roundtrip for all 256 byte values."""
    print("=== UART Decoder Round-Trip Test ===")
    BAUD = 115200
    RATE = 10_000_000

    errors = 0
    for val in range(256):
        sig = samples_for_byte(val, BAUD, RATE)
        decoded = decode_signal(sig, BAUD, RATE)
        if not decoded or decoded[0] != val:
            errors += 1
            if errors <= 5:
                print(f"  FAIL: 0x{val:02X} -> {decoded[0] if decoded else 'EMPTY'}")

    if errors == 0:
        print("  All 256 bytes round-trip PASS")
    else:
        print(f"  {errors}/256 bytes FAIL")
        return False

    # Test pattern burst
    sig = generate_pattern(BAUD, RATE)
    decoded = decode_signal(sig, BAUD, RATE)
    expected = [0x55, 0xAA, 0xFF, 0x00]
    if decoded == expected:
        print(f"  Pattern burst [55 AA FF 00] PASS ({len(sig)} samples -> {len(decoded)} bytes)")
    else:
        print(f"  Pattern burst FAIL: expected {expected}, got {decoded}")
        return False

    # Test counter sequence
    print("\n  Counter sequence test (0x00-0xFF)...")
    counter_sig = []
    for val in range(256):
        counter_sig.extend(samples_for_byte(val, BAUD, RATE))
    counter_decoded = decode_signal(counter_sig, BAUD, RATE)
    if counter_decoded == list(range(256)):
        print(f"  Counter 0x00-0xFF PASS ({len(counter_sig)} samples -> {len(counter_decoded)} bytes)")
    else:
        # Find first mismatch
        for i, (exp, got) in enumerate(zip(range(256), counter_decoded)):
            if exp != got:
                print(f"  Counter mismatch at 0x{i:02X}: expected 0x{exp:02X}, got 0x{got:02X}")
                break
        return False

    return True


if __name__ == '__main__':
    ok = test_roundtrip()
    print(f"\n  Overall: {'PASS' if ok else 'FAIL'}")
    sys.exit(0 if ok else 1)
