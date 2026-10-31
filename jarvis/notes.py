"""Short notes ("take a note ..."), kept in a plain text file."""


def add(path, text, now):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(f"{now:%Y-%m-%d %H:%M}  {text}\n")


def recent(path, count=5):
    try:
        lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except FileNotFoundError:
        return []
    return [line.split("  ", 1)[-1] for line in lines[-count:]]
