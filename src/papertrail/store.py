"""SQLite persistence. A page and its checkpoint always commit together."""

from datetime import datetime, timezone
import json
import sqlite3

from .api import paper_id_valid


SOURCE = "Semantic Scholar"


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
    def __init__(self, path, *, source=SOURCE):
        self.source = source
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
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
        """)
        # Additive migration for TASK-002A databases created before review fixes.
        additions = {
            "paper_aliases": {"confirmed": "INTEGER NOT NULL DEFAULT 0",
                              "conflicted": "INTEGER NOT NULL DEFAULT 0"},
            "fetch_state": {"expected_count": "INTEGER", "warning_kind": "TEXT",
                            "warning_message": "TEXT"},
        }
        with self.connection:
            for table, columns in additions.items():
                existing = {row["name"] for row in self.connection.execute(f"PRAGMA table_info({table})")}
                for column, definition in columns.items():
                    if column not in existing:
                        self.connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
            # Legacy external aliases have no evidence of direct confirmation.
            self.connection.execute("UPDATE paper_aliases SET confirmed = 1 WHERE alias = paper_id")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.connection.close()

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
            for row in rows:
                paper = row.get(nested) if isinstance(row, dict) else None
                if not isinstance(paper, dict) or not paper_id_valid(paper.get("paperId")):
                    continue
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

