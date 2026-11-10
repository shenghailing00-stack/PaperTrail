"""Explainable discovery in one validated run; no collection or database writes.

Use discover() for a database, or summarize_discovery() with an unmodified graph
returned by analysis.load_graph(). Filter/rank helpers also work on its rows.
"""

from collections import Counter
from copy import deepcopy

from .analysis import load_graph


CATEGORIES = ("shared_reference", "shared_citing")
SORT_FIELDS = ("referenced_by_seed_count", "cites_seed_count", "global_citation_count", "year")
DISCOVERY_LIMITATIONS = [
    "All statistics describe the current collected sample before filtering, not research-field trends.",
    "Candidate categories require at least one distinct seed, not necessarily two; categories can overlap.",
    "local_out_degree counts only observed outgoing sample edges, not a paper's full reference list.",
    "Seed relation counts use distinct other seeds; self-loops are excluded from those counts but retained in local degrees.",
    "Seed rows are separate from non-seed candidate rankings; paperId tie order has no research-value meaning.",
]


def _keywords(keywords):
    if not isinstance(keywords, (list, tuple)):
        raise ValueError("keywords must be a list/tuple of non-empty strings, not a single string.")
    terms = []
    for term in keywords:
        if not isinstance(term, str) or not term.strip():
            raise ValueError("Each keyword must be a non-empty string.")
        normalized = term.strip().casefold()
        if normalized not in terms:
            terms.append(normalized)
    return terms


def _text_match(row, field, term):
    return term in (row.get(field) or "").casefold()


def validate_options(*, year_min=None, year_max=None, citation_min=None, citation_max=None,
                     keywords=(), categories=(), include_seeds=False,
                     sort_by="referenced_by_seed_count", top=None):
    """Normalize public options; reject ambiguous values before opening a DB."""
    for name, low, high in (("year", year_min, year_max), ("citation", citation_min, citation_max)):
        if any(value is not None and (type(value) is not int or value < 0) for value in (low, high)):
            raise ValueError(f"{name} bounds must be non-negative integers or None.")
        if low is not None and high is not None and low > high:
            raise ValueError(f"{name} minimum must not exceed maximum.")
    if not isinstance(categories, (list, tuple)) or any(value not in CATEGORIES for value in categories):
        raise ValueError("categories must contain shared_reference and/or shared_citing.")
    if type(include_seeds) is not bool:
        raise ValueError("include_seeds must be a boolean.")
    if sort_by not in SORT_FIELDS:
        raise ValueError("sort_by must be one of: " + ", ".join(SORT_FIELDS))
    if top is not None and (type(top) is not int or not 1 <= top <= 100):
        raise ValueError("top must be None (all rows) or an integer in 1..100.")
    return dict(year_min=year_min, year_max=year_max, citation_min=citation_min,
                citation_max=citation_max, keywords=_keywords(keywords),
                categories=sorted(set(categories)), include_seeds=include_seeds,
                sort_by=sort_by, top=top)


def discover_papers(graph):
    """One row per sample member, including seeds for separate display/stats.

    predecessors(p) intersect seeds gives seed -> p; successors(p) intersect
    seeds gives p -> seed. Degrees include ALL observed neighbors, not just seeds.
    """
    seed_ids = {paper_id for paper_id, attrs in graph.nodes(data=True) if attrs["is_seed"]}
    rows = []
    for paper_id, attrs in sorted(graph.nodes(data=True)):
        referenced_by = sorted((set(graph.predecessors(paper_id)) & seed_ids) - {paper_id})
        cites = sorted((set(graph.successors(paper_id)) & seed_ids) - {paper_id})
        categories, evidence = [], []
        if attrs["is_seed"]:
            evidence.append({"kind": "seed", "paper_id": paper_id})
        else:
            for category, seeds, orientation in (
                ("shared_reference", referenced_by, "seed -> paper"),
                ("shared_citing", cites, "paper -> seed"),
            ):
                if seeds:
                    categories.append(category)
                    evidence.append({"kind": category, "seed_ids": seeds,
                                     "seed_count": len(seeds), "edge_direction": orientation})
        reasons = (["Seed paper; displayed separately from non-seed candidates."] if attrs["is_seed"] else [])
        if referenced_by:
            reasons.append(f"Referenced by {len(referenced_by)} distinct other seed(s): " + ", ".join(referenced_by))
        if cites:
            reasons.append(f"Cites {len(cites)} distinct other seed(s): " + ", ".join(cites))
        rows.append({**deepcopy(attrs), "paperId": paper_id, "categories": categories,
                     "referenced_by_seed_ids": referenced_by, "referenced_by_seed_count": len(referenced_by),
                     "cites_seed_ids": cites, "cites_seed_count": len(cites),
                     "local_in_degree": graph.in_degree(paper_id),
                     "local_out_degree": graph.out_degree(paper_id),
                     "evidence": evidence, "reason": " ".join(reasons)})
    return rows


def filter_papers(rows, *, year_min=None, year_max=None, citation_min=None, citation_max=None,
                  keywords=(), categories=(), include_seeds=False):
    """AND across filters/keywords, OR across categories and title/abstract.

    Active numeric filters exclude missing values (None is never zero). Without
    the filter, missing values remain. Seeds have no candidate category, so an
    active category filter excludes them even when include_seeds is True.
    """
    options = validate_options(year_min=year_min, year_max=year_max, citation_min=citation_min,
                               citation_max=citation_max, keywords=keywords, categories=categories,
                               include_seeds=include_seeds)
    selected = []
    for row in rows:
        if row["is_seed"] and not include_seeds:
            continue
        if not row["is_seed"] and not row["categories"]:
            continue
        if options["categories"] and not set(options["categories"]).intersection(row["categories"]):
            continue
        if any((low is not None or high is not None) and
               (row[field] is None or (low is not None and row[field] < low)
                or (high is not None and row[field] > high))
               for field, low, high in (("year", year_min, year_max),
                                        ("global_citation_count", citation_min, citation_max))):
            continue
        if not all(_text_match(row, "title", term) or _text_match(row, "abstract", term)
                   for term in options["keywords"]):
            continue
        selected.append(deepcopy(row))
    return selected


def rank_papers(rows, *, sort_by="referenced_by_seed_count", top=None):
    """Descending single metric; competition ranks, ID ties, missing last.

    tie_count includes the row itself, over the full filtered group BEFORE top.
    Missing metrics have rank=None/tie_count=0: unknown is not a measured tie.
    Mixed seed/candidate groups are refused to prevent accidental comparison.
    """
    validate_options(sort_by=sort_by, top=top)
    rows = list(rows)
    if len({row["is_seed"] for row in rows}) > 1:
        raise ValueError("Rank seeds and non-seed candidates separately.")
    ordered = sorted(rows, key=lambda row: (row[sort_by] is None,
                                           -row[sort_by] if row[sort_by] is not None else 0, row["paperId"]))
    counts = Counter(row[sort_by] for row in rows if row[sort_by] is not None)
    ranks = {}
    ranked = []
    for position, row in enumerate(ordered, 1):
        value = row[sort_by]
        rank = ranks.setdefault(value, position) if value is not None else None
        ranked.append({**deepcopy(row), "sort_value": value, "rank": rank,
                       "tie_count": counts[value] if value is not None else 0})
    return ranked if top is None else ranked[:top]


def sample_statistics(rows, *, keywords=()):
    """Unique-paper counts over the entire current sample, before any filters."""
    rows = list(rows)
    years = Counter(row["year"] for row in rows if row["year"] is not None)
    missing = {field: sum(row.get(field) is None or row.get(field) == [] or row.get(field) == {} or
                         (isinstance(row.get(field), str) and not row[field].strip()) for row in rows)
               for field in ("year", "title", "abstract", "global_citation_count", "url", "authors", "externalIds")}
    keyword_counts = []
    for term in _keywords(keywords):
        title_ids = {row["paperId"] for row in rows if _text_match(row, "title", term)}
        abstract_ids = {row["paperId"] for row in rows if _text_match(row, "abstract", term)}
        keyword_counts.append({"keyword": term, "title_paper_count": len(title_ids),
                               "abstract_paper_count": len(abstract_ids),
                               "either_paper_count": len(title_ids | abstract_ids)})
    non_seeds = [row for row in rows if not row["is_seed"]]
    return {"scope": "current_run_before_filters", "paper_count": len(rows),
            "seed_count": len(rows) - len(non_seeds), "non_seed_count": len(non_seeds),
            "candidate_count": sum(bool(row["categories"]) for row in non_seeds),
            "candidate_counts": {category: sum(category in row["categories"] for row in non_seeds)
                                 for category in CATEGORIES},
            "both_category_count": sum(len(row["categories"]) == 2 for row in non_seeds),
            "year_distribution": [{"year": year, "paper_count": count} for year, count in sorted(years.items())],
            "missing_metadata": missing, "keyword_counts": keyword_counts,
            "keyword_count_policy": "Case-insensitive literal substring; each paper counts once per field/keyword; either is a union, not a sum.",
            "missing_metadata_policy": "None, blank text and empty author/external-ID containers are missing; numeric zero is not missing."}


def summarize_discovery(graph, **options):
    """JSON-compatible UI contract. Pass only an unmodified load_graph result."""
    options = validate_options(**options)
    rows = discover_papers(graph)
    filters = {key: value for key, value in options.items() if key not in ("sort_by", "top")}
    selected = filter_papers(rows, **filters)
    candidates = [row for row in selected if not row["is_seed"]]
    seeds = [row for row in selected if row["is_seed"]]
    sample = deepcopy(graph.graph)
    sample["limitations"].extend(DISCOVERY_LIMITATIONS)
    return {"schema_version": 1, "sample": sample,
            "statistics": sample_statistics(rows, keywords=options["keywords"]),
            "selection": {**options, "candidate_count_before_top": len(candidates),
                          "seed_count_before_top": len(seeds),
                          "filter_policy": "AND across filters/keywords; OR across categories; active ranges exclude null; category filters exclude seeds."},
            "ranking": {"sort_by": options["sort_by"], "direction": "descending", "missing": "last, unranked",
                        "tie_breaker": "paperId ascending, display stability only; no research-value meaning",
                        "tie_count_scope": "Same measured value within each filtered group before top; includes self",
                        "rank_policy": "Competition ranks (1, 1, 3); seeds always separate; top applies to each group"},
            "candidates": rank_papers(candidates, sort_by=options["sort_by"], top=options["top"]),
            "seeds": rank_papers(seeds, sort_by=options["sort_by"], top=options["top"])}


def discover(db_path, run_id, **options):
    """Validated read-only entry point for CLI and future Streamlit callers."""
    options = validate_options(**options)
    return summarize_discovery(load_graph(db_path, run_id), **options)
