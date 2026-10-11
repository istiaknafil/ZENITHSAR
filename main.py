from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.image_comparison import compare_images
from src.image_inspection import inspect_image
from src.image_viewer import save_image_preview, save_inspection_figure


def _common_image_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--output-dir", type=Path, default=Path("outputs"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Team Zenith image viewer, comparison, and prepared-SAR prototype."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    view = commands.add_parser("view", help="Open an image and save a processed preview.")
    view.add_argument("--image", type=Path, required=True)
    view.add_argument("--grayscale", action="store_true")
    view.add_argument("--enhance-contrast", action="store_true")
    _common_image_args(view)
    inspect = commands.add_parser("inspect", help="Report image metadata and statistics.")
    inspect.add_argument("--image", type=Path, required=True)
    inspect.add_argument("--grayscale", action="store_true")
    inspect.add_argument("--enhance-contrast", action="store_true")
    _common_image_args(inspect)
    compare = commands.add_parser("compare", help="Compare ordinary PNG/JPG images.")
    compare.add_argument("--before", type=Path, required=True)
    compare.add_argument("--after", type=Path, required=True)
    compare.add_argument("--resize-for-demo", action="store_true")
    _common_image_args(compare)
    sar = commands.add_parser("sar-change", help="Compare compatible SAR GeoTIFF layers.")
    sar.add_argument("--before", type=Path, required=True)
    sar.add_argument("--after", type=Path, required=True)
    sar.add_argument("--threshold-db", type=float, default=3.0)
    sar.add_argument("--units", choices=("linear", "db"), default="linear")
    sar.add_argument("--polarization")
    sar.add_argument("--acquisition")
    sar.add_argument("--output-dir", type=Path, default=Path("outputs"))
    return parser


def run(args: argparse.Namespace) -> int:
    if args.command == "view":
        output = args.output_dir / f"{args.image.stem}_preview.png"
        info = save_image_preview(
            args.image, output, grayscale=args.grayscale, enhance_contrast=args.enhance_contrast
        )
        print(json.dumps({"image": info.as_dict(), "preview": str(output)}, indent=2))
    elif args.command == "inspect":
        output = args.output_dir / f"{args.image.stem}_inspection.png"
        info = save_inspection_figure(
            args.image, output, grayscale=args.grayscale, enhance_contrast=args.enhance_contrast
        )
        print(json.dumps({"image": info.as_dict(), "inspection": str(output)}, indent=2))
    elif args.command == "compare":
        result = compare_images(
            args.before, args.after, args.output_dir / "image_comparison_preview.png",
            resize_for_demo=args.resize_for_demo,
        )
        print(json.dumps({
            "before": result.before.as_dict(), "after": result.after.as_dict(),
            "mean_absolute_difference": result.mean_absolute_difference,
            "maximum_absolute_difference": result.maximum_absolute_difference,
            "resized_for_demo": result.resized_for_demo,
            "warning": "Ordinary screenshot differences are not validated Earth-surface changes.",
        }, indent=2))
    elif args.command == "sar-change":
        from src.sar_change_detection import compare_sar

        result = compare_sar(
            args.before, args.after, args.output_dir, threshold_db=args.threshold_db,
            units=args.units, polarization=args.polarization, acquisition=args.acquisition,
        )
        print(json.dumps(result.as_dict(), indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(run(build_parser().parse_args()))
    except (FileNotFoundError, ValueError, OSError, RuntimeError) as exc:
        raise SystemExit(f"Error: {exc}")
