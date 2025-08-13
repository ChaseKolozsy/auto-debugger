#!/bin/bash

# Activate the aXaTT conda environment
source $(conda info --base)/etc/profile.d/conda.sh
conda activate aXaTT

echo "Testing skip-to-next-file with calculator example"
echo "----------------------------------------"
echo "This test uses multiple files (main.py -> calculator.py -> ops/*.py)"
echo ""
echo "Press 't' to skip to the next file during debugging"
echo "The debugger will fast-forward until it enters a different file"
echo ""

# Run the calculator test with manual mode, web interface, and audio
python -m autodebugger run \
    --manual \
    --manual-web \
    --manual-audio \
    --stop \
    tests/calculator/main.py