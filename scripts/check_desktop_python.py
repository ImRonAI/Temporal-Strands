"""Read-only AST checks used by the Kilo hook and desktop guard command.

Input is an explicit map of nonsecret source files, never imported/executed.
These are structural checks, not proof of browser, vision, or VNC correctness.
"""

import ast
from functools import cache
import importlib
import inspect
import json
import sys


# Only these installed public exports may be imported, never source-map modules.
# This is an import boundary, not a copy of the libraries' parameter contracts.
CONTRACT_TARGETS = {
    "temporalio.contrib.strands.workflow.activity_as_tool": ("temporalio.contrib.strands.workflow", "activity_as_tool"),
    "temporalio.contrib.strands.workflow.activity_as_hook": ("temporalio.contrib.strands.workflow", "activity_as_hook"),
    "temporalio.worker.Worker": ("temporalio.worker", "Worker"),
    "temporalio.client.Client.connect": ("temporalio.client", "Client.connect"),
    "temporalio.contrib.strands.StrandsPlugin": ("temporalio.contrib.strands", "StrandsPlugin"),
    "temporalio.contrib.strands.TemporalAgent": ("temporalio.contrib.strands", "TemporalAgent"),
    # Some source may use this location; warn if the installed SDK lacks it.
    "temporalio.contrib.strands.workflow.TemporalAgent": ("temporalio.contrib.strands.workflow", "TemporalAgent"),
}
BROWSER_INPUT_MODULE = "strands_tools.browser.models"
BROWSER_INPUT_NAME = f"{BROWSER_INPUT_MODULE}.BrowserInput"


@cache
def _browser_input_model():
    try:
        model = importlib.import_module(BROWSER_INPUT_MODULE).BrowserInput
        pydantic = importlib.import_module("pydantic")
        if not inspect.isclass(model) or not issubclass(model, pydantic.BaseModel):
            raise TypeError("BrowserInput is not an installed Pydantic model")
        return model, pydantic.ValidationError, None
    except Exception as error:
        return None, None, f"Cannot load installed {BROWSER_INPUT_NAME}: {type(error).__name__}: {error}"


@cache
def _contract_signature(name):
    try:
        module, attribute = CONTRACT_TARGETS[name]
        target = importlib.import_module(module)
        for part in attribute.split("."):
            target = getattr(target, part)
        if not (inspect.isfunction(target) or inspect.ismethod(target) or inspect.isclass(target)):
            raise TypeError("dynamic callable does not expose a verified native function or class")
        signature = inspect.signature(target, follow_wrapped=False, eval_str=False)
        if signature.parameters and all(
            parameter.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
            for parameter in signature.parameters.values()
        ):
            raise ValueError("signature has only variadic parameters; no meaningful explicit contract")
        return signature, None
    except Exception as error:
        # Cache failures as well as successes, but report them at every affected call.
        return None, f"Cannot inspect installed {name}: {type(error).__name__}: {error}"


def check_contracts(files: dict[str, str]) -> list[dict]:
    """Check native signatures and fully literal BrowserInput validation calls.

    Unpacking, rebound names, wildcard imports and dynamic tools are not proof of
    an invalid call. BrowserInput payloads/options must all be literal; skipped
    payload validation emits a warning, not a success. No browser action is run.
    Syntax diagnostics remain the responsibility of check().
    """
    findings = []
    scopes = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda,
              ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)

    def dotted(node, aliases):
        if isinstance(node, ast.Name):
            return aliases.get(node.id)
        if isinstance(node, ast.Attribute):
            parent = dotted(node.value, aliases)
            return f"{parent}.{node.attr}" if parent else None
        return None

    def nodes_in_scope(node):
        yield node
        if isinstance(node, scopes):
            # Definition headers execute in the enclosing scope, not the body.
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                headers = [*node.args.defaults, *node.args.kw_defaults]
                if not isinstance(node, ast.Lambda):
                    headers.extend(node.decorator_list)
            elif isinstance(node, ast.ClassDef):
                headers = [*node.bases, *node.keywords, *node.decorator_list]
            else:
                headers = [node.generators[0].iter]
            for header in headers:
                if header is not None:
                    yield from nodes_in_scope(header)
            return
        for child in ast.iter_child_nodes(node):
            yield from nodes_in_scope(child)

    def scan(scope, inherited, path):
        if isinstance(scope, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            roots = scope.body
        elif isinstance(scope, ast.Lambda):
            roots = [scope.body]
        else:
            roots = [scope.key, scope.value] if isinstance(scope, ast.DictComp) else [scope.elt]
            for index, generator in enumerate(scope.generators):
                roots.extend([generator.target, *generator.ifs])
                if index:
                    roots.append(generator.iter)
        nodes = [node for root in roots for node in nodes_in_scope(root)]
        aliases = dict(inherited)
        imports = {}
        rebound = set()
        wildcard = False
        if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            rebound.update(node.arg for node in ast.walk(scope.args) if isinstance(node, ast.arg))
        for node in nodes:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    if alias.name == "*":
                        wildcard = True
                        continue
                    if isinstance(node, ast.ImportFrom):
                        local = alias.asname or alias.name
                        name = f"{node.module}.{alias.name}" if not node.level else None
                    else:
                        local = alias.asname or alias.name.split(".")[0]
                        name = alias.name if alias.asname else local
                    imports[local] = name if local not in imports or imports[local] == name else None
            elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
                rebound.add(node.id)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                rebound.add(node.name)
            elif isinstance(node, (ast.Global, ast.Nonlocal)):
                rebound.update(node.names)
            elif isinstance(node, ast.ExceptHandler) and node.name:
                rebound.add(node.name)
            elif isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name:
                rebound.add(node.name)
            elif isinstance(node, ast.MatchMapping) and node.rest:
                rebound.add(node.rest)
            elif isinstance(node, ast.Attribute) and isinstance(node.ctx, (ast.Store, ast.Del)):
                root = node.value
                while isinstance(root, ast.Attribute):
                    root = root.value
                if isinstance(root, ast.Name):
                    rebound.add(root.id)
        aliases.update(imports)
        for name in rebound:
            aliases.pop(name, None)
        if wildcard:
            aliases.clear()
        for node in nodes:
            if isinstance(node, scopes):
                # Methods and nested classes do not close over class locals.
                scan(node, inherited if isinstance(scope, ast.ClassDef) else aliases, path)
            if not isinstance(node, ast.Call):
                continue
            name = dotted(node.func, aliases)
            if name in (BROWSER_INPUT_NAME, f"{BROWSER_INPUT_NAME}.model_validate"):
                rule, severity = "python-contract-unavailable", "warning"
                try:
                    if any(isinstance(arg, ast.Starred) for arg in node.args) or any(
                        keyword.arg is None for keyword in node.keywords
                    ):
                        raise ValueError("argument unpacking is not statically complete")
                    args = [ast.literal_eval(arg) for arg in node.args]
                    kwargs = {keyword.arg: ast.literal_eval(keyword.value) for keyword in node.keywords}
                except (ValueError, TypeError, SyntaxError, RecursionError):
                    message = f"{name}: payload validation skipped; all arguments must be literal without unpacking"
                else:
                    model, validation_error, message = _browser_input_model()
                    if not message:
                        try:
                            # Invoke only the fixed installed data model, never a
                            # source callable, tool, action, or application wrapper.
                            target = model if name == BROWSER_INPUT_NAME else model.model_validate
                            target(*args, **kwargs)
                        except validation_error as error:
                            details = "; ".join(
                                f"{'.'.join(str(part) for part in item['loc']) or '<root>'}: "
                                f"{item['msg']} ({item['type']})"
                                for item in error.errors(include_url=False, include_context=False, include_input=False)
                            )
                            rule, severity = "python-call-contract", "error"
                            message = f"{name}: {details}"
                        except TypeError as error:
                            rule, severity = "python-call-contract", "error"
                            message = f"{name}: {error}"
                        except Exception as error:
                            message = f"Cannot validate installed {name}: {type(error).__name__}: {error}"
                if message:
                    findings.append({"file": path, "line": node.lineno, "rule": rule,
                                     "message": message, "severity": severity})
                continue
            if name not in CONTRACT_TARGETS:
                continue
            signature, warning = _contract_signature(name)
            if warning:
                findings.append({"file": path, "line": node.lineno,
                                 "rule": "python-contract-unavailable", "message": warning,
                                 "severity": "warning"})
                continue
            if any(isinstance(arg, ast.Starred) for arg in node.args) or any(
                keyword.arg is None for keyword in node.keywords
            ):
                continue
            sentinel = object()
            try:
                signature.bind(*[sentinel for _ in node.args],
                               **{keyword.arg: sentinel for keyword in node.keywords})
            except TypeError as error:
                findings.append({"file": path, "line": node.lineno,
                                 "rule": "python-call-contract", "message": f"{name}: {error}",
                                 "severity": "error"})

    for path, source in files.items():
        if not path.endswith(".py"):
            continue
        try:
            tree = ast.parse(source, filename=path)
        except SyntaxError:
            continue
        scan(tree, {}, path)
    return findings


def check(files: dict[str, str]) -> list[dict]:
    findings = []
    trees = {}
    symbols = {}
    imports = {}

    def report(path, node, rule, message):
        findings.append({"file": path, "line": getattr(node, "lineno", 1),
                         "rule": rule, "message": message})

    def dotted(node, aliases):
        if isinstance(node, ast.Name):
            return aliases.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            return f"{dotted(node.value, aliases)}.{node.attr}"
        return ""

    for path, source in files.items():
        if not path.endswith(".py"):
            continue
        try:
            tree = ast.parse(source, filename=path)
        except SyntaxError as error:
            report(path, error, "python-syntax", "Source must parse before desktop contracts can be checked")
            continue
        trees[path] = tree
        aliases = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for name in node.names:
                    aliases[name.asname or name.name] = f"{node.module}.{name.name}"
            elif isinstance(node, ast.Import):
                for name in node.names:
                    aliases[name.asname or name.name.split(".")[0]] = name.name if name.asname else name.name.split(".")[0]
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        symbols[(path, target.id)] = node.value
        imports[path] = aliases
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for base in node.bases:
                    name = dotted(base, aliases)
                    if (name.startswith("strands_tools.browser.") or name.startswith("temporalio.contrib.strands.")) and name.rsplit(".", 1)[-1] in {
                        "Browser", "LocalChromiumBrowser", "TemporalAgent", "TemporalActivityTool",
                    }:
                        report(path, node, "framework-facade", f"Use {name} directly; do not subclass it to wrap the desktop framework")
            if isinstance(node, ast.Call):
                for keyword in node.keywords:
                    if keyword.arg == "headless" and isinstance(keyword.value, ast.Constant) and keyword.value.value is True:
                        report(path, node, "headed-desktop", "Headless launch is not the headed Linux desktop")
                name = dotted(node.func, aliases)
                if name.endswith((".getenv", ".get")) and len(node.args) >= 2:
                    if isinstance(node.args[0], ast.Constant) and node.args[0].value == "STRANDS_BROWSER_HEADLESS":
                        if isinstance(node.args[1], ast.Constant) and str(node.args[1].value).lower() in {"true", "1"}:
                            report(path, node, "headed-desktop", "Do not default STRANDS_BROWSER_HEADLESS to true")
            if isinstance(node, ast.Dict):
                for key, value in zip(node.keys, node.values):
                    if isinstance(key, ast.Constant) and key.value == "headless" and isinstance(value, ast.Constant) and value.value is True:
                        report(path, value, "headed-desktop", "Headless launch option violates the desktop contract")
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if node.value == "--no-sandbox":
                    report(path, node, "browser-sandbox", "Do not disable the Chromium sandbox")
                if "computer-use-live.html" in node.value or node.value == "Page.startScreencast":
                    report(path, node, "novnc-viewer", "Use the noVNC iframe, not the removed CDP screenshot player")

    def resolve(node, path, seen=()):
        if isinstance(node, ast.Name):
            key = (path, node.id)
            if key in seen:
                return node
            value = symbols.get(key)
            if value is None:
                key = ("orchestrator/config.py", node.id)
                value = symbols.get(key)
            if value is not None:
                return resolve(value, key[0], (*seen, key))
        return node

    def options(node, path):
        node = resolve(node, path)
        result = {}
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if key is None:
                    result.update(options(value, path))
                elif isinstance(key, ast.Constant):
                    result[key.value] = resolve(value, path)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "dict":
            result.update(call_options(node, path))
        return result

    def call_options(node, path):
        result = {}
        for keyword in node.keywords:
            if keyword.arg is None:
                result.update(options(keyword.value, path))
            else:
                result[keyword.arg] = resolve(keyword.value, path)
        return result

    browser_path = "orchestrator/browser_activity.py"
    tree = trees.get(browser_path)
    if tree is not None:
        aliases = imports[browser_path]
        stock = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                 and dotted(node.func, aliases) in {"strands_tools.browser.LocalChromiumBrowser", "strands_tools.browser.local_chromium_browser.LocalChromiumBrowser"}]
        activities = [node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                      and node.name == "browser_activity"]
        if not stock or not activities:
            report(browser_path, tree, "stock-browser-activity", "Keep the stock LocalChromiumBrowser and native browser_activity binding")
        for node in activities:
            decorated = any(isinstance(decorator, ast.Call) and dotted(decorator.func, aliases) == "temporalio.activity.defn"
                            for decorator in node.decorator_list)
            forwarded = any(isinstance(call, ast.Call) and dotted(call.func, aliases).endswith(".browser")
                            and any(kw.arg == "browser_input" and isinstance(kw.value, ast.Name) and kw.value.id == "browser_input"
                                    for kw in call.keywords) for call in ast.walk(node))
            args = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
            typed = any(arg.arg == "browser_input" and dotted(arg.annotation, aliases) == "strands_tools.browser.models.BrowserInput" for arg in args)
            if not decorated or not forwarded or not typed:
                report(browser_path, node, "stock-browser-activity", "Expose typed native BrowserInput through @activity.defn and forward browser_input unchanged to the stock tool")

    workflow_path = "orchestrator/workflow.py"
    tree = trees.get(workflow_path)
    if tree is not None:
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
        browser = [node for node in calls if dotted(node.func, {}).endswith("activity_as_tool") and node.args
                   and dotted(node.args[0], {}).rsplit(".", 1)[-1] == "browser_activity"]
        if not browser:
            report(workflow_path, tree, "native-browser-routing", "Missing native activity_as_tool(browser_activity, task_queue=...) binding")
        for call in browser:
            args = call_options(call, workflow_path)
            queue = args.get("task_queue")
            if not isinstance(queue, ast.Constant) or queue.value != "desktop-browser":
                report(workflow_path, call, "native-browser-routing", "Browser activity must route to the desktop-browser worker, not the host")
            retry = args.get("retry_policy")
            attempt = call_options(retry, workflow_path).get("maximum_attempts") if isinstance(retry, ast.Call) else None
            if not isinstance(attempt, ast.Constant) or type(attempt.value) is not int or attempt.value != 1:
                report(workflow_path, call, "mutation-retry", "Browser mutations require a verified RetryPolicy(maximum_attempts=1)")
        think = [node for node in calls if dotted(node.func, {}).endswith("activity_as_tool") and node.args
                 and dotted(node.args[0], {}) == "think_activity.think"]
        if not think or not any(isinstance(node, ast.Name) and node.id == "THINK_TOOL" and isinstance(node.ctx, ast.Load) for node in ast.walk(tree)):
            report(workflow_path, tree, "preserve-think", "Preserve the native Think activity binding and its use by the Perplexity agent")
        rollover = [node for node in calls if dotted(node.func, {}).endswith(".continue_as_new")]
        carried = [node for call in rollover for node in ast.walk(call) if isinstance(node, ast.Call) and dotted(node.func, {}) == "ChatInput"]
        if not carried:
            report(workflow_path, tree, "continue-context", "Continue-As-New must explicitly carry ChatInput context")
        for call in carried:
            fields = {keyword.arg: keyword.value for keyword in call.keywords}
            required = {"session_id": "self._session_id", "resume_prompt": "self._resume_prompt"}
            if any(dotted(fields.get(key), {}) != value for key, value in required.items()) or not {"messages", "stream_state"}.issubset(fields):
                report(workflow_path, call, "continue-context", "Carry the same session_id, messages, stream_state and resume_prompt through rollover")

    host_path = "orchestrator/run_worker.py"
    tree = trees.get(host_path)
    if tree is not None:
        registry = symbols.get((host_path, "WORKER_ACTIVITIES"))
        if registry is None:
            report(host_path, tree, "host-activity-registry", "Cannot verify host registrations: WORKER_ACTIVITIES is missing")
        else:
            for node in ast.walk(registry):
                name = dotted(node, {})
                if name == "browser_activity" or name.endswith(".COMPUTER_USE_ACTIVITIES"):
                    report(host_path, node, "host-desktop-execution", "Desktop/browser activities must execute inside Linux, not on the host worker")

    desktop_path = "orchestrator/desktop_worker.py"
    tree = trees.get(desktop_path)
    if tree is not None:
        workers = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and dotted(node.func, {}).rsplit(".", 1)[-1] == "Worker"]
        if not workers:
            report(desktop_path, tree, "desktop-worker", "Missing native Temporal Worker")
        for node in workers:
            opts = call_options(node, desktop_path)
            queue = opts.get("task_queue")
            if not isinstance(queue, ast.Constant) or queue.value != "desktop-browser" or "activity_executor" not in opts:
                report(desktop_path, node, "desktop-worker", "Desktop worker needs the desktop-browser queue and a native executor for sync activities")

    return findings


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contracts", action="store_true",
                        help="Check installed signatures and literal BrowserInput payloads instead of structural rules")
    args = parser.parse_args()
    checker = check_contracts if args.contracts else check
    print(json.dumps(checker(json.load(sys.stdin))))
