"""Bounded evidence locators and logical numeric equivalence.

This module owns deterministic value extraction from retained artifacts. It does
not discover research-log structure or decide semantic provenance.
"""

from __future__ import annotations

import ast
import csv
import json
import math
import os
import re
import warnings
import xml.etree.ElementTree as element_tree
import zipfile
from pathlib import Path
from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
    cast,
)

LOCATOR_VALUE_LIMIT = 10_000
LOCATOR_CONTEXT_LIMIT = 8 * 1024
LOCATOR_PREVIEW_ROW_LIMIT = 256
LOCATOR_ROW_LIMIT = 100_000
LOCATOR_TEXT_BYTE_LIMIT = 16 * 1024 * 1024
LOCATOR_JSON_BYTE_LIMIT = 16 * 1024 * 1024
LOCATOR_BINARY_MEMBER_BYTE_LIMIT = 64 * 1024 * 1024
STRUCTURE_TEXT_BYTE_LIMIT = 64 * 1024 * 1024
STRUCTURE_IMAGE_PIXEL_LIMIT = 64_000_000
STRUCTURE_CONTAINER_MEMBER_LIMIT = 100_000
STRUCTURE_CONTAINER_BYTE_LIMIT = 1024 * 1024 * 1024
STRUCTURE_DOCUMENT_BYTE_LIMIT = 256 * 1024 * 1024
OPAQUE_READABLE_SUFFIXES = frozenset(
    {".dat", ".jl", ".log", ".m", ".out", ".r", ".sh", ".txt"}
)
NUMBER_RE = re.compile(
    r"(?<![A-Za-z0-9_])[-+]?(?:\d+(?:,\d{3})*(?:\.\d+)?|\.\d+)"
    r"(?:[eE][-+]?\d+)?%?"
)
UNIT_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:bytes?|kib|mib|gib|tib|ns|µs|μs|us|ms|s|seconds?|"
    r"min|minutes?|h|hours?|hz|khz|mhz|ghz|nm|µm|μm|um|mas|arcsec|degrees?|deg|"
    r"radians?|rad|arcmin|kg|mg|g|km|cm|m|kelvin|k|kw|mw|w|pixels?|frames?|samples?|"
    r"counts?|photons?|ph)(?![A-Za-z0-9_])",
    re.IGNORECASE,
)
QUANTITY_SUFFIX_RE = re.compile(
    r"(?:[-+]?(?:\d+(?:,\d{3})*(?:\.\d+)?|\.\d+)(?:[eE][-+]?\d+)?%?)"
    r"\s+([A-Za-zµμ°][A-Za-zµμ°-]*)"
)
QUANTITY_SUFFIX_STOPWORDS = frozenset(
    {
        "and",
        "or",
        "to",
        "in",
        "from",
        "at",
        "by",
        "for",
        "of",
        "is",
        "are",
        "was",
        "were",
        "with",
        "than",
        "lower",
        "higher",
        "less",
        "more",
    }
)


def _inspect_json_structure(path: Path) -> Dict[str, Any]:
    if path.stat().st_size > LOCATOR_JSON_BYTE_LIMIT:
        return {
            "status": "unresolved",
            "type": "json",
            "detail": "file exceeds bounded structural-inspection limit",
        }
    json.loads(path.read_text(encoding="utf-8"))
    return {"status": "ok", "type": "json"}


def _bounded_document(path: Path, kind: str) -> Optional[Dict[str, Any]]:
    if path.stat().st_size <= STRUCTURE_DOCUMENT_BYTE_LIMIT:
        return None
    return {
        "status": "unresolved",
        "type": kind,
        "detail": "file exceeds bounded structural-inspection limit",
    }


def _inspect_ecsv_structure(path: Path) -> Dict[str, Any]:
    if path.stat().st_size > STRUCTURE_TEXT_BYTE_LIMIT:
        return {
            "status": "unresolved",
            "type": "ecsv",
            "detail": "file exceeds bounded ECSV verification limit",
        }
    try:
        from astropy.table import Table
    except ImportError:
        return _unavailable_structure("ecsv", "Astropy")
    table = Table.read(path, format="ascii.ecsv")
    return {
        "status": "ok",
        "type": "ecsv",
        "rows": len(table),
        "columns": list(table.colnames),
    }


def _inspect_table_structure(path: Path) -> Dict[str, Any]:
    delimiter = "\t" if path.suffix.lower() == ".tsv" else ","
    table_widths: set[int] = set()
    row_count = 0
    with path.open(newline="", encoding="utf-8") as handle:
        for row_count, row in enumerate(csv.reader(handle, delimiter=delimiter), 1):
            if row_count > LOCATOR_ROW_LIMIT:
                return {
                    "status": "unresolved",
                    "type": "table",
                    "detail": "file exceeds bounded structural row limit",
                }
            if row:
                table_widths.add(len(row))
    return {
        "status": "ok" if len(table_widths) <= 1 else "fail",
        "type": "table",
        "rows": max(0, row_count - 1),
        "columns": sorted(table_widths),
    }


def _unavailable_structure(kind: str, dependency: str) -> Dict[str, Any]:
    return {
        "status": "unresolved",
        "type": kind,
        "detail": f"{dependency} is unavailable for type-appropriate verification",
    }


def _inspect_image_structure(path: Path) -> Dict[str, Any]:
    kind = "jpeg" if path.suffix.lower() in {".jpg", ".jpeg"} else "png"
    try:
        from PIL import Image
    except ImportError:
        return _unavailable_structure(kind, "Pillow")
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        with Image.open(path) as image:
            width, height = image.size
            if width * height > STRUCTURE_IMAGE_PIXEL_LIMIT:
                return {
                    "status": "unresolved",
                    "type": kind,
                    "detail": "image exceeds bounded decoded-pixel limit",
                }
            image.load()
    return {
        "status": "ok",
        "type": kind,
        "width": width,
        "height": height,
    }


def _inspect_npz_structure(path: Path) -> Dict[str, Any]:
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        total_size = sum(item.file_size for item in infos)
        if (
            len(infos) > STRUCTURE_CONTAINER_MEMBER_LIMIT
            or total_size > STRUCTURE_CONTAINER_BYTE_LIMIT
        ):
            return {
                "status": "unresolved",
                "type": "npz",
                "detail": "archive exceeds bounded structural-inspection limit",
            }
        if not infos or any(not item.filename.endswith(".npy") for item in infos):
            return {
                "status": "fail",
                "type": "npz",
                "detail": "NPZ must contain one or more NumPy array members",
            }
        bad = archive.testzip()
        names = sorted(item.filename for item in infos)
    if bad is not None:
        return {"status": "fail", "type": "npz", "members": names, "bad": bad}
    try:
        import numpy as np
    except ImportError:
        return _unavailable_structure("npz", "NumPy")
    with np.load(path, allow_pickle=False) as artifact:
        for name in artifact.files:
            value = artifact[name]
            _ = value.shape, value.dtype
    return {
        "status": "ok",
        "type": "npz",
        "members": names,
    }


def _inspect_npy_structure(path: Path) -> Dict[str, Any]:
    try:
        import numpy as np
    except ImportError:
        return _unavailable_structure("npy", "NumPy")
    value = np.load(path, allow_pickle=False, mmap_mode="r")
    try:
        return {
            "status": "ok",
            "type": "npy",
            "shape": list(value.shape),
            "dtype": str(value.dtype),
        }
    finally:
        mapped = getattr(value, "_mmap", None)
        if mapped is not None:
            mapped.close()


def _inspect_hdf5_structure(path: Path) -> Dict[str, Any]:
    try:
        import h5py
    except ImportError:
        return _unavailable_structure("hdf5", "h5py")
    objects = 0
    datasets = 0
    exceeded = False
    with h5py.File(path, "r") as artifact:

        def count(_name: str, value: Any) -> Optional[str]:
            nonlocal objects, datasets, exceeded
            objects += 1
            if objects > STRUCTURE_CONTAINER_MEMBER_LIMIT:
                exceeded = True
                return "bounded structural member limit reached"
            if isinstance(value, h5py.Dataset):
                datasets += 1
                _ = value.shape, value.dtype
            return None

        artifact.visititems(count)
    if exceeded:
        return {
            "status": "unresolved",
            "type": "hdf5",
            "detail": "container exceeds bounded structural member limit",
        }
    return {
        "status": "ok",
        "type": "hdf5",
        "objects": objects,
        "datasets": datasets,
    }


def _inspect_fits_structure(path: Path) -> Dict[str, Any]:
    try:
        from astropy.io import fits
    except ImportError:
        return _unavailable_structure("fits", "Astropy")
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        with fits.open(
            path,
            mode="readonly",
            memmap=True,
            lazy_load_hdus=False,
            ignore_missing_end=False,
        ) as artifact:
            artifact.verify("exception")
            shapes = [
                list(shape)
                for hdu in artifact
                if (shape := getattr(hdu, "shape", None)) is not None
            ]
            hdus = len(artifact)
    if captured:
        raise ValueError("; ".join(str(item.message) for item in captured))
    return {
        "status": "ok",
        "type": "fits",
        "hdus": hdus,
        "shapes": shapes,
    }


def _inspect_pdf_structure(path: Path) -> Dict[str, Any]:
    bounded = _bounded_document(path, "pdf")
    if bounded is not None:
        return bounded
    try:
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError
    except ImportError:
        return _unavailable_structure("pdf", "pypdf")
    try:
        with path.open("rb") as handle:
            reader = PdfReader(handle, strict=True)
            for page in reader.pages:
                _ = page.mediabox
            pages = len(reader.pages)
    except PdfReadError as exc:
        raise ValueError(str(exc)) from exc
    return {"status": "ok", "type": "pdf", "pages": pages}


def _inspect_svg_structure(path: Path) -> Dict[str, Any]:
    if path.stat().st_size > STRUCTURE_TEXT_BYTE_LIMIT:
        return {
            "status": "unresolved",
            "type": "svg",
            "detail": "file exceeds bounded SVG verification limit",
        }
    root = element_tree.parse(path).getroot()
    if root.tag.rsplit("}", 1)[-1].lower() != "svg":
        raise ValueError("SVG root element is not <svg>")
    return {"status": "ok", "type": "svg"}


def _inspect_yaml_structure(path: Path) -> Dict[str, Any]:
    if path.stat().st_size > STRUCTURE_TEXT_BYTE_LIMIT:
        return {
            "status": "unresolved",
            "type": "yaml",
            "detail": "file exceeds bounded YAML verification limit",
        }
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError:
        return _unavailable_structure("yaml", "PyYAML")
    try:
        yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(str(exc)) from exc
    return {"status": "ok", "type": "yaml"}


def _inspect_notebook_structure(path: Path) -> Dict[str, Any]:
    bounded = _bounded_document(path, "notebook")
    if bounded is not None:
        return bounded
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("notebook root must be an object")
    if not isinstance(value.get("nbformat"), int):
        raise ValueError("notebook nbformat must be an integer")
    cells = value.get("cells")
    if not isinstance(cells, list):
        raise ValueError("notebook cells must be a list")
    if not all(
        isinstance(cell, dict)
        and cell.get("cell_type") in {"code", "markdown", "raw"}
        and isinstance(cell.get("source"), (str, list))
        for cell in cells
    ):
        raise ValueError("notebook contains an invalid cell record")
    return {"status": "ok", "type": "notebook", "cells": len(cells)}


def _inspect_parquet_structure(path: Path) -> Dict[str, Any]:
    try:
        import pyarrow.parquet as parquet
    except ImportError:
        return _unavailable_structure("parquet", "PyArrow")
    artifact = parquet.ParquetFile(path)
    try:
        return {
            "status": "ok",
            "type": "parquet",
            "rows": artifact.metadata.num_rows,
            "row_groups": artifact.metadata.num_row_groups,
            "columns": artifact.schema.names,
        }
    finally:
        artifact.close()


def _inspect_mat_structure(path: Path) -> Dict[str, Any]:
    bounded = _bounded_document(path, "mat")
    if bounded is not None:
        return bounded
    try:
        from scipy.io import whosmat
        from scipy.io.matlab import MatReadError
    except ImportError:
        return _unavailable_structure("mat", "SciPy")
    try:
        variables = whosmat(path)
    except (MatReadError, NotImplementedError, ValueError):
        with path.open("rb") as handle:
            header = handle.read(128)
        if b"MATLAB 7.3 MAT-file" not in header:
            return {
                "status": "fail",
                "type": "mat",
                "detail": "HDF5 input lacks a MATLAB 7.3 MAT-file header",
            }
        hdf5 = _inspect_hdf5_structure(path)
        return {**hdf5, "type": "mat"}
    return {
        "status": "ok",
        "type": "mat",
        "variables": len(variables),
    }


def _inspect_pickle_structure(_path: Path) -> Dict[str, Any]:
    return {
        "status": "unresolved",
        "type": "pickle",
        "detail": "pickle deserialization is prohibited during validation",
    }


def _inspect_opaque_structure(path: Path) -> Dict[str, Any]:
    with path.open("rb") as handle:
        handle.read(1)
    return {"status": "ok", "type": "opaque", "detail": "readability checked"}


def inspect_structure(path: Path) -> Dict[str, Any]:
    """Perform bounded structural inspection without importing research code."""

    suffix = path.suffix.lower()
    inspectors = {
        ".json": _inspect_json_structure,
        ".ecsv": _inspect_ecsv_structure,
        ".csv": _inspect_table_structure,
        ".tsv": _inspect_table_structure,
        ".png": _inspect_image_structure,
        ".jpg": _inspect_image_structure,
        ".jpeg": _inspect_image_structure,
        ".npz": _inspect_npz_structure,
        ".npy": _inspect_npy_structure,
        ".h5": _inspect_hdf5_structure,
        ".hdf5": _inspect_hdf5_structure,
        ".fit": _inspect_fits_structure,
        ".fits": _inspect_fits_structure,
        ".pdf": _inspect_pdf_structure,
        ".svg": _inspect_svg_structure,
        ".yaml": _inspect_yaml_structure,
        ".yml": _inspect_yaml_structure,
        ".ipynb": _inspect_notebook_structure,
        ".parquet": _inspect_parquet_structure,
        ".mat": _inspect_mat_structure,
        ".pkl": _inspect_pickle_structure,
        ".pickle": _inspect_pickle_structure,
    }
    try:
        if path.is_dir():
            with os.scandir(str(path)) as entries:
                members = sum(1 for _ in entries)
            return {
                "status": "ok",
                "type": "directory",
                "immediate_members": members,
                "identity": "deferred-until-adjudication",
            }
        if suffix == ".py":
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            return {"status": "ok", "type": "python", "detail": None}
        if suffix in inspectors:
            return inspectors[suffix](path)
        if suffix in OPAQUE_READABLE_SUFFIXES:
            return _inspect_opaque_structure(path)
        return {
            "status": "unresolved",
            "type": suffix.lstrip(".") or "file",
            "detail": "no type-appropriate structural inspector is registered",
        }
    except (
        OSError,
        SyntaxError,
        UnicodeError,
        ValueError,
        json.JSONDecodeError,
        csv.Error,
        zipfile.BadZipFile,
        element_tree.ParseError,
        Warning,
    ) as exc:
        return {
            "status": "fail",
            "type": suffix.lstrip(".") or "file",
            "detail": str(exc),
        }


def _unit_tokens(value: str) -> set[str]:
    aliases = {
        "byte": "bytes",
        "second": "s",
        "seconds": "s",
        "minute": "min",
        "minutes": "min",
        "hour": "h",
        "hours": "h",
        "degree": "deg",
        "degrees": "deg",
        "radian": "rad",
        "radians": "rad",
        "μs": "µs",
        "us": "µs",
        "μm": "µm",
        "um": "µm",
    }
    return {
        aliases.get(token.lower(), token.lower())
        for token in UNIT_RE.findall(value.replace("_", " "))
    }


def _unknown_quantity_suffixes(value: str) -> set[str]:
    """Return unit-like suffixes outside the mechanical unit vocabulary."""

    known = _unit_tokens(value)
    return {
        token.lower()
        for token in QUANTITY_SUFFIX_RE.findall(value)
        if token.lower() not in QUANTITY_SUFFIX_STOPWORDS
        and token.lower() not in known
    }


def _unit_compatibility(
    presented: str, values: Sequence[Any], retained_context: str, transformation: str
) -> Optional[Tuple[str, str]]:
    """Return an unresolved result when units cannot be linked mechanically."""

    unknown_units = _unknown_quantity_suffixes(presented)
    if unknown_units:
        return (
            "unresolved",
            "presented unit-like tokens are outside the mechanical vocabulary: "
            + ", ".join(sorted(unknown_units)),
        )
    presented_units = _unit_tokens(presented)
    retained_units = _unit_tokens(
        " ".join([*(str(value) for value in values), retained_context])
    )
    transformation_units = _unit_tokens(transformation)
    if presented_units and not (
        presented_units <= retained_units or presented_units <= transformation_units
    ):
        return (
            "unresolved",
            "presented units are not established by the retained value, locator, "
            "or declared transformation",
        )
    return None


def _displayed_number_specs(value: str) -> List[Tuple[float, int]]:
    """Return displayed numeric values and their least-significant place."""

    value = re.sub(r"(?<=[A-Za-zµμ°])\s*\^\s*[-+]?\d+", "", value)
    specs = []
    for token in NUMBER_RE.findall(value):
        cleaned = token.rstrip("%").replace(",", "")
        try:
            number = float(cleaned)
        except ValueError:
            continue
        parts = re.split(r"[eE]", cleaned, maxsplit=1)
        mantissa = parts[0]
        exponent = int(parts[1]) if len(parts) == 2 else 0
        decimals = len(mantissa.split(".", 1)[1]) if "." in mantissa else 0
        specs.append((number, exponent - decimals))
    return specs


def numeric_value_equivalent(
    presented: str,
    values: Sequence[Any],
    transformation: str = "",
    retained_context: str = "",
) -> bool:
    """Compare displayed numbers with locator-selected retained values."""

    status, _detail = numeric_equivalence(
        presented, values, transformation, retained_context
    )
    return status == "pass"


def _conversion_candidates(
    source: float,
    *,
    percent_factors: Sequence[float],
    kilo_factors: Sequence[float],
    transformation: str,
) -> List[float]:
    candidates = [source]
    candidates.extend(source * factor for factor in percent_factors)
    candidates.extend(source * factor for factor in kilo_factors)
    if "hour" in transformation:
        candidates.extend((source / 3600.0, source * 3600.0))
    if "minute" in transformation:
        candidates.extend((source / 60.0, source * 60.0))
    binary_units = {
        "byte": 1.0,
        "bytes": 1.0,
        "kib": 1024.0,
        "mib": 1024.0**2,
        "gib": 1024.0**3,
        "tib": 1024.0**4,
    }
    conversion = re.search(
        r"\b(bytes?|kib|mib|gib|tib)\s+to\s+(bytes?|kib|mib|gib|tib)\b",
        transformation,
    )
    if conversion:
        source_unit, target_unit = conversion.groups()
        candidates.append(
            source * binary_units[source_unit] / binary_units[target_unit]
        )
    return candidates


def _declared_percent_factors(transformation: str) -> tuple[float, ...]:
    """Return only percent scaling directions named by the transformation."""

    factors = []
    if (
        re.search(
            r"\b(?:convert(?:ed|ing)?|express(?:ed|ing)?|report(?:ed|ing)?)\b"
            r".*\b(?:as|to|in)\s+percent(?:age)?\b",
            transformation,
        )
        or re.search(r"\bfraction\b.*\bpercent(?:age)?\b", transformation)
        or re.search(r"\bmultipli(?:ed|cation|y|ying)\b.*\b100\b", transformation)
    ):
        factors.append(100.0)
    if (
        re.search(r"\bpercent(?:age)?\b.*\bfraction\b", transformation)
        or re.search(r"\bdivid(?:e|ed|ing)\b.*\b100\b", transformation)
    ):
        factors.append(0.01)
    return tuple(dict.fromkeys(factors))


def _declared_kilo_factors(transformation: str) -> tuple[float, ...]:
    """Return only thousands scaling directions named by the transformation."""

    transformation = transformation.replace(",", "")
    factors = []
    if (
        re.search(
            r"\b(?:convert(?:ed|ing)?|express(?:ed|ing)?|report(?:ed|ing)?)\b"
            r".*\b(?:as|to|in)\s+thousands?\b",
            transformation,
        )
        or re.search(r"\bdivid(?:e|ed|ing)\b.*\b1000\b", transformation)
    ):
        factors.append(0.001)
    if (
        re.search(r"\b(?:from)\s+thousands?\b", transformation)
        or re.search(r"\bmultipli(?:ed|cation|y|ying)\b.*\b1000\b", transformation)
    ):
        factors.append(1000.0)
    return tuple(dict.fromkeys(factors))


def _numeric_matches(
    expected: Sequence[Tuple[float, int]],
    available: Sequence[float],
    *,
    percent_factors: Sequence[float],
    kilo_factors: Sequence[float],
    transformation: str,
) -> List[List[int]]:
    """Return compatible retained-value indexes for each displayed number."""

    significant_figures = _significant_figures(transformation)
    matches = []
    for target, least_significant_place in expected:
        tolerance = _numeric_tolerance(
            target, least_significant_place, significant_figures
        )
        matches.append(
            [
                source_index
                for source_index, source in enumerate(available)
                if any(
                    abs(candidate - target) <= tolerance + 1e-12
                    for candidate in _conversion_candidates(
                        source,
                        percent_factors=percent_factors,
                        kilo_factors=kilo_factors,
                        transformation=transformation,
                    )
                )
            ]
        )
    return matches


def _numeric_tolerance(
    target: float, least_significant_place: int, significant_figures: Optional[int]
) -> float:
    if significant_figures is None or not target:
        return 0.5 * 10**least_significant_place
    significant_place = math.floor(math.log10(abs(target))) - significant_figures + 1
    return 0.5 * 10**significant_place


def _perfect_matching(candidates: Sequence[Sequence[int]]) -> bool:
    assigned: Dict[int, int] = {}

    def assign(presented_index: int, seen: set[int]) -> bool:
        for retained_index in candidates[presented_index]:
            if retained_index in seen:
                continue
            seen.add(retained_index)
            previous = assigned.get(retained_index)
            if previous is None or assign(previous, seen):
                assigned[retained_index] = presented_index
                return True
        return False

    return all(assign(index, set()) for index in range(len(candidates)))


def numeric_equivalence(
    presented: str,
    values: Sequence[Any],
    transformation: str = "",
    retained_context: str = "",
) -> Tuple[str, str]:
    """Compare retained numbers one-to-one with the displayed numbers."""

    expected = _displayed_number_specs(presented)
    available = [
        number for value in values for number, _ in _displayed_number_specs(str(value))
    ]
    if not expected or not available:
        return "unresolved", "no comparable numeric values were selected"
    if len(expected) != len(available):
        return (
            "unresolved",
            f"numeric cardinality differs: presented {len(expected)}, retained "
            f"{len(available)}",
        )
    unit_result = _unit_compatibility(
        presented, values, retained_context, transformation
    )
    if unit_result is not None:
        return unit_result
    lowered_transformation = transformation.lower()
    has_percent = "%" in presented
    has_kilo = bool(
        re.search(r"\d(?:\.\d+)?k(?:\b|$)", presented, re.IGNORECASE)
    )
    percent_factors = (
        _declared_percent_factors(lowered_transformation) if has_percent else ()
    )
    kilo_factors = _declared_kilo_factors(lowered_transformation) if has_kilo else ()
    matches = _numeric_matches(
        expected,
        available,
        percent_factors=percent_factors,
        kilo_factors=kilo_factors,
        transformation=lowered_transformation,
    )
    if matches and all(matches) and _perfect_matching(matches):
        return "pass", f"matched {len(expected)} numeric value(s) one-to-one"

    permissive_matches = _numeric_matches(
        expected,
        available,
        percent_factors=(100.0, 0.01) if has_percent else percent_factors,
        kilo_factors=(0.001, 1000.0) if has_kilo else kilo_factors,
        transformation=lowered_transformation,
    )
    if (
        permissive_matches
        and all(permissive_matches)
        and _perfect_matching(permissive_matches)
    ):
        return (
            "unresolved",
            "numeric scaling would be required but is not declared by the "
            "transformation",
        )
    if not all(matches):
        missing_index = next(
            index for index, compatible in enumerate(matches) if not compatible
        )
        target = expected[missing_index][0]
        detail = f"no retained value matches presented value {target:g}"
    else:
        detail = "retained values do not provide a one-to-one numeric match"
    return "fail", detail


def _significant_figures(transformation: str) -> Optional[int]:
    """Return an explicitly stated significant-figure count, if present."""

    words = {
        "one": 1,
        "two": 2,
        "three": 3,
        "four": 4,
        "five": 5,
        "six": 6,
    }
    match = re.search(
        r"\b(\d+|one|two|three|four|five|six)\s+significant\s+"
        r"(?:figure|figures|digit|digits)\b",
        transformation,
    )
    if not match:
        return None
    token = match.group(1)
    count = int(token) if token.isdigit() else words[token]
    return count if count > 0 else None


def _recursive_dicts(value: Any) -> Iterable[Dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _recursive_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _recursive_dicts(child)


def _json_path(value: Any, locator: str) -> Tuple[bool, Any]:
    """Resolve a JSON-style key path, including an explicit ``$`` root."""

    expression = locator.strip()
    if expression.startswith("$"):
        expression = expression[1:].removeprefix(".")

    current = value
    position = 0
    while position < len(expression):
        if expression[position] == ".":
            position += 1
            continue
        if expression[position] != "[":
            found, current, position = _json_key(current, expression, position)
            if not found:
                return False, None
        while position < len(expression) and expression[position] == "[":
            found, current, position = _json_selector(current, expression, position)
            if not found:
                return False, None
    return True, current


def _json_key(current: Any, expression: str, position: int) -> Tuple[bool, Any, int]:
    end = position
    while end < len(expression) and expression[end] not in ".[":
        end += 1
    key = expression[position:end]
    if not key or not isinstance(current, dict) or key not in current:
        return False, None, end
    return True, current[key], end


def _json_selector(
    current: Any, expression: str, position: int
) -> Tuple[bool, Any, int]:
    end = expression.find("]", position + 1)
    if end < 0 or not isinstance(current, list):
        return False, None, len(expression)
    selector = expression[position + 1 : end]
    try:
        if ":" in selector:
            parts = selector.split(":")
            if len(parts) != 2:
                return False, None, end + 1
            start = int(parts[0]) if parts[0] else None
            stop = int(parts[1]) if parts[1] else None
            selected = current[slice(start, stop)]
        else:
            index = int(selector)
            if index >= len(current) or index < -len(current):
                return False, None, end + 1
            selected = current[index]
    except ValueError:
        return False, None, end + 1
    return True, selected, end + 1


def _locator_fields(assignments: Dict[str, str]) -> List[str]:
    value = assignments.get("field") or assignments.get("fields") or ""
    return [part.strip() for part in value.split("|") if part.strip()]


def _locator_filters(assignments: Dict[str, str]) -> Dict[str, set[str]]:
    reserved = {"field", "fields", "path", "property", "text"}
    filters: Dict[str, set[str]] = {}
    for key, value in assignments.items():
        if key.startswith("where."):
            filter_key = key.removeprefix("where.")
        elif key not in reserved:
            filter_key = key
        else:
            continue
        if not filter_key:
            continue
        filters[filter_key] = {
            part.strip() for part in value.split("|") if part.strip()
        }
    return filters


def _locator_assignments(locator: str) -> Tuple[Dict[str, str], List[str]]:
    clauses = [part.strip() for part in locator.split(";") if part.strip()]
    assignments = {
        key.strip(): value.strip()
        for clause in clauses
        if "=" in clause
        for key, value in [clause.split("=", 1)]
    }
    return assignments, [clause for clause in clauses if "=" not in clause]


def _markdown_table(context: str) -> Optional[Tuple[List[str], List[List[str]]]]:
    """Parse one simple Markdown table without interpreting cell contents."""

    lines = [line.strip() for line in context.splitlines() if line.strip()]
    if len(lines) < 2:
        return None

    def cells(line: str) -> List[str]:
        return [cell.strip() for cell in line.strip().strip("|").split("|")]

    headers = cells(lines[0])
    separator = cells(lines[1])
    if len(headers) != len(separator) or not all(
        re.fullmatch(r":?-{3,}:?", cell) for cell in separator
    ):
        return None
    rows = [cells(line) for line in lines[2:]]
    if any(len(row) != len(headers) for row in rows):
        return None
    return headers, rows


def _normalized_table_cell(value: Any) -> str:
    return " ".join(str(value).strip().strip("`").split())


def _read_delimited_relation(
    path: Path,
) -> Tuple[str, List[str], List[Dict[str, str]], str]:
    delimiter = "\t" if path.suffix.lower() == ".tsv" else ","
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle, delimiter=delimiter)
            headers = list(reader.fieldnames or [])
            rows = []
            for row_number, row in enumerate(reader, 1):
                if row_number > LOCATOR_ROW_LIMIT:
                    return (
                        "unresolved",
                        [],
                        [],
                        f"the retained table exceeds the bounded row limit of "
                        f"{LOCATOR_ROW_LIMIT}",
                    )
                rows.append(cast(Dict[str, str], row))
    except (OSError, UnicodeError, csv.Error) as exc:
        return "fail", [], [], str(exc)
    if not headers:
        return "fail", [], [], "the retained table has no header"
    return "ok", headers, rows, ""


def _selected_relation(
    source_headers: Sequence[str],
    rows: Sequence[Dict[str, str]],
    locator: str,
    presented_headers: Sequence[str],
) -> Tuple[str, List[str], List[List[str]], str]:
    assignments, bare = _locator_assignments(locator)
    fields = _locator_fields(assignments)
    if not fields and bare and all(field in source_headers for field in bare):
        fields = bare
    if not fields and all(header in source_headers for header in presented_headers):
        fields = list(presented_headers)
    if fields != list(presented_headers):
        return (
            "unresolved",
            [],
            [],
            f"presented headers {list(presented_headers)} do not match retained "
            f"fields {fields}",
        )
    filters = _locator_filters(assignments)
    missing_filters = sorted(set(filters) - set(source_headers))
    if missing_filters:
        return "unresolved", [], [], f"missing locator filters: {missing_filters}"
    selected = [
        row
        for row in rows
        if all(str(row.get(key, "")) in allowed for key, allowed in filters.items())
    ]
    if len(selected) * max(1, len(fields)) > LOCATOR_VALUE_LIMIT:
        return (
            "unresolved",
            [],
            [],
            f"the table locator selects more than {LOCATOR_VALUE_LIMIT} values",
        )
    return "ok", fields, [[row[field] for field in fields] for row in selected], ""


def _table_rows_match(
    presented_row: Sequence[str],
    retained_row: Sequence[str],
    headers: Sequence[str],
    transformation: str,
) -> bool:
    for displayed, retained, header in zip(presented_row, retained_row, headers):
        displayed_value = _normalized_table_cell(displayed)
        retained_value = _normalized_table_cell(retained)
        displayed_numbers = _displayed_number_specs(displayed_value)
        retained_numbers = _displayed_number_specs(retained_value)
        if displayed_numbers or retained_numbers:
            if not displayed_numbers or not retained_numbers:
                return False
            if not numeric_value_equivalent(
                displayed_value,
                [retained_value],
                transformation,
                header,
            ):
                return False
        elif displayed_value != retained_value:
            return False
    return True


def table_equivalence(
    path: Path,
    locator: str,
    presented_context: str,
    transformation: str = "",
) -> Tuple[str, str]:
    """Compare one Markdown table with a selected retained CSV/TSV relation."""

    status, presented, detail = _presented_relation(path, presented_context)
    if status != "ok" or presented is None:
        return status, detail
    headers, presented_rows = presented
    status, source_headers, rows, detail = _read_delimited_relation(path)
    if status != "ok":
        return status, detail
    status, _fields, retained_rows, detail = _selected_relation(
        source_headers, rows, locator, headers
    )
    if status != "ok":
        return status, detail
    if len(presented_rows) != len(retained_rows):
        return (
            "unresolved",
            f"table row cardinality differs: presented {len(presented_rows)}, "
            f"retained {len(retained_rows)}",
        )

    candidates = [
        [
            retained_index
            for retained_index, retained_row in enumerate(retained_rows)
            if _table_rows_match(presented_row, retained_row, headers, transformation)
        ]
        for presented_row in presented_rows
    ]
    if not _perfect_matching(candidates):
        return "unresolved", "presented rows do not align with retained labeled rows"
    return "pass", f"matched {len(presented_rows)} labeled row(s) one-to-one"


def _presented_relation(
    path: Path, presented_context: str
) -> Tuple[str, Optional[Tuple[List[str], List[List[str]]]], str]:
    if path.suffix.lower() not in {".csv", ".tsv"}:
        return (
            "unresolved",
            None,
            "structured table comparison supports CSV/TSV only",
        )
    presented = _markdown_table(presented_context)
    if presented is None:
        return (
            "unresolved",
            None,
            "the presented Markdown table is not structurally valid",
        )
    return "ok", presented, ""


def _selected_property(value: Any, expression: str) -> Tuple[bool, Any, str]:
    """Return one closed-vocabulary structural property without evaluation."""

    if expression == "shape":
        return _shape_property(value, None)
    match = re.fullmatch(r"shape\[(\d+)\]", expression)
    if match:
        return _shape_property(value, int(match.group(1)))
    if expression == "size":
        size = getattr(value, "size", None)
        if size is None:
            return False, None, "the selected object has no size"
        return True, int(size), ""
    return False, None, f"unsupported structured property {expression!r}"


def _shape_property(value: Any, index: Optional[int]) -> Tuple[bool, Any, str]:
    shape = getattr(value, "shape", None)
    label = "shape" if index is None else f"shape[{index}]"
    if shape is None or (index is not None and index >= len(shape)):
        return False, None, f"the selected object has no {label}"
    selected = (
        tuple(int(item) for item in shape) if index is None else int(shape[index])
    )
    return True, selected, ""


def _plain_values(value: Any) -> Tuple[bool, List[Any], str]:
    """Convert one selected structured value into a bounded scalar list."""

    if isinstance(value, bytes):
        return True, [value.decode("utf-8", errors="replace")], ""
    if isinstance(value, dict):
        return False, [], "the locator selects a mapping rather than values"
    ok, value, reason = _materialized_value(value)
    if not ok:
        return False, [], reason
    flattened = list(_flatten_plain_values(value))
    if len(flattened) > LOCATOR_VALUE_LIMIT:
        return (
            False,
            [],
            f"the locator selects {len(flattened)} values, above the bounded "
            f"limit of {LOCATOR_VALUE_LIMIT}",
        )
    return True, flattened, ""


def _materialized_value(value: Any) -> Tuple[bool, Any, str]:
    if not (hasattr(value, "shape") and hasattr(value, "size")):
        return True, value, ""
    size = int(value.size)
    if size > LOCATOR_VALUE_LIMIT:
        return (
            False,
            None,
            f"the locator selects {size} values, above the bounded limit "
            f"of {LOCATOR_VALUE_LIMIT}",
        )
    try:
        value = value[()]
    except (IndexError, TypeError, ValueError):
        pass
    return True, value.tolist() if hasattr(value, "tolist") else value, ""


def _flatten_plain_values(value: Any) -> Iterable[Any]:
    if isinstance(value, (list, tuple)):
        for child in value:
            yield from _flatten_plain_values(child)
        return
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    if hasattr(value, "item"):
        try:
            value = value.item()
        except (TypeError, ValueError):
            pass
    yield value


def _json_selected_values(
    selected: Any,
    fields: Sequence[str],
    filters: Dict[str, set[str]],
) -> Tuple[str, List[Any], str]:
    """Extract fields from a JSON record or record collection."""

    status, records, detail = _json_records(selected)
    if status != "ok":
        return status, [], detail

    kept = _matching_json_records(records, filters)
    if not kept:
        return (
            "fail",
            [],
            "the structured locator selects no retained records; available "
            "filter values: "
            + _locator_context_preview(_json_filter_values(records, filters)),
        )
    status, values, detail = _json_field_values(kept, fields)
    if status != "ok":
        return status, values, detail
    return (
        "ok",
        values,
        f"selected {len(kept)} record(s), fields {list(fields)}; values: "
        + _bounded_preview(values),
    )


def _json_records(selected: Any) -> Tuple[str, List[Dict[str, Any]], str]:
    records = selected if isinstance(selected, list) else [selected]
    if len(records) > LOCATOR_ROW_LIMIT:
        return (
            "unresolved",
            [],
            f"the structured path selects more than {LOCATOR_ROW_LIMIT} records",
        )
    if not all(isinstance(record, dict) for record in records):
        return (
            "unresolved",
            [],
            "the structured path does not select records for field extraction",
        )
    return "ok", cast(List[Dict[str, Any]], records), ""


def _json_filter_values(
    records: Sequence[Dict[str, Any]], filters: Mapping[str, set[str]]
) -> Dict[str, List[str]]:
    available = {}
    for key in filters:
        values = []
        for record in records:
            found, value = _json_path(record, key)
            if found:
                values.append(str(value))
        available[key] = sorted(set(values))
    return available


def _json_field_values(
    records: Sequence[Dict[str, Any]], fields: Sequence[str]
) -> Tuple[str, List[Any], str]:
    values: List[Any] = []
    missing: set[str] = set()
    for record in records:
        for field in fields:
            found, value = _json_path(record, field)
            if not found:
                missing.add(field)
                continue
            ok, extracted, reason = _plain_values(value)
            if not ok:
                return "unresolved", [], f"field {field!r}: {reason}"
            values.extend(extracted)
    if missing:
        return "unresolved", [], "missing locator fields: " + ", ".join(sorted(missing))
    return "ok", values, ""


def _matching_json_records(
    records: Sequence[Dict[str, Any]], filters: Dict[str, set[str]]
) -> List[Dict[str, Any]]:
    """Return records satisfying every exact JSON-path filter."""

    kept = []
    for record in records:
        for key, allowed in filters.items():
            found, value = _json_path(record, key)
            if not found or str(value) not in allowed:
                break
        else:
            kept.append(record)
    return kept


def _oversized_npz_members(path: Path) -> List[str]:
    """Return compressed-container members unsafe to materialize."""

    import zipfile

    with zipfile.ZipFile(path) as archive:
        return sorted(
            item.filename
            for item in archive.infolist()
            if item.file_size > LOCATOR_BINARY_MEMBER_BYTE_LIMIT
        )


def _numpy_member(container: Any, expression: str) -> Tuple[bool, Any]:
    """Resolve one flat NPZ member with optional indexes or slices."""

    match = re.fullmatch(r"([^\[]+)((?:\[[^\]]+\])*)", expression)
    if not match or match.group(1) not in container:
        return False, None
    current = container[match.group(1)]
    for selector in re.findall(r"\[([^\]]+)\]", match.group(2)):
        try:
            if ":" in selector:
                parts = selector.split(":")
                if len(parts) != 2:
                    return False, None
                start = int(parts[0]) if parts[0] else None
                stop = int(parts[1]) if parts[1] else None
                current = current[slice(start, stop)]
            else:
                current = current[int(selector)]
        except (IndexError, TypeError, ValueError):
            return False, None
    return True, current


def _npz_locator_values(
    path: Path, assignments: Dict[str, str]
) -> Tuple[str, List[Any], str]:
    try:
        import numpy as np
    except ImportError:
        return "unresolved", [], "NumPy is unavailable for NPZ locator extraction"

    path_locator = assignments.get("path")
    if not path_locator:
        return "unresolved", [], "the NPZ locator requires path="
    fields = _locator_fields(assignments)
    filters = _locator_filters(assignments)
    property_name = assignments.get("property", "")
    try:
        oversized = _oversized_npz_members(path)
        if oversized:
            return (
                "unresolved",
                [],
                "the NPZ locator requires member(s) above the bounded byte limit: "
                + ", ".join(sorted(oversized)),
            )
        with np.load(path, allow_pickle=False) as artifact:
            return _opened_npz_values(
                artifact, path_locator, fields, filters, property_name
            )
    except (OSError, ValueError, TypeError) as exc:
        return "fail", [], str(exc)


def _opened_npz_values(
    artifact: Any,
    path_locator: str,
    fields: List[str],
    filters: Dict[str, set[str]],
    property_name: str,
) -> Tuple[str, List[Any], str]:
    if path_locator == "$":
        selected = artifact
    else:
        found, selected = _numpy_member(artifact, path_locator)
        if not found:
            return "fail", [], "the structured path does not resolve"
    if not fields:
        return _npz_scalar_values(selected, path_locator, filters, property_name)
    return _npz_field_values(
        artifact, path_locator, fields, filters, property_name
    )


def _npz_scalar_values(
    selected: Any,
    path_locator: str,
    filters: Dict[str, set[str]],
    property_name: str,
) -> Tuple[str, List[Any], str]:
    if filters:
        return "unresolved", [], "structured filters require field= or fields="
    if property_name:
        ok, selected, reason = _selected_property(selected, property_name)
        if not ok:
            return "unresolved", [], reason
    ok, values, reason = _plain_values(selected)
    if not ok:
        return "unresolved", [], reason
    return "ok", values, f"{path_locator}={_bounded_preview(values)}"


def _npz_field_values(
    artifact: Any,
    path_locator: str,
    fields: List[str],
    filters: Dict[str, set[str]],
    property_name: str,
) -> Tuple[str, List[Any], str]:
    if path_locator != "$":
        return "unresolved", [], "NPZ field extraction currently requires path=$"
    arrays = {}
    for name in {*fields, *filters}:
        found, value = _numpy_member(artifact, name)
        if not found:
            return "unresolved", [], f"missing locator field: {name}"
        arrays[name] = value
    status, indexes, detail = _npz_filter_indexes(arrays, filters)
    if status != "ok":
        return status, [], detail
    return _npz_extract_fields(arrays, fields, filters, indexes, property_name)


def _npz_filter_indexes(
    arrays: Dict[str, Any], filters: Dict[str, set[str]]
) -> Tuple[str, Optional[set[int]], str]:
    indexes = None
    for name, allowed in filters.items():
        array = arrays[name]
        if getattr(array, "ndim", 0) != 1:
            return (
                "unresolved",
                None,
                f"filter field {name!r} is not a one-dimensional array",
            )
        matched = {index for index, value in enumerate(array) if str(value) in allowed}
        indexes = matched if indexes is None else indexes & matched
    if indexes is not None and not indexes:
        return "fail", None, "the structured locator selects no aligned values"
    return "ok", indexes, ""


def _npz_extract_fields(
    arrays: Dict[str, Any],
    fields: List[str],
    filters: Dict[str, set[str]],
    indexes: Optional[set[int]],
    property_name: str,
) -> Tuple[str, List[Any], str]:
    values = []
    for field in fields:
        value = arrays[field]
        if indexes is not None:
            reference = arrays[next(iter(filters))]
            if getattr(value, "ndim", 0) == 0 or value.shape[0] != len(reference):
                return (
                    "unresolved",
                    [],
                    f"field {field!r} is not aligned with the filter array",
                )
            value = value[sorted(indexes)]
        if property_name:
            ok, value, reason = _selected_property(value, property_name)
            if not ok:
                return "unresolved", [], f"field {field!r}: {reason}"
        ok, extracted, reason = _plain_values(value)
        if not ok:
            return "unresolved", [], f"field {field!r}: {reason}"
        values.extend(extracted)
    return "ok", values, f"selected fields {fields}; values: {_bounded_preview(values)}"


def _hdf5_locator_values(
    path: Path, assignments: Dict[str, str]
) -> Tuple[str, List[Any], str]:
    try:
        import h5py
    except ImportError:
        return "unresolved", [], "h5py is unavailable for HDF5 locator extraction"

    path_locator = assignments.get("path")
    if not path_locator:
        return "unresolved", [], "the HDF5 locator requires path="
    filters = _locator_filters(assignments)
    if filters:
        return "unresolved", [], "HDF5 exact-match filters are not supported"
    try:
        with h5py.File(path, "r") as artifact:
            return _opened_hdf5_values(artifact, path_locator, assignments, h5py.Group)
    except (OSError, ValueError, TypeError) as exc:
        return "fail", [], str(exc)


def _opened_hdf5_values(
    artifact: Any,
    path_locator: str,
    assignments: Dict[str, str],
    group_type: type,
) -> Tuple[str, List[Any], str]:
    status, selected, detail = _hdf5_selection(artifact, path_locator)
    if status != "ok":
        return status, [], detail
    status, items, detail = _hdf5_items(
        selected, path_locator, _locator_fields(assignments), group_type
    )
    if status != "ok":
        return status, [], detail
    return _structured_item_values(items, assignments.get("property", ""))


def _hdf5_selection(
    artifact: Any, path_locator: str
) -> Tuple[str, Any, str]:
    if path_locator == "$":
        return "ok", artifact, ""
    if path_locator in artifact:
        return "ok", artifact[path_locator], ""
    return "fail", None, "the structured path does not resolve"


def _hdf5_items(
    selected: Any,
    path_locator: str,
    fields: Sequence[str],
    group_type: type[Any],
) -> Tuple[str, List[Tuple[str, Any]], str]:
    if not fields:
        return "ok", [(path_locator, selected)], ""
    if not isinstance(selected, group_type):
        return (
            "unresolved",
            [],
            "HDF5 fields require path= to select a group",
        )
    missing = [field for field in fields if field not in selected]
    if missing:
        return "unresolved", [], f"missing locator field: {missing[0]}"
    return "ok", [(field, selected[field]) for field in fields], ""


def _structured_item_values(
    items: Sequence[Tuple[str, Any]], property_name: str
) -> Tuple[str, List[Any], str]:
    values: List[Any] = []
    details = []
    for name, value in items:
        if property_name:
            ok, value, reason = _selected_property(value, property_name)
            if not ok:
                return "unresolved", [], f"field {name!r}: {reason}"
        ok, extracted, reason = _plain_values(value)
        if not ok:
            return "unresolved", [], f"field {name!r}: {reason}"
        values.extend(extracted)
        details.append(f"{name}={_bounded_preview(extracted)}")
    return "ok", values, "; ".join(details)


def _csv_locator_values(
    path: Path,
    locator: str,
    assignments: Dict[str, str],
    bare: List[str],
) -> Tuple[str, List[Any], str]:
    """Extract a bounded CSV/TSV selection in one streaming pass."""

    delimiter = "\t" if path.suffix.lower() == ".tsv" else ","
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=delimiter)
        headers = list(reader.fieldnames or [])
        if not headers:
            return "fail", [], "the retained table has no header"
        if not locator:
            preview_rows = []
            for row_number, row in enumerate(reader, 1):
                preview_rows.append(row)
                if row_number >= LOCATOR_PREVIEW_ROW_LIMIT:
                    break
            return (
                "unresolved",
                [],
                "the whole retained table is declared; bounded context: "
                + _locator_context_preview({"columns": headers, "rows": preview_rows}),
            )
        return _stream_csv_selection(reader, headers, assignments, bare)


def _stream_csv_selection(
    reader: csv.DictReader,
    headers: List[str],
    assignments: Dict[str, str],
    bare: List[str],
) -> Tuple[str, List[Any], str]:
    """Select bounded rows and fields from an open delimited reader."""

    status, fields, filters, detail = _csv_selection_contract(
        headers, assignments, bare
    )
    if status != "ok":
        return status, [], detail
    status, selected, available, detail = _collect_csv_rows(reader, fields, filters)
    if status != "ok":
        return status, [], detail
    if not selected:
        context = {key: sorted(values) for key, values in available.items()}
        return (
            "fail",
            [],
            "the locator selects no retained table rows; available filter values: "
            + _locator_context_preview(context),
        )
    if not fields:
        return (
            "unresolved",
            [],
            "the table locator does not name result fields; selected-row context: "
            + _locator_context_preview(selected),
        )
    values = [row[field] for row in selected for field in fields]
    return (
        "ok",
        values,
        f"selected {len(selected)} row(s), fields {fields}; values: "
        f"{_bounded_preview(values)}",
    )


def _csv_selection_contract(
    headers: List[str], assignments: Dict[str, str], bare: List[str]
) -> Tuple[str, List[str], Dict[str, set[str]], str]:
    columns = set(headers)
    fields = _locator_fields(assignments)
    if not fields and bare and all(field in columns for field in bare):
        fields = bare
    filters = _locator_filters(assignments)
    missing = sorted((set(fields) | set(filters)) - columns)
    if missing:
        return "unresolved", [], {}, f"locator fields are not columns: {missing}"
    return "ok", fields, filters, ""


def _collect_csv_rows(
    reader: csv.DictReader,
    fields: Sequence[str],
    filters: Dict[str, set[str]],
) -> Tuple[str, List[Dict[str, str]], Dict[str, set[str]], str]:
    selected: List[Dict[str, str]] = []
    available: Dict[str, set[str]] = {key: set() for key in filters}
    for row_number, row in enumerate(reader, 1):
        if row_number > LOCATOR_ROW_LIMIT:
            return (
                "unresolved",
                selected,
                available,
                f"the retained table exceeds the bounded row limit of "
                f"{LOCATOR_ROW_LIMIT}",
            )
        for key in filters:
            if len(available[key]) < 32:
                available[key].add(str(row.get(key, "")))
        if all(str(row.get(key, "")) in allowed for key, allowed in filters.items()):
            selected.append(row)
            if len(selected) * max(1, len(fields)) > LOCATOR_VALUE_LIMIT:
                return (
                    "unresolved",
                    selected,
                    available,
                    f"the locator selects more than {LOCATOR_VALUE_LIMIT} values",
                )
    return "ok", selected, available, ""


def _json_locator_values(
    path: Path,
    locator: str,
    assignments: Dict[str, str],
    bare: List[str],
) -> Tuple[str, List[Any], str]:
    """Extract a value from a byte-bounded JSON artifact."""

    if path.stat().st_size > LOCATOR_JSON_BYTE_LIMIT:
        return (
            "unresolved",
            [],
            f"the JSON artifact exceeds the bounded byte limit of "
            f"{LOCATOR_JSON_BYTE_LIMIT}",
        )
    content = json.loads(path.read_text(encoding="utf-8"))
    if not locator:
        return (
            "unresolved",
            [],
            "the whole retained JSON artifact is declared; bounded context: "
            + _locator_context_preview(content),
        )
    path_locator = assignments.get("path")
    if path_locator:
        return _json_path_locator_values(content, path_locator, assignments)
    if bare and not assignments:
        return _bare_json_locator_values(content, bare)
    return _recursive_json_locator_values(content, assignments)


def _json_path_locator_values(
    content: Any, path_locator: str, assignments: Dict[str, str]
) -> Tuple[str, List[Any], str]:
    found, selected = _json_path(content, path_locator)
    if not found:
        return "fail", [], "the structured path does not resolve"
    fields = _locator_fields(assignments)
    filters = _locator_filters(assignments)
    property_name = assignments.get("property", "")
    if fields:
        return _json_selected_values(selected, fields, filters)
    if filters:
        return "unresolved", [], "structured filters require field= or fields="
    if property_name:
        ok, selected, reason = _selected_property(selected, property_name)
        if not ok:
            return "unresolved", [], reason
    if isinstance(selected, (dict, list)):
        return (
            "unresolved",
            [],
            "the structured path names a compound value; context: "
            + _locator_context_preview(selected),
        )
    return "ok", [selected], f"{path_locator}={_bounded_preview(selected)}"


def _bare_json_locator_values(
    content: Any, bare: List[str]
) -> Tuple[str, List[Any], str]:
    for locator_path in bare:
        found, value = _json_path(content, locator_path)
        if not found:
            continue
        if isinstance(value, (dict, list)):
            return (
                "unresolved",
                [],
                "the JSON locator names a compound value; context: "
                + _locator_context_preview(value),
            )
        return "ok", [value], f"{locator_path}={_bounded_preview(value)}"
    return "fail", [], "the JSON locator does not resolve"


def _recursive_json_locator_values(
    content: Any, assignments: Dict[str, str]
) -> Tuple[str, List[Any], str]:
    fields = _locator_fields(assignments)
    filters = _locator_filters(assignments)
    selected = []
    for record in _recursive_dicts(content):
        if all(
            str(record.get(key, "")) in allowed for key, allowed in filters.items()
        ) and all(field in record for field in fields):
            selected.append(record)
            if len(selected) > LOCATOR_ROW_LIMIT:
                return (
                    "unresolved",
                    [],
                    f"the JSON locator selects more than {LOCATOR_ROW_LIMIT} records",
                )
    if not fields:
        return (
            "unresolved",
            [],
            "the JSON locator does not name result fields; record preview: "
            + _bounded_preview(selected[:2]),
        )
    if not selected:
        return "fail", [], "the JSON locator selects no retained records"
    values = [record[field] for record in selected for field in fields]
    if len(values) > LOCATOR_VALUE_LIMIT:
        return (
            "unresolved",
            [],
            f"the JSON locator selects more than {LOCATOR_VALUE_LIMIT} values",
        )
    return (
        "ok",
        values,
        f"selected {len(selected)} record(s), fields {fields}; values: "
        f"{_bounded_preview(values)}",
    )


def _text_locator_values(
    path: Path, locator: str, assignments: Dict[str, str]
) -> Tuple[str, List[Any], str]:
    """Extract matching lines from a byte-bounded text artifact."""

    if path.stat().st_size > LOCATOR_TEXT_BYTE_LIMIT:
        return (
            "unresolved",
            [],
            f"the text artifact exceeds the bounded byte limit of "
            f"{LOCATOR_TEXT_BYTE_LIMIT}",
        )
    fragment = assignments.get("text", locator)
    if not fragment:
        with path.open(encoding="utf-8") as handle:
            text = handle.read(LOCATOR_CONTEXT_LIMIT)
        return (
            "unresolved",
            [],
            "the whole retained text artifact is declared; bounded context: "
            + _locator_context_preview(text.splitlines()),
        )
    matches = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if fragment not in line:
                continue
            matches.append(line.rstrip("\r\n"))
            if len(matches) > LOCATOR_VALUE_LIMIT:
                return (
                    "unresolved",
                    [],
                    f"the text locator matches more than {LOCATOR_VALUE_LIMIT} lines",
                )
    if matches:
        return (
            "ok",
            matches,
            f"matched {len(matches)} text line(s); values: "
            + _bounded_preview(matches),
        )
    return "fail", [], "the text locator was not found"


def locator_values(
    path: Path,
    locator: str,
    inspector: Callable[[Path], Dict[str, Any]] = inspect_structure,
) -> Tuple[str, List[Any], str]:
    """Extract bounded values named by a durable evidence locator."""

    assignments, bare = _locator_assignments(locator)
    suffix = path.suffix.lower()
    try:
        if suffix in {".csv", ".tsv"}:
            return _csv_locator_values(path, locator, assignments, bare)
        if suffix == ".json":
            return _json_locator_values(path, locator, assignments, bare)
        if suffix in {".txt", ".log", ".md"}:
            return _text_locator_values(path, locator, assignments)
        if suffix in {".npz", ".h5", ".hdf5", ".pkl"}:
            return _binary_locator_values(path, locator, assignments, inspector)
    except (OSError, UnicodeError, csv.Error, json.JSONDecodeError) as exc:
        return "fail", [], str(exc)
    structure = inspector(path)
    declaration = "whole artifact" if not locator else f"locator {locator!r}"
    return (
        "unresolved",
        [],
        f"no deterministic locator reader for {suffix or 'file'}; {declaration}; "
        f"structure: {_bounded_preview(structure)}",
    )


def _binary_locator_values(
    path: Path,
    locator: str,
    assignments: Dict[str, str],
    inspector: Callable[[Path], Dict[str, Any]],
) -> Tuple[str, List[Any], str]:
    suffix = path.suffix.lower()
    if suffix == ".pkl":
        return (
            "unresolved",
            [],
            "pickle deserialization is prohibited; retain a CSV or JSON "
            "summary produced by an explicit command",
        )
    if not locator:
        artifact_type = "NPZ" if suffix == ".npz" else "HDF5"
        return (
            "unresolved",
            [],
            f"the whole retained {artifact_type} artifact is declared; structure: "
            + _locator_context_preview(inspector(path)),
        )
    if suffix == ".npz":
        return _npz_locator_values(path, assignments)
    return _hdf5_locator_values(path, assignments)


def normalized_text_equivalence(path: Path, presented: str) -> Tuple[str, str]:
    """Compare one complete normalized excerpt within a bounded text artifact."""

    try:
        if path.stat().st_size > LOCATOR_TEXT_BYTE_LIMIT:
            return (
                "unresolved",
                f"the text artifact exceeds the bounded byte limit of "
                f"{LOCATOR_TEXT_BYTE_LIMIT}",
            )
        retained = " ".join(path.read_text(encoding="utf-8").split())
    except (OSError, UnicodeError) as exc:
        return "fail", str(exc)
    excerpt = " ".join(presented.split())
    if excerpt and excerpt in retained:
        return "pass", "complete normalized output excerpt occurs in source"
    return "fail", "complete normalized output excerpt was not found in source"


def mechanical_evidence_support(
    row: Mapping[str, Any],
    source: Mapping[str, Any],
    inspector: Callable[[Path], Dict[str, Any]] = inspect_structure,
) -> Dict[str, str]:
    """Check one presented-item association with deterministic mechanics.

    The caller owns artifact-structure inspection because that operation also
    serves scan inventory. This function owns locator extraction and logical
    equivalence only; unresolved semantics remain for adjudication.
    """

    if source["status"] != "resolved" or not source.get("path"):
        return {
            "status": "fail",
            "detail": (f"supporting source is {source['status']}: {source['source']}"),
        }
    if row["kind"] == "table":
        return _mechanical_table_support(row, source)
    if row["kind"] == "output":
        return _mechanical_output_support(row, source)
    return _mechanical_numeric_support(row, source, inspector)


def _mechanical_table_support(
    row: Mapping[str, Any], source: Mapping[str, Any]
) -> Dict[str, str]:
    presented = row.get("presented_item")
    if not presented:
        return {
            "status": "unresolved",
            "detail": "unmatched table requires semantic review",
        }
    if len(row.get("source_specs", [])) != 1:
        return {
            "status": "unresolved",
            "detail": "multi-source table requires semantic review",
        }
    status, detail = table_equivalence(
        Path(source["path"]),
        source.get("locator", ""),
        presented["context"],
        row.get("transformation", ""),
    )
    return {"status": status, "detail": detail}


def _mechanical_output_support(
    row: Mapping[str, Any], source: Mapping[str, Any]
) -> Dict[str, str]:
    presented = row.get("presented_item")
    if not presented:
        return {
            "status": "unresolved",
            "detail": "unmatched output excerpt requires semantic review",
        }
    status, detail = normalized_text_equivalence(
        Path(source["path"]), presented["context"]
    )
    return {"status": status, "detail": detail}


def _mechanical_numeric_support(
    row: Mapping[str, Any],
    source: Mapping[str, Any],
    inspector: Callable[[Path], Dict[str, Any]],
) -> Dict[str, str]:
    status, values, detail = locator_values(
        Path(source["path"]),
        source.get("locator", ""),
        inspector,
    )
    if status != "ok":
        return {"status": status, "detail": detail}
    selector = re.sub(r" \[occurrence \d+\]$", "", row["evidence"])
    equivalence, equivalence_detail = numeric_equivalence(
        selector,
        values,
        row.get("transformation", ""),
        source.get("locator", ""),
    )
    if equivalence == "pass":
        return {
            "status": "unresolved",
            "detail": (
                f"{detail}; {equivalence_detail}; numeric agreement is a mechanical "
                "candidate and requires semantic field/context confirmation"
            ),
        }
    return {
        "status": equivalence,
        "detail": (
            "presented value is not mechanically equivalent to locator-selected "
            f"values ({detail}; {equivalence_detail})"
        ),
    }


def _bounded_preview(value: Any, limit: int = 320) -> str:
    """Return compact extracted context without turning scan JSON into a copy."""

    if isinstance(value, dict):
        value = dict(list(value.items())[:12])
    elif isinstance(value, list):
        value = value[:12]
    rendered = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    if len(rendered) > limit:
        return rendered[: limit - 3] + "..."
    return rendered


def _locator_context_preview(value: Any) -> str:
    """Return complete small-artifact context with a strict character bound."""

    rendered = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    if len(rendered) > LOCATOR_CONTEXT_LIMIT:
        return rendered[: LOCATOR_CONTEXT_LIMIT - 3] + "..."
    return rendered
