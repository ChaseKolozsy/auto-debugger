from dataclasses import dataclass
from calculator.ops.add import Add
from calculator.ops.subtract import Subtract
from calculator.ops.multiply import Multiply
from calculator.ops.divide import Divide

@dataclass
class Calculator:
    adder: Add
    subtractor: Subtract
    multiplier: Multiply
    divider: Divide

    def add(self, a, b):
        return self.adder.compute(a, b)

    def subtract(self, a, b):
        return self.subtractor.compute(a, b)

    def multiply(self, a, b):
        return self.multiplier.compute(a, b)

    def divide(self, a, b):
        return self.divider.compute(a, b)
