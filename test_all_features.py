#!/usr/bin/env python3
"""
Comprehensive test script for all new MCP debugging tools.
Tests: string mutations, state transitions, dependencies, and precision loss.
"""

class DataProcessor:
    def __init__(self):
        self.state = "INIT"
        self.buffer = ""
        self.counter = 0
        self.total = 0.0
        
    def process_item(self, item):
        """Process an item through state machine."""
        # State transitions
        if self.state == "INIT":
            self.state = "PROCESSING"
        elif self.state == "PROCESSING" and self.counter > 3:
            self.state = "FINALIZING"
        
        # String mutations
        self.buffer = self.buffer.strip()
        self.buffer = self.buffer.upper()
        self.buffer += f"_ITEM_{item}"
        
        # Numerical calculations with precision issues
        value = item * 1.1
        rounded = round(value, 2)
        self.total += rounded
        
        # Variable dependencies
        tax_rate = 0.08
        tax = rounded * tax_rate
        final_amount = rounded + tax
        
        self.counter += 1
        
        return final_amount

def main():
    processor = DataProcessor()
    results = []
    
    # Test data
    items = [10.5, 20.3, 30.7, 15.9, 25.1]
    
    for item in items:
        result = processor.process_item(item)
        results.append(result)
        print(f"Processed {item}: result={result:.2f}, state={processor.state}")
    
    # Calculate summary (more dependencies)
    total_results = sum(results)
    average = total_results / len(results)
    variance = sum((r - average) ** 2 for r in results) / len(results)
    
    print(f"\nSummary:")
    print(f"  Total: {total_results:.2f}")
    print(f"  Average: {average:.2f}")
    print(f"  Variance: {variance:.2f}")
    print(f"  Final state: {processor.state}")
    print(f"  Buffer: {processor.buffer}")
    
    return {
        'results': results,
        'total': total_results,
        'average': average,
        'variance': variance,
        'final_state': processor.state
    }

if __name__ == "__main__":
    main()