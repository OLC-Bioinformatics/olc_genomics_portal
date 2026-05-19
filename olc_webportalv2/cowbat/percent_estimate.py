#!/usr/bin/env python

"""
Parse a COWBAT logfile and create estimates for the percentage complete of the
COWBAT pipeline based on the timestamps of the log entries.
"""

# Standard imports
from datetime import datetime
import json
import re
import sys

# Get the log file path from the command line arguments
log_file_path = sys.argv[1]

# Read the log entries from the file
with open(log_file_path, 'r', encoding='utf-8') as file:
    log_entries = file.read()

# Remove ANSI escape codes
log_entries = re.sub(r'\x1b\[.*?m', '', log_entries)

# Split the log entries into lines
lines = log_entries.split("\n")

# Define a pattern for the date, time, and message
pattern = r"(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2}) (.*)"

# Initialize a dictionary to track message occurrences
message_occurrences = {}

# Initialize an empty list to hold the parsed log entries
parsed_log_entries = []

# Loop over each line
for line in lines:
    # If the line is not empty
    if line:
        # Search for the pattern in the line
        match = re.search(pattern, line)

        # If a match is found
        if match:
            # Extract the date, time, and message
            date, time, message = match.groups()

            # Increment the message occurrence count
            if message in message_occurrences:
                message_occurrences[message] += 1
            else:
                message_occurrences[message] = 1

            # Temporarily add the parsed log entry to the list
            parsed_log_entries.append({
                "datetime": datetime.strptime(
                    f"{date} {time}", '%Y-%m-%d %H:%M:%S'
                ),
                "message": message
            })

# Filter out entries with messages that occur more than once
filtered_log_entries = [
    entry for entry in parsed_log_entries if message_occurrences[
        entry["message"]
    ] == 1
]

# Assuming the total expected time is the time of the last log entry
total_time = filtered_log_entries[-1]['datetime']

# Calculate the percent complete for each log entry
for entry in filtered_log_entries:
    elapsed_time = entry['datetime']
    percent_complete = (
        (elapsed_time - filtered_log_entries[0]['datetime']).total_seconds()
    ) / (
        (total_time - filtered_log_entries[0]['datetime']).total_seconds()
    ) * 100
    entry['percent_complete'] = round(percent_complete, 2)

# Create a new list with only the message and percent_complete fields
output_data = [
    {
        "message": entry["message"],
        "percent_complete": entry["percent_complete"]
    } for entry in filtered_log_entries
]

# Convert the output data to JSON
json_data = json.dumps(output_data)

# Write the JSON data to a file
with open('cowbat_percent_complete.json', 'w', encoding='utf-8') as file:
    file.write(json_data)
