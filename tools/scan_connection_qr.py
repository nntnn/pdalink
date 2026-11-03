#!/usr/bin/env python3
"""Decode one PdaLink QR from an image or camera without retaining frames."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path


def decode(detector, frame) -> str:
    value, _, _ = detector.detectAndDecode(frame)
    return value.strip()


def scan_image(cv2, detector, path: Path) -> str:
    frame = cv2.imread(str(path))
    if frame is None:
        raise RuntimeError(f"could not read QR image: {path}")
    return decode(detector, frame)


def scan_camera(cv2, detector, index: int, timeout: float) -> str:
    camera = cv2.VideoCapture(index)
    if not camera.isOpened():
        raise RuntimeError(f"could not open camera {index}")
    deadline = time.monotonic() + timeout
    preview = True
    try:
        while time.monotonic() < deadline:
            ok, frame = camera.read()
            if not ok:
                continue
            value = decode(detector, frame)
            if value:
                return value
            if preview:
                try:
                    cv2.imshow("PdaLink QR - press Q to cancel", frame)
                    if cv2.waitKey(1) & 0xFF in (ord("q"), 27):
                        raise RuntimeError("QR scan cancelled")
                except cv2.error:
                    preview = False
        raise RuntimeError("QR scan timed out")
    finally:
        camera.release()
        if preview:
            try:
                cv2.destroyAllWindows()
            except cv2.error:
                pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scan a PdaLink connection QR")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--image", type=Path)
    source.add_argument("--camera", type=int)
    parser.add_argument("--timeout", type=float, default=60.0)
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    try:
        import cv2
    except ImportError:
        print(
            "OpenCV QR support is missing. Install opencv-python in this Python environment.",
            file=sys.stderr,
        )
        return 127

    detector = cv2.QRCodeDetector()
    try:
        value = scan_image(cv2, detector, args.image) if args.image else scan_camera(
            cv2,
            detector,
            args.camera,
            args.timeout,
        )
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 1
    if not value.lower().startswith("pdalink://connect?"):
        print("QR does not contain a PdaLink connection URI", file=sys.stderr)
        return 2
    print(value)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
