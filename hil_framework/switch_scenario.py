#!/usr/bin/env python3
"""
switch_scenario.py — Apply a saved scenario to hil_workspace and build it.

Usage:
    python3 hil_framework/switch_scenario.py accel_stream   # ISM330DHCX
    python3 hil_framework/switch_scenario.py temperature    # HTS221

Then flash manually or via auto_hil.py:
    python3 hil_framework/auto_hil.py --scenario <name>

Scenarios are in hil_framework/scenarios/<name>/
"""
import sys, re, shutil, pathlib, subprocess

WORKSPACE = "/home/kerem/logic_analyzer/hil_workspace"
SCENARIOS = "/home/kerem/logic_analyzer/hil_framework/scenarios"
MAIN_C    = f"{WORKSPACE}/Core/Src/main.c"


def find_block_lines(text, block_name):
    """Return (start_line, end_line) for a USER CODE block by block name."""
    lines = text.split('\n')
    start = end = -1
    for i, line in enumerate(lines):
        if f'/* USER CODE BEGIN {block_name} */' in line:
            start = i
        elif start >= 0 and f'/* USER CODE END {block_name} */' in line:
            end = i
            break
    return start, end


def patch_block(main_text, block_name, new_code):
    """Replace the content of a USER CODE block by line positions."""
    start, end = find_block_lines(main_text, block_name)
    if start < 0 or end < 0:
        print(f"  [WARN] Block '{block_name}' not found in main.c")
        return main_text
    lines = main_text.split('\n')
    new_lines = lines[:start + 1]          # keep opening marker
    new_lines.append(new_code.strip())    # insert new content
    new_lines.append('')                  # blank line
    new_lines.extend(lines[end:])         # keep closing marker and rest
    return '\n'.join(new_lines)


def apply_scenario(name):
    scenario_dir = pathlib.Path(SCENARIOS) / name
    if not scenario_dir.exists():
        print(f"[ERROR] Scenario '{name}' not found in {SCENARIOS}/")
        print(f"Available: {[p.name for p in pathlib.Path(SCENARIOS).iterdir() if p.is_dir()]}")
        sys.exit(1)

    # Read scenario blocks
    blocks = {}
    for block in ["PV", "2", "3"]:
        f = scenario_dir / f"USER_CODE_{block}.c"
        if f.exists():
            # Strip the /* USER CODE ... */ wrapper lines
            content = f.read_text()
            content = re.sub(r"^\/\* USER CODE [^\n]+\*\/\n?", "", content)
            content = re.sub(r"\n?\/\* USER CODE END [^\n]+\*\/", "", content)
            blocks[block] = content

    # Read main.c
    main_text = MAIN_C
    with open(MAIN_C) as f:
        main_text = f.read()

    # Apply each block
    for block, content in blocks.items():
        main_text = patch_block(main_text, block, content)

    # Update Includes if PV block has new includes
    if "PV" in blocks:
        inc_match = re.search(r"\/\* USER CODE BEGIN Includes \*\/\n([\s\S]*?)\n/\* USER CODE END Includes \*/", main_text)
        pv_inc = re.search(r"\/\* USER CODE BEGIN Includes \*\/\n([\s\S]*?)\n/\* USER CODE END Includes \*/", blocks["PV"])
        if inc_match and pv_inc:
            main_text = main_text.replace(inc_match.group(0), pv_inc.group(0))

    # Write main.c
    with open(MAIN_C, "w") as f:
        f.write(main_text)

    print(f"[OK] Applied scenario '{name}' to {MAIN_C}")

    # Build
    print("[BUILD] Compiling...")
    result = subprocess.run(
        ["make", "-C", f"{WORKSPACE}/Debug", "all"],
        capture_output=True, text=True, timeout=120
    )
    if result.returncode != 0:
        print(f"[ERROR] Build failed:\n{result.stderr[-500:]}")
        sys.exit(1)
    print(f"[OK] Build complete: {WORKSPACE}/Debug/Logic_Analyzer_USART3.bin")
    print(f"\nNext: python3 hil_framework/auto_hil.py --scenario {name}")
    print(f"  Or flash manually: st-flash erase 0x8000000 0x200000 && st-flash --reset write {WORKSPACE}/Debug/Logic_Analyzer_USART3.bin 0x8000000")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    apply_scenario(sys.argv[1])
