"""SQLite persistence. A page and its checkpoint always commit together."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

from .api import paper_id_valid, utc_now


SOURCE = "Semantic Scholar"
FIXTURE_SOURCE = "Synthetic offline fixture (Semantic Scholar schema)"


class SourceMismatch(ValueError):
    """A database cannot mix datasets or infer an unknown fixture's identity."""


class ReadOnlyStoreError(ValueError):
    """Safe, user-facing error for read-only access; never contains file contents."""


def normalize_alias(value):
    value = value.strip()
    prefix, sep, suffix = value.partition(":")
    if not sep:
        return value.lower() if len(value) == 40 else value
    prefix = {"corpusid": "CorpusId"}.get(prefix.lower(), prefix.upper())
    return prefix + ":" + (suffix.lower() if prefix == "DOI" else suffix)


def fresh(timestamp, ttl_seconds):
    if not timestamp:
        return False
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(timestamp)).total_seconds()
    return 0 <= age < ttl_seconds


class PaperStore:
    @classmethod
    def open_readonly(cls, path):
        """Open one read transaction without calling the writable constructor.

        URI mode=ro enforces read-only access in SQLite; normal WAL/locking rules
        remain enabled. Closing the store ends the snapshot. No migration runs.
        """
        database = Path(path).resolve()
        if not database.is_file():
            raise ReadOnlyStoreError("Database file does not exist or is not a regular file; nothing was created.")
        if database.stat().st_size == 0:
            raise ReadOnlyStoreError("Database file is empty (0 bytes); read-only access will not initialize it.")
        store = cls.__new__(cls)
        connection = None
        try:
            connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
            connection.row_factory = sqlite3.Row
            store.connection = connection
            connection.execute("BEGIN")  # One consistent snapshot across all sample queries.
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            if not {"papers", "citation_edges", "paper_aliases", "fetch_state"} <= tables:
                raise ReadOnlyStoreError("Not a PaperTrail database; no tables were added.")
            required = {
                "papers": {"paper_id"},
                "paper_aliases": {"alias", "paper_id", "confirmed", "conflicted"},
                "dataset_identity": {"singleton", "source", "source_key"},
                "collection_runs": {"run_id", "source", "source_key", "inputs_json", "config_json",
                                    "status", "started_at", "finished_at", "report_json"},
                "run_seeds": {"run_id", "seed_id", "citation_quota"},
                "run_items": {"run_id", "seed_id", "direction", "ordinal", "other_paper_id",
                              "missing_abstract", "fetched_at"},
            }
            for table, columns in required.items():
                existing = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
                if not columns <= existing:
                    raise ReadOnlyStoreError(
                        "Old or incomplete PaperTrail schema; read-only access does not migrate it. "
                        "Use the collection workflow to upgrade and create a traceable run.")
            identity = connection.execute("SELECT source FROM dataset_identity WHERE singleton = 1").fetchone()
            store.source = identity["source"] if identity else None
            return store
        except BaseException as error:
            if connection is not None:
                connection.close()
            if isinstance(error, sqlite3.Error):
                raise ReadOnlyStoreError("Cannot read SQLite database; check file format, access permissions and locks.") from None
            raise

    def __init__(self, path, *, source=SOURCE):
        self.source = source
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        try:
            existing_tables = {
                row[0] for row in self.connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            if existing_tables & {"library_items", "library_sources"}:
                raise SourceMismatch(
                    "Reading library database cannot be used as a collection database; choose a separate file."
                )
            self.connection.execute("PRAGMA foreign_keys = ON")
            self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS papers (
                paper_id TEXT PRIMARY KEY CHECK(length(trim(paper_id)) > 0),
                title TEXT, year INTEGER, authors_json TEXT, abstract TEXT, url TEXT,
                external_ids_json TEXT, reference_count INTEGER, citation_count INTEGER,
                source TEXT NOT NULL, updated_at TEXT NOT NULL,
                metadata_fetched_at TEXT
            );
            CREATE TABLE IF NOT EXISTS paper_aliases (
                alias TEXT PRIMARY KEY,
                paper_id TEXT NOT NULL REFERENCES papers(paper_id)
            );
            CREATE TABLE IF NOT EXISTS citation_edges (
                citing_paper_id TEXT NOT NULL REFERENCES papers(paper_id),
                cited_paper_id TEXT NOT NULL REFERENCES papers(paper_id),
                source TEXT NOT NULL, fetched_at TEXT NOT NULL,
                PRIMARY KEY(citing_paper_id, cited_paper_id)
            );
            CREATE TABLE IF NOT EXISTS fetch_state (
                seed_id TEXT NOT NULL REFERENCES papers(paper_id),
                direction TEXT NOT NULL CHECK(direction IN ('references', 'citations')),
                status TEXT NOT NULL CHECK(status IN ('partial', 'complete', 'truncated', 'error')),
                next_offset INTEGER, raw_count INTEGER NOT NULL DEFAULT 0,
                unresolved_count INTEGER NOT NULL DEFAULT 0,
                missing_abstract_count INTEGER NOT NULL DEFAULT 0,
                complete INTEGER NOT NULL DEFAULT 0, truncated INTEGER NOT NULL DEFAULT 0,
                target_limit INTEGER NOT NULL, error_kind TEXT, error_message TEXT,
                started_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                PRIMARY KEY(seed_id, direction)
            );
            CREATE TABLE IF NOT EXISTS dataset_identity (
                singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                source_key TEXT NOT NULL, source TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS collection_runs (
                run_id INTEGER PRIMARY KEY,
                source_key TEXT NOT NULL, source TEXT NOT NULL,
                inputs_json TEXT NOT NULL, config_json TEXT NOT NULL,
                started_at TEXT NOT NULL, finished_at TEXT,
                status TEXT NOT NULL, report_json TEXT
            );
            CREATE TABLE IF NOT EXISTS run_seeds (
                run_id INTEGER NOT NULL REFERENCES collection_runs(run_id),
                seed_id TEXT NOT NULL REFERENCES papers(paper_id),
                citation_quota INTEGER NOT NULL,
                PRIMARY KEY(run_id, seed_id)
            );
            CREATE TABLE IF NOT EXISTS run_items (
                run_id INTEGER NOT NULL, seed_id TEXT NOT NULL, direction TEXT NOT NULL,
                ordinal INTEGER NOT NULL CHECK(ordinal >= 0),
                other_paper_id TEXT REFERENCES papers(paper_id),
                missing_abstract INTEGER NOT NULL, fetched_at TEXT NOT NULL,
                PRIMARY KEY(run_id, seed_id, direction, ordinal),
                FOREIGN KEY(run_id, seed_id) REFERENCES run_seeds(run_id, seed_id),
                CHECK(direction IN ('references', 'citations'))
            );
            """)
            # Additive migration for TASK-002A databases created before review fixes.
            additions = {
                "paper_aliases": {"confirmed": "INTEGER NOT NULL DEFAULT 0",
                                  "conflicted": "INTEGER NOT NULL DEFAULT 0"},
                "fetch_state": {"expected_count": "INTEGER", "warning_kind": "TEXT",
                                "warning_message": "TEXT", "sample_run_id": "INTEGER REFERENCES collection_runs(run_id)",
                                "sample_digest": "TEXT"},
            }
            with self.connection:
                for table, columns in additions.items():
                    existing = {row["name"] for row in self.connection.execute(f"PRAGMA table_info({table})")}
                    for column, definition in columns.items():
                        if column not in existing:
                            self.connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
                # Legacy external aliases have no evidence of direct confirmation.
                self.connection.execute("UPDATE paper_aliases SET confirmed = 1 WHERE alias = paper_id")
            identity = self.connection.execute("SELECT source FROM dataset_identity WHERE singleton = 1").fetchone()
            if identity:
                self.source = identity["source"]
        except BaseException:
            self.connection.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.connection.close()

    def bind_source(self, source_key, source):
        """Check identity before cache use or HTTP; never relabel historical rows."""
        with self.connection:
            identity = self.connection.execute("SELECT * FROM dataset_identity WHERE singleton = 1").fetchone()
            sources = {row[0] for row in self.connection.execute(
                "SELECT source FROM papers UNION SELECT source FROM citation_edges")}
            if identity:
                if identity["source_key"] != source_key or identity["source"] != source or sources - {source}:
                    raise SourceMismatch("Dataset identity mismatch; use a separate database.")
            else:
                # Old real-only DBs have a named source; old fixture DBs lack a fingerprint.
                if sources and (source_key != "semantic-scholar" or sources != {SOURCE}):
                    raise SourceMismatch("Legacy dataset identity is unknown or synthetic; keep it for history and use a new database.")
                self.connection.execute("INSERT INTO dataset_identity VALUES (1, ?, ?)", (source_key, source))
        self.source = source

    def start_run(self, inputs, config, started_at):
        identity = self.connection.execute("SELECT * FROM dataset_identity WHERE singleton = 1").fetchone()
        with self.connection:
            cursor = self.connection.execute("""
                INSERT INTO collection_runs (source_key, source, inputs_json, config_json, started_at, status)
                VALUES (?, ?, ?, ?, ?, 'running')
            """, (identity["source_key"], identity["source"], json.dumps(inputs), json.dumps(config), started_at))
        return cursor.lastrowid

    def finish_run(self, run_id, status, report=None):
        with self.connection:
            self.connection.execute("""
                UPDATE collection_runs SET status = ?, finished_at = ?, report_json = ? WHERE run_id = ?
            """, (status, utc_now(), json.dumps(report) if report is not None else None, run_id))

    def add_run_seed(self, run_id, seed_id, quota):
        with self.connection:
            self.connection.execute("""INSERT INTO run_seeds VALUES (?, ?, ?)
                ON CONFLICT(run_id, seed_id) DO UPDATE SET citation_quota = excluded.citation_quota
            """, (run_id, seed_id, quota))

    def has_sample(self, state):
        if not state or state.get("sample_run_id") is None:
            return False
        if self.connection.execute("SELECT 1 FROM run_seeds WHERE run_id = ? AND seed_id = ?",
                                   (state["sample_run_id"], state["seed_id"])).fetchone() is None:
            return False
        row = self.connection.execute("""
            SELECT count(*) AS n, min(ordinal) AS lo, max(ordinal) AS hi FROM run_items
            WHERE run_id = ? AND seed_id = ? AND direction = ?
        """, (state["sample_run_id"], state["seed_id"], state["direction"])).fetchone()
        return (row["n"] == state["raw_count"]
                and (row["n"] == 0 or (row["lo"] == 0 and row["hi"] == row["n"] - 1))
                and state.get("sample_digest") == self._sample_digest(state))

    def _sample_digest(self, state):
        rows = self.connection.execute("""
            SELECT ordinal, other_paper_id, missing_abstract, fetched_at FROM run_items
            WHERE run_id = ? AND seed_id = ? AND direction = ? ORDER BY ordinal
        """, (state["sample_run_id"], state["seed_id"], state["direction"]))
        content = json.dumps([tuple(row) for row in rows], separators=(",", ":"))
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def copy_sample(self, state, run_id):
        """Caller commits the copied prefix and new checkpoint in one transaction."""
        self.connection.execute("""
            INSERT INTO run_items SELECT ?, seed_id, direction, ordinal, other_paper_id, missing_abstract, fetched_at
            FROM run_items WHERE run_id = ? AND seed_id = ? AND direction = ?
        """, (run_id, state["sample_run_id"], state["seed_id"], state["direction"]))
        state["sample_run_id"] = run_id

    def get_sample(self, run_id):
        """Exact membership for one run, never an induced graph of historical edges.

        Paper attributes remain in papers as latest-known metadata, not snapshots.
        Running/interrupted samples contain only committed pages and resolved seeds.
        """
        run = self.connection.execute("SELECT * FROM collection_runs WHERE run_id = ?", (run_id,)).fetchone()
        if run is None:
            raise ValueError("Unknown collection run.")
        seeds = [dict(row) for row in self.connection.execute(
            "SELECT seed_id, citation_quota FROM run_seeds WHERE run_id = ? ORDER BY rowid", (run_id,))]
        papers = {seed["seed_id"] for seed in seeds}
        edges = set()
        counts = {(seed["seed_id"], direction): {"seed_id": seed["seed_id"], "direction": direction,
                  "raw_count": 0, "unresolved_count": 0, "missing_abstract_count": 0}
                  for seed in seeds for direction in ("references", "citations")}
        for row in self.connection.execute("SELECT * FROM run_items WHERE run_id = ? ORDER BY seed_id, direction, ordinal", (run_id,)):
            key = (row["seed_id"], row["direction"])
            count = counts[key]
            count["raw_count"] += 1
            count["missing_abstract_count"] += row["missing_abstract"]
            other = row["other_paper_id"]
            if other is None:
                count["unresolved_count"] += 1
            else:
                papers.add(other)
                edges.add((row["seed_id"], other) if row["direction"] == "references" else (other, row["seed_id"]))
        return {"run_id": run_id, "source": run["source"], "source_key": run["source_key"],
                "status": run["status"], "started_at": run["started_at"], "finished_at": run["finished_at"],
                "inputs": json.loads(run["inputs_json"]), "config": json.loads(run["config_json"]),
                "seeds": seeds, "paper_ids": sorted(papers), "edges": [list(edge) for edge in sorted(edges)],
                "record_counts": list(counts.values()),
                "report": json.loads(run["report_json"]) if run["report_json"] else None}

    def lookup(self, alias):
        row = self.connection.execute("""
            SELECT p.*, a.confirmed AS alias_confirmed, a.conflicted AS alias_conflicted
            FROM papers p JOIN paper_aliases a USING(paper_id)
            WHERE a.alias = ?
        """, (normalize_alias(alias),)).fetchone()
        return dict(row) if row else None

    def upsert_paper(self, paper, fetched_at, *, metadata=False, alias=None):
        """Caller owns the transaction; empty fields preserve known values."""
        paper = {key: None if (value is None or value == [] or value == {}
                               or isinstance(value, str) and not value.strip()) else value
                 for key, value in paper.items()}
        paper_id = paper.get("paperId")
        if not paper_id_valid(paper_id):
            raise ValueError("Cannot store a paper without paperId.")
        if alias and (not metadata or (":" not in alias and normalize_alias(alias) != normalize_alias(paper_id))):
            raise ValueError("An input alias requires direct metadata confirmation; canonical IDs cannot be remapped.")
        values = {
            "paper_id": paper_id, "title": paper.get("title"), "year": paper.get("year"),
            "abstract": paper.get("abstract"), "url": paper.get("url"),
            "reference_count": paper.get("referenceCount"), "citation_count": paper.get("citationCount"),
            "source": self.source, "updated_at": fetched_at,
            "metadata_fetched_at": fetched_at if metadata else None,
        }
        for field, column in (("authors", "authors_json"), ("externalIds", "external_ids_json")):
            values[column] = json.dumps(paper[field], ensure_ascii=False) if paper.get(field) is not None else None
        columns = ", ".join(values)
        updates = ", ".join(
            f"{name} = COALESCE(excluded.{name}, papers.{name})" for name in values if name != "paper_id"
        )
        self.connection.execute(
            f"INSERT INTO papers ({columns}) VALUES ({', '.join('?' for _ in values)}) "
            f"ON CONFLICT(paper_id) DO UPDATE SET {updates}", tuple(values.values()),
        )
        aliases = []
        external = paper.get("externalIds")
        if isinstance(external, dict):
            for key, value in external.items():
                if key in {"DOI", "ArXiv", "CorpusId", "ACL", "MAG", "PubMed", "PubMedCentral"} and value:
                    prefix = {"ArXiv": "ARXIV", "PubMed": "PMID", "PubMedCentral": "PMCID"}.get(key, key)
                    aliases.append(f"{prefix}:{value}")
        for item in aliases:
            self.connection.execute("""
                INSERT INTO paper_aliases (alias, paper_id) VALUES (?, ?)
                ON CONFLICT(alias) DO UPDATE SET conflicted = CASE
                    WHEN paper_aliases.paper_id != excluded.paper_id THEN 1
                    ELSE paper_aliases.conflicted END
            """, (normalize_alias(item), paper_id))
        # Only the directly queried input (and the canonical ID itself) is authoritative.
        for item in [paper_id] + ([alias] if alias else []):
            self.connection.execute("""
                INSERT INTO paper_aliases (alias, paper_id, confirmed, conflicted) VALUES (?, ?, 1, 0)
                ON CONFLICT(alias) DO UPDATE SET paper_id = excluded.paper_id, confirmed = 1, conflicted = 0
            """, (normalize_alias(item), paper_id))

    def alias_conflicts(self):
        return [dict(row) for row in self.connection.execute(
            "SELECT alias, paper_id AS retained_paper_id FROM paper_aliases WHERE conflicted = 1 ORDER BY alias")]

    def get_state(self, seed_id, direction):
        row = self.connection.execute(
            "SELECT * FROM fetch_state WHERE seed_id = ? AND direction = ?", (seed_id, direction),
        ).fetchone()
        return dict(row) if row else None

    def save_state(self, state):
        if state.get("sample_run_id") is not None:
            state["sample_digest"] = self._sample_digest(state)
        columns = ", ".join(state)
        updates = ", ".join(f"{key} = excluded.{key}" for key in state if key not in {"seed_id", "direction"})
        self.connection.execute(
            f"INSERT INTO fetch_state ({columns}) VALUES ({', '.join('?' for _ in state)}) "
            f"ON CONFLICT(seed_id, direction) DO UPDATE SET {updates}", tuple(state.values()),
        )

    def save_page(self, state, rows):
        """Atomic checkpoint: never advance offset without committing that page's edges."""
        seed, direction = state["seed_id"], state["direction"]
        nested = "citedPaper" if direction == "references" else "citingPaper"
        with self.connection:
            for index, row in enumerate(rows):
                paper = row.get(nested) if isinstance(row, dict) else None
                other = paper.get("paperId") if isinstance(paper, dict) else None
                abstract = paper.get("abstract") if isinstance(paper, dict) else None
                if state.get("sample_run_id") is not None:
                    # Valid papers must exist before their membership foreign key is inserted.
                    if paper_id_valid(other):
                        self.upsert_paper(paper, state["updated_at"])
                    self.connection.execute("INSERT INTO run_items VALUES (?, ?, ?, ?, ?, ?, ?)", (
                        state["sample_run_id"], seed, direction, state["raw_count"] - len(rows) + index,
                        other if paper_id_valid(other) else None,
                        int(not isinstance(abstract, str) or not abstract.strip()), state["updated_at"]))
                if not isinstance(paper, dict) or not paper_id_valid(paper.get("paperId")):
                    continue
                if state.get("sample_run_id") is None:
                    self.upsert_paper(paper, state["updated_at"])
                other = paper["paperId"]
                citing, cited = (seed, other) if direction == "references" else (other, seed)
                self.connection.execute("""
                    INSERT INTO citation_edges VALUES (?, ?, ?, ?)
                    ON CONFLICT(citing_paper_id, cited_paper_id)
                    DO UPDATE SET fetched_at = excluded.fetched_at, source = excluded.source
                """, (citing, cited, self.source, state["updated_at"]))
            self.save_state(state)

    def counts(self):
        return {name: self.connection.execute(f"SELECT count(*) FROM {name}").fetchone()[0]
                for name in ("papers", "citation_edges")}

