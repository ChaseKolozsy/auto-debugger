#!/bin/bash

echo "🚀 Installing Roo Bridge in Cursor"
echo "=================================="

# Build the extension
echo "Building extension..."
cd extension
npm run compile
cd ..

# Create VSIX package
echo "Creating extension package..."
cd extension
npx vsce package --no-dependencies
cd ..

echo ""
echo "✅ Extension built!"
echo ""
echo "NOW DO THIS MANUALLY IN CURSOR:"
echo "1. Press Cmd+Shift+P (Mac) or Ctrl+Shift+P (Windows/Linux)"
echo "2. Type: 'Extensions: Install from VSIX...'"
echo "3. Select: extension/roo-bridge-test-0.0.1.vsix"
echo "4. Reload Cursor when prompted"
echo ""
echo "Then run: python3 test_roo_connection.py"