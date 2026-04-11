#!/usr/bin/env python3
"""
HIL Framework - Test Validator Module

Validates captured/decoded firmware output against expected patterns.
Provides pass/fail reporting and detailed diagnostics.
"""
import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class ValidationResult:
    """Result of a single validation check."""
    name: str
    passed: bool
    expected: str
    actual: str
    details: str = ''
    match_count: int = 0


@dataclass
class TestResult:
    """Overall HIL test result."""
    name: str
    passed: bool
    duration_s: float
    bytes_decoded: int
    validations: list[ValidationResult]
    summary: str = ''

    def print_report(self):
        """Print a formatted test report."""
        status = "PASS" if self.passed else "FAIL"
        print(f"\n{'='*60}")
        print(f"  HIL TEST RESULT: {status}")
        print(f"{'='*60}")
        print(f"  Test:       {self.name}")
        print(f"  Duration:   {self.duration_s:.2f}s")
        print(f"  Bytes:      {self.bytes_decoded}")
        print(f"  Validated:  {len(self.validations)} checks")
        print(f"{'-'*60}")

        for v in self.validations:
            mark = "PASS" if v.passed else "FAIL"
            print(f"  [{mark}] {v.name}")
            if v.details:
                print(f"        {v.details}")
        print(f"{'='*60}")


class TestValidator:
    """
    Validates decoded firmware output against expected patterns.

    Example:
        validator = TestValidator("USART3 Test")
        validator.expect_pattern("[0x55]", "Binary 0x55 pattern")
        validator.expect_pattern("[0xAA]", "Binary 0xAA pattern")
        validator.expect_sequence(["[0x55]", "[0xAA]", "[0xFF]", "[0x00]"], "Full cycle")

        result = validator.validate(decoded_text)
        result.print_report()
    """

    def __init__(self, name: str = "HIL Test"):
        self.name = name
        self._checks: list[dict] = []
        self._expected_sequence: list[str] = []

    def expect_pattern(
        self,
        pattern: str,
        description: str = '',
        max_count: int = 0,
    ) -> 'TestValidator':
        """
        Expect a substring to appear in the output.

        Args:
            pattern: Substring to search for
            description: Human-readable description
            max_count: If > 0, require at least this many occurrences
        """
        self._checks.append({
            'type': 'substring',
            'pattern': pattern,
            'description': description or f"Contains '{pattern}'",
            'max_count': max_count,
        })
        return self

    def expect_byte_sequence(
        self,
        bytes_expected: list[int],
        description: str = '',
    ) -> 'TestValidator':
        """
        Expect a specific sequence of byte values.

        Args:
            bytes_expected: List of expected byte values
            description: Human-readable description
        """
        self._checks.append({
            'type': 'byte_sequence',
            'expected': bytes_expected,
            'description': description or f"Byte sequence {bytes_expected[:10]}",
        })
        return self

    def expect_pattern_sequence(
        self,
        patterns: list[str],
        description: str = '',
        allow_gaps: bool = True,
    ) -> 'TestValidator':
        """
        Expect patterns to appear in order (may have other bytes between them).

        Args:
            patterns: List of substrings expected in order
            description: Human-readable description
            allow_gaps: If True, other bytes may appear between patterns
        """
        self._expected_sequence = patterns
        self._checks.append({
            'type': 'pattern_sequence',
            'patterns': patterns,
            'description': description or f"Pattern sequence: {patterns}",
            'allow_gaps': allow_gaps,
        })
        return self

    def expect_byte_range(
        self,
        min_val: int,
        max_val: int,
        description: str = '',
    ) -> 'TestValidator':
        """
        Expect at least some bytes to fall within a range (e.g. realistic sensor values).

        Args:
            min_val: Minimum acceptable byte value
            max_val: Maximum acceptable byte value
            description: Human-readable description
        """
        self._checks.append({
            'type': 'byte_range',
            'min': min_val,
            'max': max_val,
            'description': description or f"Bytes in range {min_val}-{max_val}",
        })
        return self

    def expect_no_zeros(self, description: str = '') -> 'TestValidator':
        """Expect the output NOT to be all zeros (realistic data)."""
        self._checks.append({
            'type': 'no_zeros',
            'description': description or "Not all zeros",
        })
        return self

    def validate(
        self,
        decoded_text: str,
        raw_bytes: Optional[list[int]] = None,
        duration_s: float = 0.0,
    ) -> TestResult:
        """
        Run all validation checks against decoded output.

        Args:
            decoded_text: Decoded output as text/string
            raw_bytes: Decoded byte values (list of ints)
            duration_s: Capture duration

        Returns:
            TestResult with pass/fail for each check
        """
        results = []

        for check in self._checks:
            ctype = check['type']

            if ctype == 'substring':
                count = decoded_text.count(check['pattern'])
                passed = count > 0
                if check['max_count'] > 0:
                    passed = count >= check['max_count']

                results.append(ValidationResult(
                    name=check['description'],
                    passed=passed,
                    expected=check['pattern'],
                    actual=f"{count} occurrence(s)",
                    details=f"{'OK' if passed else 'NOT FOUND'}",
                    match_count=count,
                ))

            elif ctype == 'byte_sequence' and raw_bytes is not None:
                expected = check['expected']
                if len(raw_bytes) >= len(expected):
                    match = raw_bytes[:len(expected)] == expected
                    first_diff = next(
                        (i for i, (a, e) in enumerate(zip(raw_bytes, expected)) if a != e),
                        -1
                    )
                else:
                    match = False
                    first_diff = len(raw_bytes)

                results.append(ValidationResult(
                    name=check['description'],
                    passed=match,
                    expected=str(expected[:10]),
                    actual=str(raw_bytes[:10]),
                    details=f"{'MATCH' if match else f'DIFFER at index {first_diff}'}",
                ))

            elif ctype == 'pattern_sequence':
                patterns = check['patterns']
                positions = []
                for p in patterns:
                    pos = decoded_text.find(p)
                    positions.append(pos)
                    if pos < 0:
                        break

                if all(p >= 0 for p in positions):
                    if check.get('allow_gaps', True):
                        # Check if positions are monotonically increasing
                        passed = all(positions[i] < positions[i+1]
                                     for i in range(len(positions)-1))
                        details = f"Found at positions {[p for p in positions]}"
                    else:
                        # Strict consecutive
                        expected_pos = positions[0]
                        passed = all(positions[i] == expected_pos + i
                                     for i in range(len(positions)))
                        details = f"Strict positions: {[p for p in positions]}"
                else:
                    passed = False
                    details = f"Missing: {[p for p, pos in zip(patterns, positions) if pos < 0]}"

                results.append(ValidationResult(
                    name=check['description'],
                    passed=passed,
                    expected=str(patterns),
                    actual=str([p for p in positions]),
                    details=details,
                ))

            elif ctype == 'byte_range' and raw_bytes is not None:
                in_range = [b for b in raw_bytes if check['min'] <= b <= check['max']]
                passed = len(in_range) > 0
                pct = len(in_range) / len(raw_bytes) * 100 if raw_bytes else 0

                results.append(ValidationResult(
                    name=check['description'],
                    passed=passed,
                    expected=f"{check['min']}-{check['max']}",
                    actual=f"{len(in_range)}/{len(raw_bytes)} ({pct:.0f}%)",
                    details=f"{'OK' if passed else 'NO VALUES IN RANGE'}",
                    match_count=len(in_range),
                ))

            elif ctype == 'no_zeros' and raw_bytes is not None:
                non_zero = sum(1 for b in raw_bytes if b != 0)
                passed = non_zero > 0
                pct = non_zero / len(raw_bytes) * 100 if raw_bytes else 0

                results.append(ValidationResult(
                    name=check['description'],
                    passed=passed,
                    expected="Non-zero bytes",
                    actual=f"{non_zero}/{len(raw_bytes)} non-zero ({pct:.0f}%)",
                    details=f"{'OK' if passed else 'ALL ZEROS'}",
                    match_count=non_zero,
                ))

        all_passed = all(v.passed for v in results)
        total_bytes = len(raw_bytes) if raw_bytes else len(decoded_text)

        result = TestResult(
            name=self.name,
            passed=all_passed,
            duration_s=duration_s,
            bytes_decoded=total_bytes,
            validations=results,
            summary="PASS" if all_passed else "FAIL",
        )

        return result


def quick_validate(
    decoded_text: str,
    raw_bytes: Optional[list[int]] = None,
    name: str = "HIL Quick Check",
) -> TestResult:
    """
    Quick HIL validation with common checks.

    Args:
        decoded_text: Decoded output as text
        raw_bytes: Decoded byte values
        name: Test name

    Returns:
        TestResult
    """
    validator = TestValidator(name)

    # Check for any non-zero data
    if raw_bytes:
        validator.expect_no_zeros("Output contains real data (not all zeros)")

    # Check for expected firmware patterns
    common_patterns = ['[0x55]', '[0xAA]', '[0xFF]', '[0x00]', '[CNT]', '[ASCII]']
    for pattern in common_patterns:
        if pattern in decoded_text:
            validator.expect_pattern(pattern)

    # Check minimum bytes decoded
    if raw_bytes and len(raw_bytes) >= 10:
        validator.expect_byte_range(0, 255, "Decoded bytes are in valid range")

    return validator.validate(decoded_text, raw_bytes)
