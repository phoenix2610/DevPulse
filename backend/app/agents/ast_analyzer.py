import ast

COMPLEXITY_LIMIT = 15
LENGTH_LIMIT = 80
PARAM_LIMIT = 7

_BRANCHES = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try, ast.With, ast.AsyncWith, ast.IfExp,
             ast.ExceptHandler, ast.comprehension, ast.Assert, ast.Match)


def _name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_name(node.value)}.{node.attr}"
    return ""


def _kw(call: ast.Call, name: str) -> ast.AST | None:
    return next((k.value for k in call.keywords if k.arg == name), None)


def _is_true(node: ast.AST | None) -> bool:
    return isinstance(node, ast.Constant) and node.value is True


def _is_dynamic_str(node: ast.AST) -> bool:
    return (isinstance(node, ast.JoinedStr) and any(isinstance(v, ast.FormattedValue) for v in node.values)) or \
        (isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Mod, ast.Add))) or \
        (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "format")


def complexity(fn: ast.AST) -> int:
    n = 1
    for node in ast.walk(fn):
        if isinstance(node, _BRANCHES):
            n += 1
        elif isinstance(node, ast.BoolOp):
            n += len(node.values) - 1
    return n


class _Visitor(ast.NodeVisitor):
    def __init__(self, path: str):
        self.path, self.issues = path, []

    def add(self, node, sev, cat, title, desc, sugg, ref, end=None):
        self.issues.append({"file_path": self.path, "line_start": node.lineno, "line_end": end or node.lineno,
                            "severity": sev, "category": cat, "agent": "static-ast", "title": title,
                            "description": desc, "suggestion": sugg, "rule_ref": ref, "source": "static"})

    def visit_Call(self, node: ast.Call):
        fn = _name(node.func)
        if fn in ("eval", "exec"):
            self.add(node, "high", "security", f"Use of {fn}()", f"{fn}() executes arbitrary code.",
                     "Use ast.literal_eval or an explicit dispatch table.", "A03 Injection")
        elif fn in ("os.system", "os.popen"):
            self.add(node, "high", "security", f"{fn} call", "Runs a shell command; vulnerable to command injection.",
                     "Use subprocess.run([...]) with an argument list.", "A03 Injection")
        elif fn.startswith("subprocess.") and _is_true(_kw(node, "shell")):
            self.add(node, "high", "security", "subprocess with shell=True", "Shell interpretation enables command injection.",
                     "Pass an argument list and drop shell=True.", "A03 Injection")
        elif fn in ("pickle.load", "pickle.loads", "cPickle.loads", "marshal.loads"):
            self.add(node, "high", "security", "Unsafe deserialization", f"{fn} can execute code from crafted input.",
                     "Use json or a schema-validated format for untrusted data.", "A08 Software and Data Integrity Failures")
        elif fn == "yaml.load" and "SafeLoader" not in ast.unparse(_kw(node, "Loader") or ast.Constant(None)):
            self.add(node, "high", "security", "yaml.load without SafeLoader", "Can construct arbitrary Python objects.",
                     "Use yaml.safe_load().", "A08 Software and Data Integrity Failures")
        elif fn in ("hashlib.md5", "hashlib.sha1") and not (_kw(node, "usedforsecurity") is not None
                                                           and isinstance(_kw(node, "usedforsecurity"), ast.Constant)
                                                           and _kw(node, "usedforsecurity").value is False):
            self.add(node, "medium", "security", "Weak hash algorithm", f"{fn} is not collision resistant.",
                     "Use hashlib.sha256 (and argon2/bcrypt for passwords).", "A02 Cryptographic Failures")
        elif fn.split(".")[-1] in ("get", "post", "put", "delete", "patch", "request") and \
                isinstance(_kw(node, "verify"), ast.Constant) and _kw(node, "verify").value is False:
            self.add(node, "high", "security", "TLS verification disabled", "verify=False allows MITM attacks.",
                     "Keep certificate verification on.", "A02 Cryptographic Failures")
        elif fn.split(".")[-1] in ("execute", "executemany", "raw") and node.args and _is_dynamic_str(node.args[0]):
            self.add(node, "critical", "security", "SQL built from dynamic string",
                     "Query text is assembled from variables; SQL injection risk.",
                     "Use parameter placeholders: cursor.execute(sql, params).", "A03 Injection")
        self.generic_visit(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler):
        if node.type is None:
            self.add(node, "low", "bug", "Bare except", "Catches SystemExit/KeyboardInterrupt and hides bugs.",
                     "Catch specific exception types.", "Error handling")
        elif len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
            self.add(node, "low", "bug", "Exception swallowed", "Errors are silently ignored.",
                     "Log or handle the exception explicitly.", "Error handling")
        self.generic_visit(node)

    def _function(self, node):
        end = getattr(node, "end_lineno", node.lineno)
        cc = complexity(node)
        if cc > COMPLEXITY_LIMIT:
            self.add(node, "medium", "architecture", f"High cyclomatic complexity in `{node.name}`",
                     f"Complexity {cc} (limit {COMPLEXITY_LIMIT}).", "Split into smaller functions.",
                     "Function size and complexity", end)
        if end - node.lineno + 1 > LENGTH_LIMIT:
            self.add(node, "low", "architecture", f"Long function `{node.name}`", f"{end - node.lineno + 1} lines.",
                     "Extract helpers.", "Function size and complexity", end)
        n_params = len(node.args.args) + len(node.args.kwonlyargs) - (1 if node.args.args and node.args.args[0].arg in ("self", "cls") else 0)
        if n_params > PARAM_LIMIT:
            self.add(node, "low", "architecture", f"Too many parameters in `{node.name}`", f"{n_params} parameters.",
                     "Group related parameters into a dataclass.", "Function size and complexity")
        for d in node.args.defaults + [d for d in node.args.kw_defaults if d is not None]:
            if isinstance(d, (ast.List, ast.Dict, ast.Set)):
                self.add(d, "medium", "bug", "Mutable default argument", "Default is shared across calls.",
                         "Default to None and create the object inside the function.", "Error handling")
        self.generic_visit(node)

    visit_FunctionDef = _function
    visit_AsyncFunctionDef = _function


def analyze_python(path: str, source: str) -> list[dict]:
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError) as e:
        return [{"file_path": path, "line_start": getattr(e, "lineno", None) or 1, "line_end": getattr(e, "lineno", None) or 1,
                 "severity": "high", "category": "bug", "agent": "static-ast", "title": "Python syntax error",
                 "description": str(getattr(e, "msg", e)), "suggestion": "Fix the syntax error.", "rule_ref": None,
                 "source": "static"}]
    v = _Visitor(path)
    v.visit(tree)
    return v.issues
