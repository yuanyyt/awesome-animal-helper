"""CSV output helpers."""

import csv
from collections.abc import Iterable
from pathlib import Path

from .models import AnimalRecord

FIELDNAMES = list(AnimalRecord("动物").as_csv_row())


def write_csv(records: Iterable[AnimalRecord], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(record.as_csv_row() for record in records)

