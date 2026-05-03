from __future__ import annotations

import ast
import contextlib
import io
import json
import multiprocessing
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from functools import partial
from pathlib import Path
from urllib.parse import urlparse

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
LEVELS_PATH = BASE_DIR / "data" / "levels.json"

MAX_CODE_LENGTH = 4000
MAX_OUTPUT_LENGTH = 2000
EXECUTION_TIMEOUT = 2.0

SAFE_BUILTINS = {
    "abs": abs,
    "all": all,
    "any": any,
    "bool": bool,
    "dict": dict,
    "enumerate": enumerate,
    "float": float,
    "int": int,
    "len": len,
    "list": list,
    "max": max,
    "min": min,
    "print": print,
    "range": range,
    "round": round,
    "set": set,
    "sorted": sorted,
    "str": str,
    "sum": sum,
    "tuple": tuple,
    "zip": zip,
}

BANNED_CALLS = {
    "__import__",
    "breakpoint",
    "compile",
    "dir",
    "eval",
    "exec",
    "getattr",
    "globals",
    "help",
    "input",
    "locals",
    "open",
    "setattr",
    "vars",
}

ALLOWED_NODES = {
    ast.Module,
    ast.Expr,
    ast.Assign,
    ast.AnnAssign,
    ast.AugAssign,
    ast.Name,
    ast.Load,
    ast.Store,
    ast.Constant,
    ast.BinOp,
    ast.UnaryOp,
    ast.BoolOp,
    ast.Compare,
    ast.If,
    ast.IfExp,
    ast.For,
    ast.While,
    ast.Break,
    ast.Continue,
    ast.Return,
    ast.Call,
    ast.List,
    ast.Tuple,
    ast.Dict,
    ast.Set,
    ast.Subscript,
    ast.Slice,
    ast.FunctionDef,
    ast.arguments,
    ast.arg,
    ast.ClassDef,
    ast.keyword,
    ast.comprehension,
    ast.ListComp,
    ast.DictComp,
    ast.SetComp,
    ast.GeneratorExp,
    ast.Yield,
    ast.YieldFrom,
    ast.JoinedStr,
    ast.FormattedValue,
    ast.Pass,
    ast.Attribute,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.Mod,
    ast.Pow,
    ast.FloorDiv,
    ast.UAdd,
    ast.USub,
    ast.Not,
    ast.And,
    ast.Or,
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
    ast.In,
    ast.NotIn,
    ast.Is,
    ast.IsNot,
}


def load_levels() -> list[dict]:
    with LEVELS_PATH.open(encoding="utf-8") as handle:
        data = json.load(handle)
    if isinstance(data, dict):
        return data.get("levels", [])
    return data


LEVELS = load_levels()
LEVELS_BY_ID = {level["id"]: level for level in LEVELS}


class SafetyVisitor(ast.NodeVisitor):
    def generic_visit(self, node: ast.AST) -> None:
        if type(node) not in ALLOWED_NODES:
            raise ValueError("That syntax is not supported in this practice arena.")
        super().generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr.startswith("_"):
            raise ValueError("Private and dunder attributes are not allowed.")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name) and node.func.id in BANNED_CALLS:
            raise ValueError("That function is not allowed in this arena.")
        self.generic_visit(node)


def trim_output(output: str) -> str:
    if len(output) > MAX_OUTPUT_LENGTH:
        return output[:MAX_OUTPUT_LENGTH] + "\n...output truncated..."
    return output


def values_match(result: object, expected: object) -> bool:
    if isinstance(expected, float) and isinstance(result, (float, int)):
        return abs(result - expected) < 1e-6
    return result == expected


def run_checks(checks: list[dict], env: dict, stdout: str, tree: ast.AST) -> dict:
    messages: list[str] = []
    passed = True
    missing = object()
    for check in checks:
        check_type = check.get("type")
        if check_type == "stdout_contains":
            expected = check.get("value", "")
            if expected not in stdout:
                passed = False
                messages.append(check.get("failure", "Expected output was not found."))
        elif check_type == "variable_equals":
            name = check.get("name")
            expected = check.get("value")
            value = env.get(name, missing)
            if value is missing or not values_match(value, expected):
                passed = False
                messages.append(check.get("failure", f"{name} did not match the expected value."))
        elif check_type == "function_returns":
            name = check.get("name")
            args = check.get("args", [])
            expected = check.get("value")
            func = env.get(name)
            if not callable(func):
                passed = False
                messages.append(check.get("failure", f"{name} should be a function."))
            else:
                try:
                    result = func(*args)
                    if check.get("cast") == "list":
                        result = list(result)
                except Exception as exc:  # noqa: BLE001 - feedback for user code
                    passed = False
                    messages.append(f"{name} raised an error: {exc}")
                else:
                    if not values_match(result, expected):
                        passed = False
                        messages.append(check.get("failure", f"{name} returned an unexpected value."))
        elif check_type == "method_returns":
            class_name = check.get("class")
            method_name = check.get("method")
            init_args = check.get("init_args", [])
            call_args = check.get("args", [])
            expected = check.get("value")
            cls = env.get(class_name)
            if not isinstance(cls, type):
                passed = False
                messages.append(check.get("failure", f"{class_name} should be a class."))
            else:
                try:
                    instance = cls(*init_args)
                    method = getattr(instance, method_name)
                    result = method(*call_args)
                except Exception as exc:  # noqa: BLE001 - feedback for user code
                    passed = False
                    messages.append(f"{class_name}.{method_name} raised an error: {exc}")
                else:
                    if not values_match(result, expected):
                        passed = False
                        messages.append(check.get("failure", "The method returned an unexpected value."))
        elif check_type == "ast_contains":
            node_name = check.get("node")
            node_type = getattr(ast, node_name, None)
            if node_type is None:
                passed = False
                messages.append("A required syntax element was missing.")
            elif not any(isinstance(node, node_type) for node in ast.walk(tree)):
                passed = False
                messages.append(check.get("failure", f"Missing required syntax: {node_name}."))
    if passed and not messages:
        messages.append("Level complete! 🎉")
    return {
        "ok": True,
        "passed": passed,
        "stdout": trim_output(stdout),
        "messages": messages,
    }


def evaluate_submission(code: str, level: dict) -> dict:
    if len(code) > MAX_CODE_LENGTH:
        return {"ok": False, "error": "Your code is too long for this arena."}

    stdout_buffer = io.StringIO()
    try:
        tree = ast.parse(code, mode="exec")
        SafetyVisitor().visit(tree)
        safe_globals = {"__builtins__": SAFE_BUILTINS}
        safe_locals: dict = {}
        with contextlib.redirect_stdout(stdout_buffer):
            exec(compile(tree, "<player>", "exec"), safe_globals, safe_locals)
    except Exception as exc:  # noqa: BLE001 - feedback for user code
        return {"ok": False, "error": f"{exc.__class__.__name__}: {exc}", "stdout": stdout_buffer.getvalue()}

    env = {**safe_globals, **safe_locals}
    env.pop("__builtins__", None)
    return run_checks(level.get("checks", []), env, stdout_buffer.getvalue(), tree)


def run_submission(code: str, level: dict) -> dict:
    queue: multiprocessing.Queue = multiprocessing.Queue()
    process = multiprocessing.Process(target=_evaluate_worker, args=(code, level, queue))
    process.start()
    process.join(EXECUTION_TIMEOUT)
    if process.is_alive():
        process.terminate()
        process.join()
        return {"ok": False, "error": "Your code took too long to finish. Try simplifying it."}
    if queue.empty():
        return {"ok": False, "error": "No response from the runner. Please try again."}
    return queue.get()


def _evaluate_worker(code: str, level: dict, queue: multiprocessing.Queue) -> None:
    result = evaluate_submission(code, level)
    queue.put(result)


def public_level(level: dict) -> dict:
    return {key: value for key, value in level.items() if key != "checks"}


class GameHandler(SimpleHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - required by base class
        parsed = urlparse(self.path)
        if parsed.path == "/api/levels":
            self.send_json([public_level(level) for level in LEVELS])
            return
        if parsed.path == "/":
            self.path = "/index.html"
        super().do_GET()

    def do_POST(self) -> None:  # noqa: N802 - required by base class
        parsed = urlparse(self.path)
        if parsed.path != "/api/submit":
            self.send_error(404, "Not Found")
            return
        length = int(self.headers.get("Content-Length", 0))
        if length <= 0:
            self.send_error(400, "Missing payload")
            return
        payload = self.rfile.read(length)
        try:
            body = json.loads(payload)
        except json.JSONDecodeError:
            self.send_error(400, "Invalid JSON")
            return
        level_id = body.get("level_id")
        code = body.get("code", "")
        level = LEVELS_BY_ID.get(level_id)
        if level is None:
            self.send_error(404, "Unknown level")
            return
        result = run_submission(code, level)
        self.send_json(result)

    def send_json(self, data: object) -> None:
        payload = json.dumps(data).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003 - match base API
        return


def main() -> None:
    handler = partial(GameHandler, directory=str(STATIC_DIR))
    server = ThreadingHTTPServer(("localhost", 8000), handler)
    print("Python Quest running at http://localhost:8000")
    print("Press Ctrl+C to stop.")
    server.serve_forever()


if __name__ == "__main__":
    main()
