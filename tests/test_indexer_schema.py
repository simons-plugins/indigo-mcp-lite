"""TDD tests for the FTS5 schema creation in Indexer.build()."""


def test_indexer_creates_fts5_table_with_expected_columns(mock_indigo):
    from indexer import Indexer

    mock_indigo.devices = []
    mock_indigo.variables = []
    mock_indigo.actionGroups = []
    idx = Indexer(indigo_module=mock_indigo)
    idx.build()

    cols = idx.connection.execute("PRAGMA table_info(entities)").fetchall()
    names = {c[1] for c in cols}
    assert {"entity_type", "entity_id", "name", "description",
            "type_label", "folder", "aliases", "extra"} <= names


def test_indexer_uses_porter_tokenizer(mock_indigo):
    from indexer import Indexer

    mock_indigo.devices = []
    mock_indigo.variables = []
    mock_indigo.actionGroups = []
    idx = Indexer(indigo_module=mock_indigo)
    idx.build()

    row = idx.connection.execute(
        "SELECT sql FROM sqlite_master WHERE name='entities'"
    ).fetchone()
    assert "porter" in row[0]


def test_indexer_build_is_idempotent(mock_indigo):
    """Calling build() twice should drop and recreate cleanly so a
    Reindex Now menu action is safe to call on demand."""
    from indexer import Indexer

    mock_indigo.devices = []
    mock_indigo.variables = []
    mock_indigo.actionGroups = []
    idx = Indexer(indigo_module=mock_indigo)
    idx.build()
    idx.build()

    cols = idx.connection.execute("PRAGMA table_info(entities)").fetchall()
    names = {c[1] for c in cols}
    assert "name" in names
