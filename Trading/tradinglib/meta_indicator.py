import yfinance as yf
from tradinglib import market_data as md
import pandas as pd
import numpy as np
import ta
import ast
import operator

# Erlaubte Funktionen und Operatoren für sicheren Ausdruck
allowed_functions = {
    'min': min,
    'max': max,
    'mean': np.mean,
    'sum': sum
}

allowed_operators = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.USub: operator.neg
}

class SafeEval(ast.NodeVisitor):
    def __init__(self, variables):
        """Bind the evaluator to a dict of named numeric variables."""
        self.variables = variables

    def visit(self, node):
        """Recursively evaluate an AST node and return its numeric result."""
        if isinstance(node, ast.Expression):
            return self.visit(node.body)
        elif isinstance(node, ast.BinOp):
            left = self.visit(node.left)
            right = self.visit(node.right)
            return allowed_operators[type(node.op)](left, right)
        elif isinstance(node, ast.UnaryOp):
            operand = self.visit(node.operand)
            return allowed_operators[type(node.op)](operand)
        elif isinstance(node, ast.Num):
            return node.n
        elif isinstance(node, ast.Name):
            if node.id in self.variables:
                return self.variables[node.id]
            raise ValueError(f"Variable '{node.id}' nicht erlaubt.")
        elif isinstance(node, ast.Call):
            func = node.func.id
            if func in allowed_functions:
                args = [self.visit(arg) for arg in node.args]
                return allowed_functions[func](*args)
            raise ValueError(f"Funktion '{func}' nicht erlaubt.")
        elif isinstance(node, ast.IfExp):
            test = self.visit(node.test)
            return self.visit(node.body) if test else self.visit(node.orelse)
        elif isinstance(node, ast.Compare):
            left = self.visit(node.left)
            right = self.visit(node.comparators[0])
            op = node.ops[0]
            if isinstance(op, ast.Gt): return left > right
            elif isinstance(op, ast.Lt): return left < right
            elif isinstance(op, ast.Eq): return left == right
            elif isinstance(op, ast.GtE): return left >= right
            elif isinstance(op, ast.LtE): return left <= right
            elif isinstance(op, ast.NotEq): return left != right
            else: raise ValueError("Vergleichsoperator nicht erlaubt.")
        else:
            raise ValueError(f"Nicht unterstützter Ausdruck: {ast.dump(node)}")

    def eval(self, expression):
        """Parse and safely evaluate an arithmetic expression string against the variable dict."""
        tree = ast.parse(expression, mode='eval')
        return self.visit(tree)


class MetaIndicator:
    def __init__(self, ticker: str, expression: str, period: str = "6mo", interval: str = "1d"):
        """Configure the meta-indicator with a ticker, a formula string, and OHLC period/interval."""
        self.ticker = ticker
        self.expression = expression
        self.period = period
        self.interval = interval

        self.df = None
        self.info = None
        self.indicators = {}

    def load_data(self):
        """Download OHLCV history and ticker info via market_data wrappers."""
        # Prefer centralized ticker history wrapper (handles errors consistently)
        self.df = md.ticker_history(self.ticker, period=self.period, interval=self.interval)
        self.df = self.df.dropna()
        # Use centralized metadata wrapper
        self.info = md.ticker_info(self.ticker)

    def compute_indicators(self):
        """Compute RSI, MACD diff, RoA, and dividend rate into self.indicators."""
        self.indicators['rsi'] = ta.momentum.RSIIndicator(self.df['Close']).rsi().iloc[-1]
        self.indicators['macd'] = ta.trend.MACD(self.df['Close']).macd_diff().iloc[-1]
        self.indicators['roa'] = self.info.get('returnOnAssets', 0) * 100
        self.indicators['dividendRate'] = self.info.get('dividendRate', 0)

    def evaluate_expression(self):
        """Safely evaluate self.expression using the computed indicator values."""
        evaluator = SafeEval(self.indicators)
        return evaluator.eval(self.expression)

    def run(self):
        """Load data, compute indicators, and evaluate the expression; returns the numeric score."""
        self.load_data()
        self.compute_indicators()
        return self.evaluate_expression()

if __name__ == "__main__":
    # Beispiel-Test mit AAPL
    meta = MetaIndicator("AAPL", "macd * 2 + rsi + (roa if roa > 0 else 0)")
    score = meta.run()
    score
