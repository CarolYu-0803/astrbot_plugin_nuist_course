"""Build an AstrBot upload archive from an explicit list of public source files."""

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = "astrbot_plugin_nuist_course"


def build():
    output = ROOT / "dist" / f"{PLUGIN}.zip"
    output.parent.mkdir(exist_ok=True)
    files = [ROOT / name for name in (
        "main.py", "metadata.yaml", "_conf_schema.json", "requirements.txt",
        "README.md", "pyproject.toml", "config.example.json", "tools/package_plugin.py",
    )]
    files += sorted((ROOT / "coursebot").glob("*.py"))
    files += sorted((ROOT / "docs").glob("*.md"))
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        # AstrBot 4.5's uploader expects the first ZIP entry to be a directory.
        archive.writestr(PLUGIN + "/", "")
        for path in files:
            archive.write(path, PLUGIN + "/" + path.relative_to(ROOT).as_posix())
    with ZipFile(output) as archive:
        assert archive.testzip() is None
        names = {Path(name).relative_to(PLUGIN).as_posix() for name in archive.namelist()[1:]}
        assert {"main.py", "metadata.yaml", "_conf_schema.json", "requirements.txt"} <= names
        assert not any(name.startswith(("state/", ".venv/")) for name in names)
        assert "config.json" not in names and "coursebot/push.py" not in names
    return output


if __name__ == "__main__":
    print(build())
