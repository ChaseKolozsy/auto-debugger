# Manual Test Instructions for Audio Features

## Setup
1. The debugger is currently running at http://127.0.0.1:65301
2. Open this URL in your browser

## Test Steps

### Test 1: Audio Toggle with 'm' key
1. Press 'm' key on your keyboard
2. The audio button should toggle between "🔊 Audio On" and "🔇 Audio Off"
3. Press 'm' again to toggle back

### Test 2: Function Body Reading with 'f' key
1. Make sure audio is ON (press 'm' if needed to turn it on)
2. Step through the code by pressing Enter key several times until you're inside a function
   - The "Function:" field should show a function name (not "-")
3. Press 'f' key
   - The function context should appear visually below the code
   - You should hear the function being read aloud including:
     - "In function [name]"
     - "Signature: [function signature]"  
     - "Body: [function body code]"

### Test 3: Audio Toggle Button
1. Click the "🔊 Audio On" / "🔇 Audio Off" button
2. It should toggle the audio state
3. The button appearance should change

## Expected Audio Output
When pressing 'f' inside a function with audio on, you should hear something like:
"In function greet. Signature: def greet(name):. Body: message = f\"Hello, {name}!\" [etc...]"

## Notes
- The function body preview is limited to the first 5 lines for performance
- Audio must be enabled for the function to be read aloud
- The 'm' key provides a quick keyboard shortcut for audio toggle