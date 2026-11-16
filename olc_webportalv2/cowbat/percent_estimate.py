#!/usr/bin/env python

"""
Parse a COWBAT logfile and estimate pipeline progress for each unique message.
"""

import json
import os
import re
import sys
from datetime import datetime


LOG_PATTERN = re.compile(r"(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2}) (.*)")
ANSI_PATTERN = re.compile(r"\x1b\[.*?m")


def estimate_percent_complete(log_entries):
    """Return unique timestamped log messages with estimated percentages."""
    log_entries = ANSI_PATTERN.sub("", log_entries)
    message_occurrences = {}
    parsed_entries = []

    for line in log_entries.splitlines():
        match = LOG_PATTERN.search(line)
        if not match:
            continue

        date, time, message = match.groups()
        timestamp = datetime.strptime(
            "{} {}".format(date, time), "%Y-%m-%d %H:%M:%S"
        )
        message_occurrences[message] = message_occurrences.get(message, 0) + 1
        parsed_entries.append({
            "datetime": timestamp,
            "message": message,
        })

    unique_entries = [
        entry for entry in parsed_entries
        if message_occurrences[entry["message"]] == 1
    ]
    if len(unique_entries) < 2:
        raise ValueError("At least two unique timestamped messages are required.")

    start_time = unique_entries[0]["datetime"]
    total_seconds = (
        unique_entries[-1]["datetime"] - start_time
    ).total_seconds()
    if total_seconds <= 0:
        raise ValueError("The unique log messages must span a positive duration.")

    output_data = []
    for entry in unique_entries:
        elapsed_seconds = (entry["datetime"] - start_time).total_seconds()
        output_data.append({
            "message": entry["message"],
            "percent_complete": round(
                elapsed_seconds / total_seconds * 100, 2
            ),
        })
    return output_data


def main(log_file_path, output_path=None):
    """Generate the progress lookup JSON consumed by the COWBAT view."""
    with open(log_file_path, "r", encoding="utf-8") as log_file:
        output_data = estimate_percent_complete(log_file.read())

    if output_path is None:
        output_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "cowbat_percent_complete.json",
        )
    with open(output_path, "w", encoding="utf-8") as output_file:
        json.dump(output_data, output_file)
    return output_path


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.stderr.write("Usage: percent_estimate.py <cowbat-log-file>\n")
        sys.exit(2)
    main(sys.argv[1])
