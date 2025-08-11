#!/bin/bash

# Activate the aXaTT conda environment
source $(conda info --base)/etc/profile.d/conda.sh
conda activate aXaTT

# Test script for auto-debugger with calculator in audio/manual mode
# This tests the calculator script with:
# - Manual stepping mode
# - Web interface for control
# - Audio feedback for line execution and variable changes

echo "Starting auto-debugger test with calculator script"
echo "Features enabled:"
echo "  - Manual stepping mode (press Enter to step)"
echo "  - Web interface (port will be shown when started)"
echo "  - Audio feedback with TTS (system default voice)"
echo ""
echo "Controls:"
echo "  - Enter: Step to next line"
echo "  - 'a': Switch to auto mode"
echo "  - 'c': Continue execution"
echo "  - 'q': Quit"
echo "  - 'm': Toggle audio on/off"
echo "  - 'f': Read function context (when inside a function)"
echo ""

# Run the auto-debugger with all the requested features
python -m autodebugger run \
    --manual \
    --manual-web \
    --manual-audio \
    --manual-rate 210 \
    --stop \
    tests/calculator/main.py

echo ""
echo "Test completed!"