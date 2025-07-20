from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"


def compare_with_reference(name: str, data: str):
    existing_path = DATA_DIR / f"{name}"
    if existing_path.exists():
        assert data == existing_path.read_bytes().decode("utf-8")
    else:
        existing_path.write_bytes(data.encode("utf-8"))
