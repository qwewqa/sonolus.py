import argparse
import contextlib
import http.server
import importlib
import json
import shutil
import socket
import socketserver
import sys
from pathlib import Path
from time import perf_counter

from sonolus.backend.optimize.optimize import FAST_PASSES, MINIMAL_PASSES, STANDARD_PASSES
from sonolus.build.engine import no_gil, package_engine
from sonolus.build.level import package_level_data
from sonolus.build.project import build_project_to_collection, get_project_schema
from sonolus.script.project import BuildConfig, Project


def find_default_module() -> str | None:
    current_dir = Path.cwd()

    potential_modules = []

    project_files = list(current_dir.glob("*/project.py"))
    potential_modules.extend(str(f.parent.relative_to(current_dir)).replace("/", ".") for f in project_files)

    init_files = list(current_dir.glob("*/__init__.py"))
    potential_modules.extend(str(f.parent.relative_to(current_dir)).replace("/", ".") for f in init_files)

    potential_modules = [m for m in set(potential_modules) if m]

    return potential_modules[0] if len(potential_modules) == 1 else None


def import_project(module_path: str) -> Project | None:
    try:
        current_dir = Path.cwd()
        if current_dir not in sys.path:
            sys.path.insert(0, str(current_dir))

        project = None

        try:
            module = importlib.import_module(module_path)
            project = getattr(module, "project", None)
        except ImportError as e:
            if not str(e).endswith(f"'{module_path}'"):
                # It's an error from the module itself
                raise

        if project is None:
            try:
                project_module = importlib.import_module(f"{module_path}.project")
                project = getattr(project_module, "project", None)
            except ImportError as e:
                if not str(e).endswith(f"'{module_path}.project'"):
                    raise

        if project is None:
            print(f"Error: No Project instance found in module {module_path} or {module_path}.project")
            return None

        return project
    except Exception as e:
        print(f"Error: Failed to import project: {e}")
        raise e from None


def build_project(project: Project, build_dir: Path, config: BuildConfig):
    dist_dir = build_dir / "dist"
    levels_dir = dist_dir / "levels"
    shutil.rmtree(dist_dir, ignore_errors=True)
    dist_dir.mkdir(parents=True, exist_ok=True)
    levels_dir.mkdir(parents=True, exist_ok=True)

    package_engine(project.engine.data, config).write(dist_dir / "engine")

    for level in project.levels:
        level_path = levels_dir / level.name
        level_path.write_bytes(package_level_data(level.data))


def build_collection(project: Project, build_dir: Path, config: BuildConfig):
    site_dir = build_dir / "site"
    shutil.rmtree(site_dir, ignore_errors=True)
    site_dir.mkdir(parents=True, exist_ok=True)

    collection = build_project_to_collection(project, config)
    collection.write(site_dir)


def get_local_ips():
    hostname = socket.gethostname()
    local_ips = []

    with contextlib.suppress(socket.gaierror):
        local_ips.append(socket.gethostbyname(socket.getfqdn()))

    try:
        for info in socket.getaddrinfo(hostname, None):
            ip = info[4][0]
            if not ip.startswith("127.") and ":" not in ip:
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
        for ip in local_ips:
            print(f"  http://{ip}:{port}")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nStopping server...")
            httpd.shutdown()


def get_config(args: argparse.Namespace) -> BuildConfig:
    if hasattr(args, "optimize_minimal") and args.optimize_minimal:
        optimization_passes = MINIMAL_PASSES
    elif hasattr(args, "optimize_fast") and args.optimize_fast:
        optimization_passes = FAST_PASSES
    elif hasattr(args, "optimize_standard") and args.optimize_standard:
        optimization_passes = STANDARD_PASSES
    else:
        optimization_passes = FAST_PASSES if args.command == "dev" else STANDARD_PASSES

    if any(hasattr(args, attr) and getattr(args, attr) for attr in ["play", "watch", "preview", "tutorial"]):
        build_play = hasattr(args, "play") and args.play
        build_watch = hasattr(args, "watch") and args.watch
        build_preview = hasattr(args, "preview") and args.preview
        build_tutorial = hasattr(args, "tutorial") and args.tutorial
    else:
        build_play = build_watch = build_preview = build_tutorial = True

    return BuildConfig(
        passes=optimization_passes,
        build_play=build_play,
        build_watch=build_watch,
        build_preview=build_preview,
        build_tutorial=build_tutorial,
    )


def main():
    parser = argparse.ArgumentParser(description="Sonolus project build and development tools")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_common_arguments(parser):
        optimization_group = parser.add_mutually_exclusive_group()
        optimization_group.add_argument(
            "-o0", "--optimize-minimal", action="store_true", help="Use minimal optimization passes"
        )
        optimization_group.add_argument(
            "-o1", "--optimize-fast", action="store_true", help="Use fast optimization passes"
        )
        optimization_group.add_argument(
            "-o2", "--optimize-standard", action="store_true", help="Use standard optimization passes"
        )

        build_components = parser.add_argument_group("build components")
        build_components.add_argument("--play", action="store_true", help="Build play component")
        build_components.add_argument("--watch", action="store_true", help="Build watch component")
        build_components.add_argument("--preview", action="store_true", help="Build preview component")
        build_components.add_argument("--tutorial", action="store_true", help="Build tutorial component")

    build_parser = subparsers.add_parser("build")
    build_parser.add_argument(
        "module",
        type=str,
        nargs="?",
        help="Module path (e.g., 'module.name'). If omitted, will auto-detect if only one module exists.",
    )
    build_parser.add_argument("--build-dir", type=str, default="./build")
    add_common_arguments(build_parser)

    dev_parser = subparsers.add_parser("dev")
    dev_parser.add_argument(
        "module",
        type=str,
        nargs="?",
        help="Module path (e.g., 'module.name'). If omitted, will auto-detect if only one module exists.",
    )
    dev_parser.add_argument("--build-dir", type=str, default="./build")
    dev_parser.add_argument("--port", type=int, default=8000)
    add_common_arguments(dev_parser)

    schema_parser = subparsers.add_parser("schema")
    schema_parser.add_argument(
        "module",
        type=str,
        nargs="?",
        help="Module path (e.g., 'module.name'). If omitted, will auto-detect if only one module exists.",
    )

    args = parser.parse_args()

    if not args.module:
        default_module = find_default_module()
        if default_module:
            if args.command != "schema":
                print(f"Using auto-detected module: {default_module}")
            args.module = default_module
        else:
            parser.error("Module argument is required when multiple or no modules are found")

    if no_gil():
        print("Multithreading is enabled")
    if hasattr(sys, "_jit") and sys._jit.is_enabled():
        print("Python JIT is enabled")

    project = import_project(args.module)
    if project is None:
        sys.exit(1)

    if args.command == "build":
        build_dir = Path(args.build_dir)
        start_time = perf_counter()
        config = get_config(args)
        build_project(project, build_dir, config)
        end_time = perf_counter()
        print(f"Project built successfully to '{build_dir.resolve()}' in {end_time - start_time:.2f}s")
    elif args.command == "dev":
        build_dir = Path(args.build_dir)
        start_time = perf_counter()
        config = get_config(args)
        build_collection(project, build_dir, config)
        end_time = perf_counter()
        print(f"Build finished in {end_time - start_time:.2f}s")
        run_server(build_dir / "site", port=args.port)
    elif args.command == "schema":
        print(json.dumps(get_project_schema(project), indent=2))
