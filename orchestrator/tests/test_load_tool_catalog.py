"""``load_tool`` resolves a bare module name to the installed package file."""

from load_tool import STRANDS_TOOLS_DIR, tool_file_path


def test_nested_ids_resolve_inside_the_package() -> None:
    assert tool_file_path("dicom") == str(STRANDS_TOOLS_DIR / "dicom" / "strands_tools.py")
    assert tool_file_path("fhir") == str(STRANDS_TOOLS_DIR / "fhir" / "fhir_tools.py")
    assert tool_file_path("forge") == str(STRANDS_TOOLS_DIR / "forge" / "strands_tools.py")
    assert tool_file_path("current_time") == str(STRANDS_TOOLS_DIR / "current_time.py")
    assert tool_file_path("/etc/passwd") == "/etc/passwd"
