from __future__ import annotations

import argparse
import contextlib
import http.server
import importlib
import queue
import shlex
import socket
import socketserver
import sys
import textwrap
import threading
import traceback
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING, Protocol

from sonolus.backend.excepthook import print_simple_traceback
from sonolus.backend.utils import get_function, get_functions, get_tree_from_file
from sonolus.build.compile import CompileCache
from sonolus.script.internal.error import CompilationError

if TYPE_CHECKING:
    from sonolus.script.project import BuildConfig

HELP_TEXT = """
[r]ebuild
[q]uit
""".strip()

HELP_TEXT = textwrap.dedent(HELP_TEXT)


class Command(Protocol):
    def execute(
        self,
        project_module_name: str,
        core_module_names: set[str],
        build_dir: Path,
        config: BuildConfig,
        cache: CompileCache,
    ) -> None: ...


@dataclass
class RebuildCommand:
    def execute(
        self,
        project_module_name: str,
        core_module_names: set[str],
        build_dir: Path,
        config: BuildConfig,
        cache: CompileCache,
    ):
        from sonolus.build.cli import build_collection

        for module_name in tuple(sys.modules):
            if module_name not in core_module_names:
                del sys.modules[module_name]

        try:
            project_module = importlib.import_module(project_module_name)
        except Exception:
            print(traceback.format_exc())
            return

        get_function.cache_clear()
        get_tree_from_file.cache_clear()
        get_functions.cache_clear()
        print("Rebuilding...")
        try:
            start_time = perf_counter()
            build_collection(project_module.project, build_dir, config, cache=cache)
            end_time = perf_counter()
            print(f"Rebuild completed in {end_time - start_time:.2f} seconds")
        except CompilationError:
            exc_info = sys.exc_info()
            print_simple_traceback(*exc_info)


@dataclass
class ExitCommand:
    def execute(
        self,
        project_module_name: str,
        core_module_names: set[str],
        build_dir: Path,
        config: BuildConfig,
        cache: CompileCache,
    ):
        print("Exiting...")
        sys.exit(0)


def parse_dev_command(command_line: str) -> Command | None:
    parser = argparse.ArgumentParser(prog="", add_help=False, exit_on_error=False)
    subparsers = parser.add_subparsers(dest="cmd")

    subparsers.add_parser("rebuild", aliases=["r"])
    subparsers.add_parser("quit", aliases=["q"])

    try:
        args = parser.parse_args(shlex.split(command_line))
        if args.cmd in {"rebuild", "r"}:
            return RebuildCommand()
        elif args.cmd in {"quit", "q"}:
            return ExitCommand()
        return None
    except argparse.ArgumentError:
        return None


def command_input_thread(command_queue: queue.Queue, stop_event: threading.Event, prompt_event: threading.Event):
    print(f"\nAvailable commands:\n{HELP_TEXT}")

    while not stop_event.is_set():
        try:
            prompt_event.wait()
            prompt_event.clear()

            if stop_event.is_set():
                break

            print("\n> ", end="", flush=True)
            command_line = input()
            if command_line.strip():
                cmd = parse_dev_command(command_line.strip())
                if cmd:
                    command_queue.put(cmd)
                    if isinstance(cmd, ExitCommand):
                        break
                else:
                    print(f"Unknown command. Available commands:\n{HELP_TEXT}")
                    # Show prompt again
                    prompt_event.set()
        except EOFError:
            break
        except Exception as e:
            print(f"Error reading command: {e}")
            break


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


def run_server(
    base_dir: Path,
    port: int,
    project_module_name: str | None,
    core_module_names: set[str] | None,
    build_dir: Path,
    config: BuildConfig,
    cache: CompileCache,
):
    interactive = project_module_name is not None and core_module_names is not None

    class DirectoryHandler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(base_dir), **kwargs)

        def log_message(self, fmt, *args):
            sys.stdout.write("\r\033[K")  # Clear line
            sys.stdout.write(f"{self.address_string()} - - [{self.log_date_time_string()}] {fmt % args}\n")
            if interactive:
                sys.stdout.write("> ")
            sys.stdout.flush()

    with socketserver.TCPServer(("", port), DirectoryHandler) as httpd:
        local_ips = get_local_ips()
        print(f"Server started on port {port}")
        print("Available on:")
        for ip in local_ips:
            print(f"  http://{ip}:{port}")

        if interactive:
            threading.Thread(target=httpd.serve_forever, daemon=True).start()

            command_queue = queue.Queue()
            stop_event = threading.Event()
            prompt_event = threading.Event()
            input_thread = threading.Thread(
                target=command_input_thread, args=(command_queue, stop_event, prompt_event), daemon=True
            )
            input_thread.start()

            prompt_event.set()

            try:
                while True:
                    try:
                        cmd = command_queue.get(timeout=0.5)
                        cmd.execute(project_module_name, core_module_names, build_dir, config, cache)
                        prompt_event.set()
                    except queue.Empty:
                        continue
            except KeyboardInterrupt:
                print("\nStopping server...")
                sys.exit(0)
            finally:
                httpd.shutdown()
                stop_event.set()
                prompt_event.set()
                input_thread.join()
        else:
            httpd.serve_forever()
