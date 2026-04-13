"""
HIL Framework - Hardware-In-the-Loop Testing for Embedded Firmware

Usage:
    from hil_framework import HILTest, LogicAnalyzerCapture, UARTDecoder

    # Quick test
    test = HILTest()
    test.capture(duration_s=2, sample_rate='12M', channel=0)
    test.decode_uart(baud=115200)
    result = test.validate_patterns(['[0x55]', '[0xAA]', '[0xFF]'])
    print(result)

Co-Pilot:
    from hil_framework import copilot
    # Run: ANTHROPIC_API_KEY=... python3 -m hil_framework.copilot
"""
from .capture import LogicAnalyzerCapture
from .decoder import UARTDecoder
from .validator import TestValidator
from .hardware import BoardHardware
from . import copilot

__all__ = [
    'LogicAnalyzerCapture',
    'UARTDecoder',
    'TestValidator',
    'BoardHardware',
    'copilot',
]
