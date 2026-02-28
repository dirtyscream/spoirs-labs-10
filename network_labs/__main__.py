import sys

from network_labs.config import (
    ServerConfig, ClientConfig, FileConfig,
    ReliableUdpConfig, Lab4Config,
)
from network_labs.application.lab2 import Lab2Runner
from network_labs.application.lab3 import Lab3Runner
from network_labs.application.lab_mux import LabMuxRunner
from network_labs.application.lab4 import Lab4Runner
from network_labs.presentation.menu import run_menu


def parse_server_host() -> str:
    if len(sys.argv) > 1:
        return sys.argv[1]
    return "127.0.0.1"


def create_runners(server_host: str) -> dict:
    server_cfg = ServerConfig()
    client_cfg = ClientConfig(host=server_host)
    file_cfg = FileConfig()
    rudp_cfg = ReliableUdpConfig()
    lab4_cfg = Lab4Config(client_host=server_host)

    return {
        1: Lab2Runner(server_cfg, client_cfg, file_cfg),
        2: Lab3Runner(server_cfg, client_cfg, file_cfg, rudp_cfg),
        3: LabMuxRunner(server_cfg, client_cfg, file_cfg),
        4: Lab4Runner(lab4_cfg, file_cfg, rudp_cfg),
    }


def main() -> None:
    host = parse_server_host()
    runners = create_runners(host)
    try:
        run_menu(runners)
    except KeyboardInterrupt:
        print("\nExiting.")


if __name__ == "__main__":
    main()
