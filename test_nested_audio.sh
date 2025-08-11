#!/bin/bash

# Activate the aXaTT conda environment
source $(conda info --base)/etc/profile.d/conda.sh
conda activate aXaTT

# Test script for nested structure exploration with audio
echo "Starting auto-debugger with nested structure test"
echo "Features:"
echo "  - Manual stepping mode"
echo "  - Audio feedback with interactive exploration"
echo "  - Web interface for control"
echo ""
echo "Commands during playback:"
echo "  - Enter: Step to next line"
echo "  - 'explore': Start interactive exploration of variables"
echo "  - 'a': Switch to auto mode"
echo "  - 'c': Continue execution"
echo "  - 'q': Quit"
echo ""
echo "During exploration:"
echo "  - Type 'y' to explore deeper into nested structures"
echo "  - Type 'n' to skip to the next item"
echo ""

# Run the auto-debugger with the nested structures test
python -m autodebugger run \
    --manual \
    --manual-web \
    --manual-audio \
    --manual-rate 210 \
    --stop \
    tests/test_nested_structures.py

echo ""
echo "Test completed!"