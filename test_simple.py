#!/usr/bin/env python3
"""Simple test without loops."""

def main():
    print("Start", flush=True)
    x = 1
    y = 2
    z = x + y
    print(f"Result: {z}", flush=True)
    print("End", flush=True)

if __name__ == "__main__":
    main()