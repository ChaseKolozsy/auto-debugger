#!/bin/bash

echo "🚀 Roo Bridge Test - Installation & Test Script"
echo "=============================================="

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Step 1: Build the extension
echo -e "\n${YELLOW}Step 1: Building VS Code extension...${NC}"
cd extension
npm install
npx tsc -p .
cd ..

if [ -d "extension/out" ]; then
    echo -e "${GREEN}✅ Extension built successfully${NC}"
else
    echo -e "${RED}❌ Extension build failed${NC}"
    exit 1
fi

# Step 2: Install the extension
echo -e "\n${YELLOW}Step 2: Installing extension in VS Code...${NC}"
code --install-extension ./extension

echo -e "${GREEN}✅ Extension installed${NC}"

# Step 3: Open VS Code
echo -e "\n${YELLOW}Step 3: Opening VS Code...${NC}"
code .

echo -e "${YELLOW}⏰ Waiting 5 seconds for VS Code to start...${NC}"
sleep 5

# Step 4: Run the Python test
echo -e "\n${YELLOW}Step 4: Running Python test...${NC}"
python3 test_roo_connection.py

echo -e "\n${GREEN}Test complete!${NC}"
echo "Check VS Code for notifications and terminal output"