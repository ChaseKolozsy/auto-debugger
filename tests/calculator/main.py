
try:
    from calculator import Calculator
    from calculator.ops.add import Add
    from calculator.ops.subtract import Subtract
    from calculator.ops.multiply import Multiply
    from calculator.ops.divide import Divide
except ImportError:
    from calculator import Calculator
    from ops.add import Add
    from ops.subtract import Subtract
    from ops.multiply import Multiply
    from ops.divide import Divide

def compute_demo():
    calc = Calculator(Add(), Subtract(), Multiply(), Divide())
    a = calc.add(2, 3)
    b = calc.multiply(a, 10)
    c = calc.subtract(b, 5)
    d = calc.divide(c, 3)
    return d

def main():
    result = compute_demo()
    print(f"Result: {result}")

if __name__ == "__main__":
    main()
