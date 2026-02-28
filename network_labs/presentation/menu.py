from typing import Dict

from network_labs.domain.interfaces import LabRunner

LABS = {
    1: "TCP Commands + File Transfer (ECHO, TIME, UPLOAD, DOWNLOAD, CLOSE)",
    2: "Reliable UDP (commands + file transfer over UDP)",
    3: "Multiplexing TCP (select/poll, multiple clients)",
    4: "UDP + Dynamic Thread Pool",
}


def display_header() -> None:
    print("\nNetwork Labs")
    print("============", flush=True)


def select_lab() -> int:
    print("Select lab:")
    for num, desc in LABS.items():
        print(f"  {num}. {desc}")
    print(flush=True)
    while True:
        try:
            choice = int(input("Enter lab number (1-4): "))
            if choice in LABS:
                return choice
        except (ValueError, EOFError):
            pass
        print("Invalid choice. Enter 1-4.")


def select_mode() -> str:
    print("\nSelect mode:")
    print("  c - Client")
    print("  s - Server")
    print(flush=True)
    while True:
        try:
            choice = input("Enter mode (c/s): ").strip().lower()
            if choice in ("c", "s"):
                return choice
        except (EOFError, KeyboardInterrupt):
            raise SystemExit()
        print("Invalid choice. Enter 'c' or 's'.")


def run_menu(runners: Dict[int, LabRunner]) -> None:
    display_header()
    lab_num = select_lab()
    mode = select_mode()
    runner = runners[lab_num]
    print(flush=True)
    if mode == "s":
        runner.run_server()
    else:
        runner.run_client()
