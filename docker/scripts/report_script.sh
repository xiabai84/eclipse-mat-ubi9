#!/bin/bash

# Extract the part of "Problem Suspect" from Eclipse-MAT zip file
# Usage: ./script.sh <zip_file> <output_file>

set -e
trap 'rm -rf "$temp_dir"' EXIT

# Check parameters
if [ $# -ne 2 ]; then
    echo "Usage: $0 <zip_file> <output_file>"
    echo "Example: $0 archive.zip results.txt"
    exit 1
fi

zip_file="$1"
output_file="$2"

# Validate input
[ -f "$zip_file" ] || { echo "Error: File does not exist: $zip_file"; exit 1; }
command -v unzip >/dev/null || { echo "Error: unzip command not found"; exit 1; }

# Create temporary directory
temp_dir=$(mktemp -d) || { echo "Error: Unable to create temporary directory"; exit 1; }

# Extract and process
echo "Processing $zip_file..."
if unzip -q "$zip_file" "index.html" -d "$temp_dir" 2>/dev/null && [ -f "$temp_dir/index.html" ]; then
    # Extract matching fields and save to temporary file first
    temp_output=$(mktemp)
    grep -o "Problem Suspect [0-9]\+" "$temp_dir/index.html" 2>/dev/null | sort -u > "$temp_output"
    
    # Check if any matches were found
    if [ -s "$temp_output" ]; then
        count=$(wc -l < "$temp_output")
        mv "$temp_output" "$output_file"
        echo "Complete! Found $count unique results, saved to $output_file"
    else
        rm -f "$temp_output"
        echo "No matching fields found. Output file not created."
        # Exit successfully but without creating output file
        exit 0
    fi
else
    echo "Error: Unable to extract index.html"
    exit 1
fi