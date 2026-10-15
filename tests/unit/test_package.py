import iac_node_forge


def test_version_is_declared() -> None:
    assert isinstance(iac_node_forge.__version__, str)
    assert iac_node_forge.__version__ != ""
