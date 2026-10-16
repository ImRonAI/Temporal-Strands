"""Stdlib tests against the installed SDK, without running application sources."""

import importlib
import inspect
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import check_desktop_python as checker


class PythonContractsTests(unittest.TestCase):
    def setUp(self):
        checker._contract_signature.cache_clear()

    def check_source(self, source):
        return checker.check_contracts({"example.py": source})

    def assert_contract_error(self, source, fragment):
        findings = self.check_source(source)
        self.assertEqual(len(findings), 1, findings)
        self.assertEqual(findings[0]["file"], "example.py")
        self.assertEqual(findings[0]["rule"], "python-call-contract")
        self.assertEqual(findings[0]["severity"], "error")
        self.assertIn(fragment, findings[0]["message"])
        return findings[0]

    def test_real_import_alias_and_typo(self):
        prefix = "from temporalio.contrib.strands.workflow import activity_as_tool as bind\n"
        self.assertEqual(self.check_source(prefix + "bind(activity, task_queue=queue)"), [])
        finding = self.assert_contract_error(prefix + "bind(activity, task_queu=queue)", "task_queu")
        self.assertEqual(finding["line"], 2)

    def test_real_required_positional_and_keyword_parameters(self):
        self.assert_contract_error(
            "from temporalio.contrib.strands.workflow import activity_as_tool\nactivity_as_tool()",
            "activity_fn",
        )
        self.assert_contract_error(
            "from temporalio.contrib.strands.workflow import activity_as_hook as hook\nhook(activity)",
            "activity_input",
        )
        self.assertEqual(self.check_source(
            "from temporalio.contrib.strands.workflow import activity_as_hook as hook\n"
            "hook(activity, activity_input=lambda event: event)"
        ), [])

    def test_worker_module_and_class_aliases(self):
        for prefix, name in (
            ("from temporalio.worker import Worker as W", "W"),
            ("import temporalio.worker as w", "w.Worker"),
            ("import temporalio.worker", "temporalio.worker.Worker"),
            ("from temporalio import worker as w", "w.Worker"),
        ):
            with self.subTest(prefix=prefix):
                self.assertEqual(self.check_source(f"{prefix}\n{name}(client, task_queue=queue)"), [])
                self.assert_contract_error(f"{prefix}\n{name}(client)", "task_queue")
                self.assert_contract_error(f"{prefix}\n{name}(client, task_queue=queue, typo=True)", "typo")

    def test_bound_client_connect_signature(self):
        prefix = "from temporalio.client import Client as C\n"
        self.assertEqual(self.check_source(prefix + "C.connect(host, namespace=namespace)"), [])
        self.assert_contract_error(prefix + "C.connect()", "target_host")
        self.assert_contract_error(prefix + "C.connect(host, namespac=namespace)", "namespac")

    def test_plugin_and_actual_temporal_agent_export(self):
        prefix = "from temporalio.contrib.strands import StrandsPlugin as P, TemporalAgent as A\n"
        self.assertEqual(self.check_source(prefix + "P(models=factories)\nA(model=model, tools=tools)"), [])
        self.assert_contract_error(prefix + "P(model=factories)", "model")
        # TemporalAgent's installed **agent_kwargs must allow unknown names.
        self.assertEqual(self.check_source(prefix + "A(arbitrary_native_forwarded_option=value)"), [])

    def test_missing_temporal_agent_export_warns_not_rejects(self):
        module = importlib.import_module("temporalio.contrib.strands.workflow")
        if hasattr(module, "TemporalAgent"):
            self.skipTest("Installed SDK now provides the workflow export")
        findings = self.check_source(
            "from temporalio.contrib.strands.workflow import TemporalAgent\nTemporalAgent()"
        )
        self.assertEqual(findings[0]["rule"], "python-contract-unavailable")
        self.assertEqual(findings[0]["severity"], "warning")
        self.assertIn("AttributeError", findings[0]["message"])

    def test_argument_counts_duplicates_and_keyword_only(self):
        prefix = "from temporalio.contrib.strands.workflow import activity_as_tool as bind\n"
        self.assert_contract_error(prefix + "bind(activity, queue)", "too many positional arguments")
        self.assert_contract_error(prefix + "bind(activity, activity_fn=activity)", "multiple values")
        self.assertEqual(self.check_source(prefix + "bind(activity_fn=activity)"), [])

    def test_unpacking_defers_all_argument_validation(self):
        prefix = "from temporalio.worker import Worker\n"
        for call in ("Worker(**options)", "Worker(*args)",
                     "Worker(client, typo=True, **options)", "Worker(*args, typo=True)",
                     "Worker(**{'typo': True})", "Worker(*[client], typo=True)"):
            with self.subTest(call=call):
                self.assertEqual(self.check_source(prefix + call), [])

    def test_unknown_libraries_and_source_modules_never_imported(self):
        source = (
            "from app.worker import Worker\nWorker(typo=True)\n"
            "from unknown_library import activity_as_tool\nactivity_as_tool(bad=True)\n"
            "from strands_tools.browser import LocalChromiumBrowser\n"
            "LocalChromiumBrowser().browser(dynamic_option=True)\n"
        )
        with patch.object(checker.importlib, "import_module", side_effect=AssertionError("unsafe import")) as load:
            self.assertEqual(self.check_source(source), [])
        load.assert_not_called()

    def test_decorated_source_function_is_not_a_native_call(self):
        source = (
            "from temporalio.worker import Worker\nfrom strands import tool\n"
            "@tool\ndef Worker(): pass\nWorker(tool_use=payload)\n"
        )
        with patch.object(checker.importlib, "import_module") as load:
            self.assertEqual(self.check_source(source), [])
        load.assert_not_called()

    def test_imports_are_lazy_and_signatures_cached_across_files_and_calls(self):
        from temporalio.contrib.strands.workflow import activity_as_tool

        source = (
            "from temporalio.contrib.strands.workflow import activity_as_tool as bind\n"
            "from temporalio.worker import Worker\nbind(activity)\nbind(activity)"
        )
        with patch.object(checker.importlib, "import_module", wraps=importlib.import_module) as load, \
                patch.object(checker.inspect, "signature", wraps=inspect.signature) as signature:
            self.assertEqual(checker.check_contracts({"one.py": source, "two.py": source}), [])
            self.assertEqual(self.check_source(source), [])
        load.assert_called_once_with("temporalio.contrib.strands.workflow")
        signature.assert_called_once_with(activity_as_tool, follow_wrapped=False, eval_str=False)

    def test_import_failures_are_cached_availability_warnings(self):
        source = "from temporalio.worker import Worker\nWorker(client, task_queue=queue)\n"
        with patch.object(checker.importlib, "import_module", side_effect=ImportError("SDK not installed")) as load:
            findings = checker.check_contracts({"one.py": source, "two.py": source})
        load.assert_called_once_with("temporalio.worker")
        self.assertEqual(len(findings), 2)
        for finding in findings:
            self.assertEqual(finding["rule"], "python-contract-unavailable")
            self.assertEqual(finding["severity"], "warning")
            self.assertIn("SDK not installed", finding["message"])

    def test_signature_failure_is_warning(self):
        importlib.import_module("temporalio.worker")
        with patch.object(checker.inspect, "signature", side_effect=ValueError("unavailable")):
            findings = self.check_source("from temporalio.worker import Worker\nWorker()")
        self.assertEqual(findings[0]["rule"], "python-contract-unavailable")
        self.assertEqual(findings[0]["severity"], "warning")
        self.assertIn("ValueError", findings[0]["message"])

    def test_dynamic_callable_is_not_unwrapped_into_a_false_contract(self):
        module = importlib.import_module("temporalio.contrib.strands.workflow")

        class DynamicTool:
            def __call__(self, *args, **kwargs):
                raise AssertionError("must never invoke callable")

        with patch.object(module, "activity_as_tool", DynamicTool()):
            findings = self.check_source(
                "from temporalio.contrib.strands.workflow import activity_as_tool\nactivity_as_tool(typo=True)"
            )
        self.assertEqual(findings[0]["rule"], "python-contract-unavailable")
        self.assertEqual(findings[0]["severity"], "warning")

    def test_variadic_only_function_signature_warns(self):
        module = importlib.import_module("temporalio.contrib.strands.workflow")

        def dynamic(*args, **kwargs):
            raise AssertionError("must never invoke callable")

        with patch.object(module, "activity_as_tool", dynamic):
            findings = self.check_source(
                "from temporalio.contrib.strands.workflow import activity_as_tool\nactivity_as_tool()"
            )
        self.assertEqual(findings[0]["rule"], "python-contract-unavailable")
        self.assertIn("variadic", findings[0]["message"])

    def test_shadowed_relative_and_wildcard_imports_are_deferred(self):
        for source in (
            "from temporalio.worker import Worker\nWorker = custom\nWorker()",
            "from temporalio.worker import Worker\ndef run(Worker):\n Worker()",
            "from temporalio.worker import Worker\ndef run():\n Worker = custom\n Worker()",
            "from temporalio.worker import Worker\nfrom app import Worker\nWorker()",
            "from .temporalio.worker import Worker\nWorker()",
            "from temporalio.worker import Worker\nfrom app import *\nWorker()",
            "import temporalio.worker as w\nw.Worker = custom\nw.Worker()",
            "def run():\n from temporalio.worker import Worker\nWorker()",
            "from temporalio.worker import Worker\n[Worker() for Worker in tools]",
        ):
            with self.subTest(source=source):
                self.assertEqual(self.check_source(source), [])

    def test_lexical_scopes_and_with_block_imports(self):
        self.assert_contract_error(
            "with imports_passed_through():\n from temporalio.worker import Worker as W\n"
            "async def run():\n W(client)\n",
            "task_queue",
        )
        self.assert_contract_error(
            "def run():\n from temporalio.worker import Worker as W\n W(client)\n",
            "task_queue",
        )
        self.assert_contract_error(
            "from temporalio.worker import Worker\nclass App:\n Worker = custom\n"
            " def run(self):\n  Worker(client)\n",
            "task_queue",
        )

    def test_application_side_effects_and_argument_expressions_never_execute(self):
        source = (
            "from pathlib import Path\nfrom app import bootstrap\n"
            "Path('must-not-exist').write_text('executed')\n"
            "raise RuntimeError('executed source')\n"
            "from temporalio.worker import Worker\n"
            "Worker(bootstrap(), task_queue=1 / 0, typo=Path('also-must-not-exist').touch())"
        )
        importlib.import_module("temporalio.worker")
        with patch.object(Path, "write_text", side_effect=AssertionError("source executed")) as write, \
                patch.object(Path, "touch", side_effect=AssertionError("argument executed")) as touch, \
                patch("builtins.open", side_effect=AssertionError("source or environment file read")):
            self.assert_contract_error(source, "typo")
        write.assert_not_called()
        touch.assert_not_called()

    def test_malformed_and_non_python_sources_remain_structural_responsibility(self):
        files = {"broken.py": "def broken(:", "notes.txt": "from temporalio.worker import Worker\nWorker()"}
        with patch.object(checker.importlib, "import_module") as load:
            self.assertEqual(checker.check_contracts(files), [])
            self.assertEqual([item["rule"] for item in checker.check(files)], ["python-syntax"])
        load.assert_not_called()

    def test_default_cli_is_unchanged_and_contract_cli_emits_json(self):
        script = str(Path(checker.__file__).resolve())
        files = {"example.py": "from temporalio.worker import Worker\nWorker(client, task_queue=queue, typo=True)\nlaunch(headless=True)"}
        for options, expected in (([], "headed-desktop"), (["--contracts"], "python-call-contract")):
            with self.subTest(options=options):
                result = subprocess.run([sys.executable, "-I", script, *options], input=json.dumps(files),
                                        text=True, capture_output=True, check=True)
                findings = json.loads(result.stdout)
                self.assertEqual([item["rule"] for item in findings], [expected])


class BrowserInputContractsTests(unittest.TestCase):
    # Reuse assertion helpers without inheriting the Temporal test cases.
    check_source = PythonContractsTests.check_source
    assert_contract_error = PythonContractsTests.assert_contract_error

    def setUp(self):
        checker._browser_input_model.cache_clear()

    def test_native_nested_action_constructor_and_model_validate_aliases(self):
        from strands_tools.browser.models import BrowserInput

        payload = {"action": {"type": "navigate", "session_name": "contract-test", "url": "https://example.com"}}
        self.assertEqual(BrowserInput.model_validate(payload).action.type, "navigate")
        for prefix, name in (
            ("from strands_tools.browser.models import BrowserInput", "BrowserInput"),
            ("from strands_tools.browser.models import BrowserInput as Input", "Input"),
            ("import strands_tools.browser.models as models", "models.BrowserInput"),
        ):
            for call in (f"{name}.model_validate({payload!r})", f"{name}.model_validate(obj={payload!r})",
                         f"{name}(action={payload['action']!r})"):
                with self.subTest(call=call):
                    self.assertEqual(self.check_source(f"{prefix}\n{call}"), [])

    def test_action_typo_and_missing_fields_are_native_validation_errors(self):
        prefix = "from strands_tools.browser.models import BrowserInput as Input\n"
        for action, fragment in (
            ({"type": "navigte", "session_name": "contract-test", "url": "https://example.com"}, "union_tag_invalid"),
            ({"type": "navigate", "session_name": "contract-test"}, "action.navigate.url"),
            ({"type": "click", "selector": "#target"}, "action.click.session_name"),
            ({"type": "click", "session_name": "contract-test"}, "action.click.selector"),
        ):
            for call in (f"Input(action={action!r})", f"Input.model_validate({{'action': {action!r}}})"):
                with self.subTest(call=call):
                    finding = self.assert_contract_error(prefix + call, fragment)
                    self.assertEqual(finding["line"], 2)
        self.assert_contract_error(prefix + "Input()", "action")
        self.assert_contract_error(prefix + "Input.model_validate({})", "action")

    def test_native_constraints_coercion_and_extra_field_policy(self):
        prefix = "from strands_tools.browser.models import BrowserInput as Input\n"
        self.assert_contract_error(prefix + "Input(action={'type': 'init_session', 'session_name': 'short', 'description': 'test'})",
                                   "string_too_short")
        self.assertEqual(self.check_source(prefix + "Input(action={'type': 'list_local_sessions'}, wait_time='2', extra_field=True)"), [])
        payload = "{'action': {'type': 'list_local_sessions'}, 'wait_time': '2'}"
        self.assertEqual(self.check_source(prefix + f"Input.model_validate({payload})"), [])
        self.assert_contract_error(prefix + f"Input.model_validate({payload}, strict=True)", "int_type")

    def test_dynamic_payloads_and_options_warn_without_partial_validation(self):
        prefix = "from strands_tools.browser.models import BrowserInput as Input\n"
        for call in (
            "Input.model_validate(payload)",
            "Input(action={'type': 'navigte', 'url': url})",
            "Input.model_validate({'action': {'type': 'navigte'}}, strict=flag)",
            "Input(action={'type': 'navigte'}, wait_time=1 / 0)",
            "Input(action=make_action())",
            "Input(**payload)",
            "Input(**{'action': {'type': 'navigte'}})",
            "Input.model_validate(*args)",
        ):
            with self.subTest(call=call), patch.object(checker, "_browser_input_model") as load:
                findings = self.check_source(prefix + call)
                self.assertEqual(len(findings), 1)
                self.assertEqual(findings[0]["rule"], "python-contract-unavailable")
                self.assertEqual(findings[0]["severity"], "warning")
                self.assertIn("skipped", findings[0]["message"])
                load.assert_not_called()

    def test_wrapper_calls_and_application_model_aliases_are_not_assumed_native(self):
        for source in (
            "from browser_activity import browser_activity\nbrowser_activity(browser_input={'action': {'type': 'navigte'}})",
            "from app.models import BrowserInput\nBrowserInput(action={'type': 'navigte'})",
            "from strands_tools.browser.models import BrowserInput\nBrowserInput = custom\nBrowserInput(action={'type': 'navigte'})",
        ):
            with self.subTest(source=source), patch.object(checker.importlib, "import_module") as load:
                self.assertEqual(self.check_source(source), [])
                load.assert_not_called()

    def test_installed_model_unavailable_warns_and_caches_failure(self):
        source = "from strands_tools.browser.models import BrowserInput\nBrowserInput(action={'type': 'list_local_sessions'})"
        with patch.object(checker.importlib, "import_module", side_effect=ImportError("browser model unavailable")) as load:
            findings = checker.check_contracts({"one.py": source, "two.py": source})
        load.assert_called_once()
        self.assertEqual(len(findings), 2)
        for finding in findings:
            self.assertEqual(finding["rule"], "python-contract-unavailable")
            self.assertEqual(finding["severity"], "warning")
            self.assertIn("browser model unavailable", finding["message"])

    def test_native_model_is_cached_but_each_literal_payload_is_validated(self):
        from strands_tools.browser.models import BrowserInput

        prefix = "from strands_tools.browser.models import BrowserInput as Input\n"
        payload = {"action": {"type": "list_local_sessions"}}
        source = prefix + f"Input.model_validate({payload!r})\nInput.model_validate({payload!r})"
        with patch.object(checker.importlib, "import_module", wraps=importlib.import_module) as load, \
                patch.object(BrowserInput, "model_validate", wraps=BrowserInput.model_validate) as validate:
            self.assertEqual(self.check_source(source), [])
            self.assertEqual(self.check_source(source), [])
        self.assertEqual([call.args[0] for call in load.call_args_list], ["strands_tools.browser.models", "pydantic"])
        self.assertEqual(validate.call_count, 4)
        validate.assert_called_with(payload)

    def test_unexpected_model_validation_failure_warns(self):
        from strands_tools.browser.models import BrowserInput

        with patch.object(BrowserInput, "model_validate", side_effect=RuntimeError("native validator unavailable")):
            findings = self.check_source(
                "from strands_tools.browser.models import BrowserInput\n"
                "BrowserInput.model_validate({'action': {'type': 'list_local_sessions'}})"
            )
        self.assertEqual(findings[0]["rule"], "python-contract-unavailable")
        self.assertEqual(findings[0]["severity"], "warning")
        self.assertIn("native validator unavailable", findings[0]["message"])

    def test_literal_script_is_only_data_and_dynamic_python_never_executes(self):
        prefix = "from strands_tools.browser.models import BrowserInput as Input\n"
        source = prefix + "Input(action={'type': 'evaluate', 'session_name': 'contract-test', 'script': 'throw new Error(\"must not run\")'})"
        checker._browser_input_model()
        with patch.object(subprocess, "run", side_effect=AssertionError("source command executed")) as run, \
                patch.object(Path, "touch", side_effect=AssertionError("source executed")) as touch, \
                patch("builtins.open", side_effect=AssertionError("source or environment file read")):
            self.assertEqual(self.check_source(source), [])
            findings = self.check_source(prefix + "Input.model_validate(__import__('pathlib').Path('must-not-exist').touch())")
        self.assertEqual(findings[0]["severity"], "warning")
        run.assert_not_called()
        touch.assert_not_called()

    def test_cli_preserves_list_schema_with_browser_validation(self):
        source = "from strands_tools.browser.models import BrowserInput\nBrowserInput(action={'type': 'navigte'})"
        result = subprocess.run([sys.executable, "-I", str(Path(checker.__file__).resolve()), "--contracts"],
                                input=json.dumps({"example.py": source}), text=True, capture_output=True, check=True)
        findings = json.loads(result.stdout)
        self.assertIsInstance(findings, list)
        self.assertEqual(len(findings), 1)
        self.assertEqual(set(findings[0]), {"file", "line", "rule", "message", "severity"})
        self.assertEqual(findings[0]["rule"], "python-call-contract")


if __name__ == "__main__":
    unittest.main()
