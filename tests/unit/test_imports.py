def test_workspace_packages_are_importable() -> None:
    import weave_common
    import weave_ingestion

    import agent

    assert agent is not None
    assert weave_common is not None
    assert weave_ingestion is not None
