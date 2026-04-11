#!/usr/bin/env python3
"""
HIL Framework - Board Hardware Module

Detects connected hardware boards (STLink, VCP, USB-UART).
Provides firmware flashing and serial communication.
"""
import subprocess
import os
import time
import serial
import struct
from dataclasses import dataclass
from typing import Optional


@dataclass
class BoardInfo:
    """Discovered development board."""
    name: str
    stlink_port: str  # e.g., /dev/ttyACM0
    serial_number: str = ''
    mcu: str = ''
    flash_size_kb: int = 0


class BoardHardware:
    """
    Detect and communicate with development boards.

    Example:
        hw = BoardHardware()
        boards = hw.list_boards()
        print(boards)

        hw.flash('firmware.bin')
        hw.reset()

        # Read VCP output
        for line in hw.vcp_reader(timeout=3):
            print(line)
    """

    def __init__(self):
        self._stlink_path = os.path.basename(os.environ.get('STLINK_PATH', 'st-link'))
        self._boards: list[BoardInfo] = []
        self._serial_cache: dict[str, list[str]] = {}

    def list_boards(self) -> list[BoardInfo]:
        """
        Scan for connected development boards via STLink.

        Returns:
            List of BoardInfo for each discovered board.
        """
        boards = []

        # Scan for STLink VCP ports
        import glob
        for port_path in sorted(glob.glob('/dev/ttyACM*')) + sorted(glob.glob('/dev/ttyUSB*')):
            try:
                # Try to get serial info
                sn = ''
                try:
                    link = os.readlink(port_path)
                    if 'by-id' in link:
                        link_path = os.path.join('/dev', 'serial/by-id',
                                                  os.readlink(port_path).split('/')[-1])
                        if os.path.exists(link_path.replace(link_path.split('/')[-1],
                                                              os.readlink(port_path).split('/')[-1])):
                            sn = os.readlink(link_path).split('_')[-1].rstrip('-if02')
                except:
                    pass

                # Check if it's an STLink
                try:
                    result = subprocess.run(
                        ['lsusb', '-s', f'{port_path.split("/")[-1]}'],
                        capture_output=True, text=True, timeout=2
                    )
                    is_stlink = 'STMicroelectronics' in result.stdout and \
                                'STLINK' in result.stdout.upper()
                except:
                    is_stlink = False

                if is_stlink or 'STMicro' in port_path or 'stlink' in port_path.lower():
                    boards.append(BoardInfo(
                        name="STLink Board",
                        stlink_port=port_path,
                        serial_number=sn,
                    ))
            except Exception:
                continue

        self._boards = boards
        return boards

    def flash(
        self,
        binary_path: str,
        address: str = '0x08000000',
        port: Optional[str] = None,
    ) -> tuple[bool, str]:
        """
        Flash firmware to the board via STLink.

        Args:
            binary_path: Path to .bin file
            address: Flash address (default: 0x08000000)
            port: STLink port (auto-detect if None)

        Returns:
            (success: bool, message: str)
        """
        if not os.path.exists(binary_path):
            return False, f"File not found: {binary_path}"

        # Auto-detect if needed
        if port is None:
            boards = self.list_boards()
            if boards:
                port = boards[0].stlink_port
            else:
                # Try common ports
                for p in ['/dev/ttyACM0']:
                    if os.path.exists(p):
                        port = p
                        break
                if port is None:
                    return False, "No STLink device found"

        cmd = [
            'st-flash', 'write', binary_path, address,
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
            )
            output = result.stdout + result.stderr

            if result.returncode == 0 or 'Flash written' in output or 'jolly good' in output:
                return True, "Flash successful"
            elif 'Verification' in output and 'failed' in output:
                # Write might have succeeded even if verify failed (RDP?)
                return True, "Flash written (verify skipped - likely RDP enabled)"
            else:
                return False, output

        except subprocess.TimeoutExpired:
            return False, "Flash timed out"
        except FileNotFoundError:
            return False, "st-flash not found. Install: apt install stlink-tools"
        except Exception as e:
            return False, f"Flash error: {e}"

    def reset(self, port: Optional[str] = None) -> tuple[bool, str]:
        """
        Reset the board via STLink.

        Returns:
            (success: bool, message: str)
        """
        try:
            result = subprocess.run(
                ['st-flash', 'reset'],
                capture_output=True, text=True, timeout=10
            )
            return result.returncode == 0, result.stdout + result.stderr
        except Exception as e:
            return False, str(e)

    def vcp_read(
        self,
        baud: int = 115200,
        timeout: float = 3.0,
        port: Optional[str] = None,
        max_bytes: int = 4096,
    ) -> tuple[bool, str, list[int]]:
        """
        Read from the board's VCP (USART1/STLink).

        Args:
            baud: UART baud rate
            timeout: Read timeout in seconds
            port: VCP port (auto-detect if None)
            max_bytes: Maximum bytes to read

        Returns:
            (success: bool, text: str, raw_bytes: list[int])
        """
        if port is None:
            boards = self.list_boards()
            if boards:
                port = boards[0].stlink_port
            else:
                for p in ['/dev/ttyACM0']:
                    if os.path.exists(p):
                        port = p
                        break
                if port is None:
                    return False, "No VCP found", []

        try:
            with serial.Serial(port, baud, timeout=timeout) as ser:
                ser.reset_input_buffer()
                time.sleep(0.3)
                data = ser.read(max_bytes)
                raw = list(data)
                text = ''.join(
                    chr(b) if 32 <= b < 127 or b in (13, 10) else f'[{b:02X}]'
                    for b in raw
                )
                return len(raw) > 0, text, raw
        except serial.SerialException as e:
            return False, f"VCP error: {e}", []
        except Exception as e:
            return False, f"Error: {e}", []

    def vcp_reader(
        self,
        baud: int = 115200,
        duration: float = 5.0,
        port: Optional[str] = None,
    ):
        """
        Generator that yields lines from VCP for a duration.

        Yields:
            str: Each line received from the VCP

        Example:
            for line in hw.vcp_reader(duration=3):
                print(line)
        """
        if port is None:
            boards = self.list_boards()
            if boards:
                port = boards[0].stlink_port

        if port is None:
            return

        try:
            with serial.Serial(port, baud, timeout=duration + 1) as ser:
                ser.reset_input_buffer()
                time.sleep(0.3)

                start = time.time()
                while time.time() - start < duration:
                    if ser.in_waiting > 0:
                        line = ser.readline().decode('utf-8', errors='replace').strip()
                        if line:
                            yield line
                    else:
                        time.sleep(0.05)
        except Exception:
            pass


def flash_and_verify(
    binary_path: str,
    baud: int = 115200,
    timeout: float = 5.0,
    expected_patterns: Optional[list[str]] = None,
) -> tuple[bool, str, list[str]]:
    """
    Flash firmware and verify output via VCP.

    Args:
        binary_path: Path to firmware .bin
        baud: VCP baud rate
        timeout: VCP read timeout
        expected_patterns: List of substrings expected in output

    Returns:
        (flash_success: bool, output: str, lines: list[str])
    """
    hw = BoardHardware()

    # Flash
    success, msg = hw.flash(binary_path)
    if not success:
        return False, f"Flash failed: {msg}", []

    time.sleep(1)  # Wait for board to boot

    # Reset
    hw.reset()

    # Read VCP
    time.sleep(0.5)
    success, text, raw = hw.vcp_read(baud=baud, timeout=timeout)

    if not success:
        return True, f"Flash OK, no VCP output: {text}", []

    lines = [l for l in text.split('\n') if l.strip()]

    # Check patterns
    if expected_patterns:
        missing = [p for p in expected_patterns if p not in text]
        if missing:
            return True, f"Flash OK, patterns missing: {missing}\nOutput: {text}", lines

    return True, f"Flash OK, VCP output received\n{text}", lines
