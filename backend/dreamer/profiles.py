"""Resolve the vendor preset graph into complete CLI input files."""

import argparse
import json
from pathlib import Path


def flatten(root: Path, category: str, name: str) -> dict:
    presets = {}
    for path in (root / category).rglob("*.json"):
        value = json.loads(path.read_text())
        if value.get("name") in presets:
            raise ValueError("Duplicate preset name")
        presets[value.get("name", path.stem)] = value

    def resolve(key, stack=()):
        if key in stack:
            raise ValueError("Preset inheritance cycle")
        if key not in presets:
            raise ValueError(f"Missing preset: {key}")
        source = presets[key]
        result = {}
        if source.get("inherits"):
            result.update(resolve(source["inherits"], (*stack, key)))
        for include in source.get("include", []):
            result.update(resolve(include, (*stack, key)))
        result.update(source)
        result.pop("inherits", None)
        result.pop("include", None)
        return result

    result = resolve(name)
    # Template fragments have their own names; the selected leaf owns identity.
    result["name"] = name
    result["type"] = category
    result["instantiation"] = "true"
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("resources", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--machine", default="Bambu Lab A1 mini 0.4 nozzle")
    parser.add_argument("--process", default="0.20mm Standard @BBL A1M")
    parser.add_argument("--filament", default="Generic PLA @BBL A1M")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    for category in ("machine", "process", "filament"):
        preset = flatten(args.resources, category, getattr(args, category))
        (args.output / f"{category}.json").write_text(json.dumps(preset, indent=2))


if __name__ == "__main__":
    main()
