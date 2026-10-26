#!/usr/bin/env python3
"""DCSD logger that survives USB re-enumeration for one boot command."""

from __future__ import annotations

import argparse
import os
import select
import signal
import termios
import time
from pathlib import Path


running = True


def stop(_signum: int, _frame: object) -> None:
    global running
    running = False


def configure(fd: int) -> None:
    attributes = termios.tcgetattr(fd)
    attributes[0] = 0
    attributes[1] = 0
    attributes[2] = termios.CS8 | termios.CLOCAL | termios.CREAD
    attributes[3] = 0
    attributes[4] = termios.B115200
    attributes[5] = termios.B115200
    attributes[6][termios.VMIN] = 1
    attributes[6][termios.VTIME] = 0
    termios.tcsetattr(fd, termios.TCSANOW, attributes)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", type=Path, default=Path("/dev/ttyACM0"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ready-file", type=Path, required=True)
    parser.add_argument("--parent-pid", type=int, help="exit when the owning boot command exits")
    args = parser.parse_args()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.ready_file.unlink(missing_ok=True)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    def owner_alive() -> bool:
        return args.parent_pid is None or os.getppid() == args.parent_pid

    def clear_ready() -> None:
        try:
            if args.ready_file.read_text().strip() == str(os.getpid()):
                args.ready_file.unlink(missing_ok=True)
        except FileNotFoundError:
            pass

    try:
        with args.output.open("ab", buffering=0) as output:
            while running and owner_alive():
                fd = None
                try:
                    fd = os.open(args.device, os.O_RDONLY | os.O_NOCTTY | os.O_NONBLOCK)
                    configure(fd)
                    args.ready_file.write_text(f"{os.getpid()}\n")
                    while running and owner_alive():
                        readable, _, _ = select.select([fd], [], [], 1)
                        if not readable:
                            continue
                        chunk = os.read(fd, 65536)
                        if not chunk:
                            raise OSError("DCSD device closed")
                        output.write(chunk)
                except (FileNotFoundError, OSError, termios.error):
                    clear_ready()
                    if running and owner_alive():
                        time.sleep(0.25)
                finally:
                    if fd is not None:
                        os.close(fd)
    finally:
        clear_ready()


if __name__ == "__main__":
    main()
