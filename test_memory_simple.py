#!/usr/bin/env python3
"""Simple memory test that quickly allocates memory."""

# Allocate 100 MB immediately
print("Allocating 100 MB...", flush=True)
big_list = [0] * (100 * 1024 * 1024 // 8)  # ~100 MB of integers
print(f"Allocated list with {len(big_list)} elements", flush=True)

# Do some work
total = sum(big_list[:1000])
print(f"Sum of first 1000 elements: {total}", flush=True)

print("Done", flush=True)