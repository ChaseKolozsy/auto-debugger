#!/usr/bin/env python3
"""Simple loop test."""

# Simple loop that should be caught
counter = 0
while True:
    counter += 1
    print(f"Iteration {counter}")
    if counter > 100:  # Won't reach this if max iterations is lower
        break

print("Done")