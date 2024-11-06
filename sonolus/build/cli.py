import argparse
import contextlib
import http.server
import socket
import socketserver
from pathlib import Path

from sonolus.build.engine import package_engine
from sonolus.build.level import package_level_data
from sonolus.build.project import build_project_to_collection
from sonolus.script.project import Project


def run_cli(project: Project):
    parser = argparse.ArgumentParser(description="Sonolus project build and development tools")
    subparsers = parser.add_subparsers(dest="command", required=True)

    build_parser = subparsers.add_parser("build", help="Build the Sonolus project")
    build_parser.add_argument(
        "--build-dir", type=str, default="./build", help="Directory to output built files (default: ./build)"
    )

    dev_parser = subparsers.add_parser("dev", help="Start development server")
    dev_parser.add_argument(
        "--build-dir", type=str, default="./build", help="Directory to serve files from (default: ./build)"
    )
    dev_parser.add_argument("--port", type=int, default=8000, help="Port to run the server on (default: 8000)")

    args = parser.parse_args()
    build_dir = Path(args.build_dir)

    if args.command == "build":
        build_project(project, build_dir)
        build_collection(project, build_dir)
        print(f"Project built successfully in {build_dir}")
    elif args.command == "dev":
        build_collection(project, build_dir)
        print(f"Collection built in {build_dir}/site")
        run_server(build_dir / "site", port=args.port)


def build_project(project: Project, build_dir: Path):
    dist_dir = build_dir / "dist"
    levels_dir = dist_dir / "levels"
    dist_dir.mkdir(parents=True, exist_ok=True)
    levels_dir.mkdir(parents=True, exist_ok=True)

    package_engine(project.engine.data).write(dist_dir / "engine")

    for level in project.levels:
        level_path = levels_dir / level.name
        level_path.write_bytes(package_level_data(level.data))


def build_collection(project: Project, build_dir: Path):
    site_dir = build_dir / "site"
    site_dir.mkdir(parents=True, exist_ok=True)

    collection = build_project_to_collection(project)
    collection.write(site_dir)


def get_local_ips():
    hostname = socket.gethostname()
    local_ips = []

    with contextlib.suppress(socket.gaierror):
        local_ips.append(socket.gethostbyname(socket.getfqdn()))

    try:
        for info in socket.getaddrinfo(hostname, None):
            ip = info[4][0]
            if not ip.startswith("127."):
                local_ips.append(ip)
    except socket.gaierror:
        pass

    return sorted(set(local_ips))


def run_server(base_dir: Path, port: int = 8000):
    class DirectoryHandler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(base_dir), **kwargs)

    with socketserver.TCPServer(("", port), DirectoryHandler) as httpd:
        local_ips = get_local_ips()
        print(f"Server started on port {port}")
        print("Available on:")
        print(f"  http://localhost:{port}")
        for ip in local_ips:
            print(f"  http://{ip}:{port}")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nStopping server...")
            httpd.shutdown()
