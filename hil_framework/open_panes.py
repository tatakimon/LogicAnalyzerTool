#!/usr/bin/env python3
"""
open_panes.py — Open Pane 2 and Pane 3 as tmux panes.
Run this script, then:  tmux attach -t hil
"""
import subprocess

P2 = "/home/kerem/logic_analyzer/hil_framework/.pane2_output"
P3 = "/home/kerem/logic_analyzer/hil_framework/.pane3_output"


def open_panes():
    subprocess.run(["tmux", "kill-session", "-t", "hil"],
                   capture_output=True)

    cmds = [
        # Step 1: start session with Pane 2 (VCP, bottom, full width)
        ["tmux", "new-session", "-d", "-s", "hil",
         "-x", "80", "-y", "24"],
        ["tmux", "send-keys", "-t", "hil:0.0",
         f"tail -f {P3}", "C-m"],

        # Step 2: split vertically — adds Pane 1 (logic, top-right) above
        ["tmux", "split-window", "-v", "-t", "hil:0.0"],
        ["tmux", "send-keys", "-t", "hil:0.1",
         f"tail -f {P2}", "C-m"],

        # Step 3: split Pane 1 horizontally — Pane 0 (left, idle) + Pane 1 (right, logic)
        ["tmux", "split-window", "-h", "-t", "hil:0.1"],
    ]

    print("[INFO] Creating tmux session 'hil'...")
    print("       Layout: [idle][logic] / [vcp]")
    print()
    for cmd in cmds:
        subprocess.run(cmd, capture_output=True)

    print("       Pane 0 (bottom, left):   tail -f .pane3_output  (VCP)")
    print("       Pane 1 (top, left):     idle")
    print("       Pane 2 (top, right):    tail -f .pane2_output  (Logic Analyzer)")
    print()
    print("  Attach:  tmux attach -t hil")
    print("  Detach:  Ctrl+b d")
    print("  Zoom:    Ctrl+b z   (while in a pane)")
    print("  Kill:    tmux kill-session -t hil")
    print()
    print("[OK] Session running — attach with: tmux attach -t hil")


if __name__ == "__main__":
    open_panes()