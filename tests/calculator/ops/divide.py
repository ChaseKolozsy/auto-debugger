class Divide:
    def apply(self, a: float, b: float) -> float:
        if b == 0:
            raise ZeroDivisionError("division by zero")
        return a / b
