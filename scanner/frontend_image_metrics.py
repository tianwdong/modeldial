from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import struct
from typing import Any, Mapping, Sequence
import zlib


class FrontendImageError(RuntimeError):
    pass


@dataclass(frozen=True)
class RGBImage:
    width: int
    height: int
    pixels: bytes


def _paeth(left: int, above: int, upper_left: int) -> int:
    estimate = left + above - upper_left
    left_distance = abs(estimate - left)
    above_distance = abs(estimate - above)
    upper_left_distance = abs(estimate - upper_left)
    if left_distance <= above_distance and left_distance <= upper_left_distance:
        return left
    if above_distance <= upper_left_distance:
        return above
    return upper_left


def read_rgb_png(path: Path) -> RGBImage:
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise FrontendImageError(f"frontend visual evidence is not a PNG: {path}")
    offset = 8
    header: tuple[int, int, int, int, int, int, int] | None = None
    compressed: list[bytes] = []
    while offset + 12 <= len(data):
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        chunk_type = data[offset + 4 : offset + 8]
        chunk = data[offset + 8 : offset + 8 + length]
        crc = data[offset + 8 + length : offset + 12 + length]
        if len(chunk) != length or len(crc) != 4:
            raise FrontendImageError(f"frontend PNG chunk is truncated: {path}")
        observed_crc = zlib.crc32(chunk_type + chunk) & 0xFFFFFFFF
        if observed_crc != struct.unpack(">I", crc)[0]:
            raise FrontendImageError(f"frontend PNG CRC mismatch: {path}")
        if chunk_type == b"IHDR":
            if length != 13:
                raise FrontendImageError(f"frontend PNG IHDR is invalid: {path}")
            header = struct.unpack(">IIBBBBB", chunk)
        elif chunk_type == b"IDAT":
            compressed.append(chunk)
        elif chunk_type == b"IEND":
            break
        offset += 12 + length
    if header is None:
        raise FrontendImageError(f"frontend PNG has no IHDR: {path}")
    width, height, depth, color_type, compression, filtering, interlace = header
    if (
        width < 1
        or height < 1
        or depth != 8
        or color_type != 2
        or compression != 0
        or filtering != 0
        or interlace != 0
    ):
        raise FrontendImageError(
            "frontend requires 8-bit non-interlaced RGB PNG: "
            f"{path}: {header}"
        )
    stride = width * 3
    try:
        raw = zlib.decompress(b"".join(compressed))
    except zlib.error as error:
        raise FrontendImageError(
            f"frontend PNG data cannot be decompressed: {path}"
        ) from error
    if len(raw) != height * (stride + 1):
        raise FrontendImageError(f"frontend PNG scanline size is invalid: {path}")
    pixels = bytearray(width * height * 3)
    previous = bytearray(stride)
    source_offset = 0
    output_offset = 0
    for _row in range(height):
        filter_type = raw[source_offset]
        scanline = bytearray(raw[source_offset + 1 : source_offset + 1 + stride])
        source_offset += stride + 1
        if filter_type not in {0, 1, 2, 3, 4}:
            raise FrontendImageError(
                f"frontend PNG filter is unsupported: {filter_type}"
            )
        for index in range(stride):
            left = scanline[index - 3] if index >= 3 else 0
            above = previous[index]
            upper_left = previous[index - 3] if index >= 3 else 0
            if filter_type == 1:
                scanline[index] = (scanline[index] + left) & 255
            elif filter_type == 2:
                scanline[index] = (scanline[index] + above) & 255
            elif filter_type == 3:
                scanline[index] = (scanline[index] + ((left + above) // 2)) & 255
            elif filter_type == 4:
                scanline[index] = (
                    scanline[index] + _paeth(left, above, upper_left)
                ) & 255
        pixels[output_offset : output_offset + stride] = scanline
        output_offset += stride
        previous = scanline
    return RGBImage(width=width, height=height, pixels=bytes(pixels))


def _block_values(
    image: RGBImage,
    crop: Sequence[int],
    block_size: int,
) -> tuple[list[tuple[float, float, float]], list[float], int, int]:
    x, y, width, height = (int(value) for value in crop)
    if x < 0 or y < 0 or x + width > image.width or y + height > image.height:
        raise FrontendImageError("frontend crop escapes screenshot")
    colors: list[tuple[float, float, float]] = []
    luminance: list[float] = []
    block_width = math.ceil(width / block_size)
    block_height = math.ceil(height / block_size)
    pixels = image.pixels
    for top in range(y, y + height, block_size):
        bottom = min(y + height, top + block_size)
        for left in range(x, x + width, block_size):
            right = min(x + width, left + block_size)
            red = green = blue = count = 0
            for row in range(top, bottom):
                index = (row * image.width + left) * 3
                for _column in range(left, right):
                    red += pixels[index]
                    green += pixels[index + 1]
                    blue += pixels[index + 2]
                    count += 1
                    index += 3
            color = (red / count, green / count, blue / count)
            colors.append(color)
            luminance.append(
                0.2126 * color[0] + 0.7152 * color[1] + 0.0722 * color[2]
            )
    return colors, luminance, block_width, block_height


def _ssim(reference: Sequence[float], candidate: Sequence[float]) -> float:
    if len(reference) != len(candidate) or not reference:
        raise FrontendImageError("frontend SSIM inputs are invalid")
    count = len(reference)
    mean_reference = sum(reference) / count
    mean_candidate = sum(candidate) / count
    variance_reference = (
        sum((value - mean_reference) ** 2 for value in reference) / count
    )
    variance_candidate = (
        sum((value - mean_candidate) ** 2 for value in candidate) / count
    )
    covariance = sum(
        (left - mean_reference) * (right - mean_candidate)
        for left, right in zip(reference, candidate)
    ) / count
    c1 = (0.01 * 255) ** 2
    c2 = (0.03 * 255) ** 2
    denominator = (
        (mean_reference**2 + mean_candidate**2 + c1)
        * (variance_reference + variance_candidate + c2)
    )
    if denominator == 0:
        return 1.0
    value = (
        (2 * mean_reference * mean_candidate + c1)
        * (2 * covariance + c2)
        / denominator
    )
    return min(1.0, max(0.0, value))


def _edge_mask(
    luminance: Sequence[float],
    width: int,
    height: int,
    threshold: float,
) -> set[tuple[int, int]]:
    edges: set[tuple[int, int]] = set()
    for row in range(height):
        for column in range(width):
            index = row * width + column
            horizontal = (
                abs(luminance[index] - luminance[index + 1])
                if column + 1 < width
                else 0.0
            )
            vertical = (
                abs(luminance[index] - luminance[index + width])
                if row + 1 < height
                else 0.0
            )
            if max(horizontal, vertical) >= threshold:
                edges.add((column, row))
    return edges


def _edge_f1(
    reference: set[tuple[int, int]],
    candidate: set[tuple[int, int]],
    tolerance: int,
) -> float:
    if not reference and not candidate:
        return 1.0
    if not reference or not candidate:
        return 0.0

    def matched(source: set[tuple[int, int]], target: set[tuple[int, int]]) -> int:
        return sum(
            any(
                (column + dx, row + dy) in target
                for dy in range(-tolerance, tolerance + 1)
                for dx in range(-tolerance, tolerance + 1)
            )
            for column, row in source
        )

    precision = matched(candidate, reference) / len(candidate)
    recall = matched(reference, candidate) / len(reference)
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def visual_similarity(
    reference: RGBImage,
    candidate: RGBImage,
    *,
    crop: Sequence[int],
    visual_rules: Mapping[str, Any],
) -> dict[str, float]:
    if (reference.width, reference.height) != (candidate.width, candidate.height):
        raise FrontendImageError(
            "frontend reference and candidate screenshot dimensions differ"
        )
    block_size = int(visual_rules["block_size"])
    reference_colors, reference_luma, block_width, block_height = _block_values(
        reference,
        crop,
        block_size,
    )
    candidate_colors, candidate_luma, candidate_width, candidate_height = (
        _block_values(candidate, crop, block_size)
    )
    if (block_width, block_height) != (candidate_width, candidate_height):
        raise FrontendImageError("frontend block grids differ")
    mean_absolute_error = sum(
        abs(left[channel] - right[channel])
        for left, right in zip(reference_colors, candidate_colors)
        for channel in range(3)
    ) / (len(reference_colors) * 3)
    color = min(1.0, max(0.0, 1.0 - mean_absolute_error / 255.0))
    ssim = _ssim(reference_luma, candidate_luma)
    reference_edges = _edge_mask(
        reference_luma,
        block_width,
        block_height,
        float(visual_rules["edge_threshold"]),
    )
    candidate_edges = _edge_mask(
        candidate_luma,
        block_width,
        block_height,
        float(visual_rules["edge_threshold"]),
    )
    edge = _edge_f1(
        reference_edges,
        candidate_edges,
        int(visual_rules["edge_tolerance_blocks"]),
    )
    weights = visual_rules["weights"]
    similarity = (
        float(weights["ssim"]) * ssim
        + float(weights["color"]) * color
        + float(weights["edge"]) * edge
    )
    return {
        "ssim": round(ssim, 6),
        "color": round(color, 6),
        "edge_f1": round(edge, 6),
        "similarity": round(similarity, 6),
    }


__all__ = [
    "FrontendImageError",
    "RGBImage",
    "read_rgb_png",
    "visual_similarity",
]
