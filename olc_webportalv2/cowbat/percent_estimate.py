#!/usr/bin/env python

"""Estimate COWBAT pipeline completion from timestamps in its log file."""

import json
import re
import sys
from datetime import datetime
from pathlib import Path

LOG_PATTERN = re.compile(r"(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2}) (.*)")
ANSI_PATTERN = re.compile(r"\x1b\[.*?m")


def estimate_percent_complete(
    log_entries: str,
) -> list[dict[str, str | float]]:
    """Estimate completion percentages for unique timestamped log messages.

    Args:
        log_entries: COWBAT log contents to parse.

    Returns:
        Ordered messages with their estimated completion percentages.

    Raises:
        ValueError: If fewer than two unique messages span positive time.
    """
    message_occurrences = {}
    parsed_entries = []
    cleaned_log = ANSI_PATTERN.sub("", log_entries)

    for line in cleaned_log.splitlines():
        match = LOG_PATTERN.search(line)
        if not match:
            continue

        date, time, message = match.groups()
        timestamp = datetime.strptime(
            f"{date} {time}",
            "%Y-%m-%d %H:%M:%S",
        )
        message_occurrences[message] = (
            message_occurrences.get(message, 0) + 1
        )
        parsed_entries.append((timestamp, message))

    unique_entries = [
        entry
        for entry in parsed_entries
        if message_occurrences[entry[1]] == 1
    ]
    if len(unique_entries) < 2:
        raise ValueError(
            "At least two unique timestamped messages are required."
        )

    start_time = unique_entries[0][0]
    total_seconds = (unique_entries[-1][0] - start_time).total_seconds()
    if total_seconds <= 0:
        raise ValueError(
            "The unique log messages must span a positive duration."
        )

    return [
        {
            "message": message,
            "percent_complete": round(
                (timestamp - start_time).total_seconds()
                / total_seconds
                * 100,
                2,
            ),
        }
        for timestamp, message in unique_entries
    ]


def main(log_file_path: str, output_path: str | None = None) -> Path:
    """Write a progress estimate JSON file for the supplied COWBAT log.

    Args:
        log_file_path: Path to the timestamped COWBAT log.
        output_path: Optional destination for the generated JSON file.

    Returns:
        The path to the generated progress map.
    """
    with open(log_file_path, "r", encoding="utf-8") as log_file:
        output_data = estimate_percent_complete(log_file.read())

    destination = (
        Path(output_path)
        if output_path
        else Path(__file__).with_name("cowbat_percent_complete.json")
    )
    with destination.open("w", encoding="utf-8") as output_file:
        json.dump(output_data, output_file)
    return destination


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.stderr.write("Usage: percent_estimate.py <cowbat-log-file>\n")
        sys.exit(2)
    main(sys.argv[1])
