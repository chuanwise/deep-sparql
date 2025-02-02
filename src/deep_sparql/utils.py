import re
import uuid
import requests
from typing import Dict, List, Callable, Tuple, Optional, Set

from tqdm import tqdm

from text_utils import prefix, tokenization, text
from text_utils.api.table import generate_table

VAR_REGEX = re.compile(r"\?(\w+)")

QLEVER_URLS = {
    "wikidata": "https://qlever.cs.uni-freiburg.de/api/wikidata",
    "dbpedia": "https://qlever.cs.uni-freiburg.de/api/dbpedia",
    "freebase": "https://qlever.cs.uni-freiburg.de/api/freebase",
}

KNOWLEDGE_GRAPHS = {
    "wikidata": "Wikidata",
    "dbpedia": "DBPedia",
    "freebase": "Freebase"
}


def load_kg_index(
    path: str,
    progress: bool = False
) -> Tuple[Dict[str, List[str]], Dict[str, str]]:
    num_lines, _ = text.file_size(path)
    with open(path, "r", encoding="utf8") as f:
        index = {}
        redirect = {}
        for line in tqdm(
            f,
            total=num_lines,
            desc="loading kg index",
            disable=not progress,
            leave=False
        ):
            split = line.strip().split("\t")
            assert len(split) >= 3
            obj_id = split[0].strip()
            redirects = [
                redir for redir in split[1].strip().split(";")
                if redir.strip() != ""
            ]
            obj_names = [n.strip() for n in split[2:]]
            assert obj_id not in index, \
                f"duplicate id {obj_id}"
            index[obj_id] = obj_names
            for red in redirects:
                assert red not in redirect, \
                    f"duplicate redirect {red}"
                redirect[red] = obj_id
        return index, redirect


def load_inverse_index(path: str) -> Dict[str, List[str]]:
    with open(path, "r", encoding="utf8") as f:
        index = {}
        for line in f:
            split = line.strip().split("\t")
            assert len(split) == 2
            obj_id_1 = split[0].strip()
            obj_id_2 = split[1].strip()
            if obj_id_1 not in index:
                index[obj_id_1] = [obj_id_2]
            else:
                index[obj_id_1].append(obj_id_2)
        return index


REP = list[tuple[str, str]]


def _replace(
    s: str,
    pattern: str,
    replacement_fn: Callable[[str], str],
) -> tuple[str, REP]:
    org_len = len(s)
    len_diff = 0
    replacements = []
    for match in re.finditer(pattern, s):
        replacement = replacement_fn(match.group(1))
        replacements.append((match.group(1).strip(), replacement))
        start = match.start() + len_diff
        end = match.end() + len_diff
        s = s[:start] + replacement + s[end:]
        len_diff = len(s) - org_len
    return s, replacements


def replace_vars(
    s: str,
    open: str = "<bov>",
    close: str = "<eov>"
) -> tuple[str, REP]:
    open = re.escape(open)
    close = re.escape(close)
    return _replace(
        s,
        f"{open}(.+?){close}",
        lambda v: f"?{v.strip()}"
    )


def replace_entities(
    s: str,
    index: prefix.Vec,
    open: str = "<boe>",
    close: str = "<eoe>"
) -> tuple[str, REP]:
    open = re.escape(open)
    close = re.escape(close)
    return _replace(
        s,
        f"{open}(.+?){close}",
        lambda e: f"{index.get(e.encode('utf8'))}"
    )


def replace_properties(
    s: str,
    index: prefix.Vec,
    open: str = "<bop>",
    close: str = "<eop>"
) -> tuple[str, REP]:
    open = re.escape(open)
    close = re.escape(close)
    return _replace(
        s,
        f"{open}(.+?){close}",
        lambda p: f"{index.get(p.encode('utf8'))}"
    )


TOKEN_PAIR = tuple[str, str]


def clean_sparql(
    s: str,
    special_tokens: tuple[tuple[str, str], ...] = (
        ("<bob>", "{"),
        ("<eob>", "}"),
    ),
    special_token_pairs: tuple[tuple[TOKEN_PAIR, TOKEN_PAIR], ...] = ()
) -> str:
    for tok, rep in special_tokens:
        s = s.replace(tok, rep)
    for (first, second), (rep_first, rep_second) in special_token_pairs:
        s = re.sub(
            f"({first})(.*?)({second})",
            lambda m: f"{rep_first}{m.group(2).strip()}{rep_second}",
            s
        )
    return re.sub(r"\s+", " ", s, flags=re.DOTALL).strip()


def general_prefixes() -> list[str]:
    return [
        "PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>",
        "PREFIX wikibase: <http://wikiba.se/ontology#>",
    ]


def wikidata_prefixes() -> list[str]:
    return [
        "PREFIX wd: <http://www.wikidata.org/entity/>",
        "PREFIX wdt: <http://www.wikidata.org/prop/direct/>",
        "PREFIX p: <http://www.wikidata.org/prop/>",
        "PREFIX ps: <http://www.wikidata.org/prop/statement/>",
        "PREFIX psn: <http://www.wikidata.org/prop/statement/"
        "value-normalized/>",
        "PREFIX pq: <http://www.wikidata.org/prop/qualifier/>",
        "PREFIX pqn: <http://www.wikidata.org/prop/qualifier/"
        "value-normalized/>",
    ]


def freebase_prefixes() -> list[str]:
    return [
        "PREFIX fb: <http://rdf.freebase.com/ns/>",
    ]


def dbpedia_prefixes() -> list[str]:
    return [
        "PREFIX dbo: <http://dbpedia.org/ontology/>",
        "PREFIX dbp: <http://dbpedia.org/property/>",
        "PREFIX dbr: <http://dbpedia.org/resource/>",
    ]


def get_prefixes(kg: str | None = None) -> list[str]:
    prefixes = general_prefixes()
    if kg == "wikidata":
        prefixes += wikidata_prefixes()
    elif kg == "freebase":
        prefixes += freebase_prefixes()
    elif kg == "dbpedia":
        prefixes += dbpedia_prefixes()
    else:
        raise RuntimeError(f"unknown knowledge graph {kg}")
    return prefixes


def _insert_newlines_after_brackets_and_triples(query: str) -> str:
    formatted = []
    current_quote = None
    in_literal = False
    for c in query:
        if c in ["'", '"'] and (current_quote is None or current_quote == c):
            if in_literal:
                in_literal = False
                current_quote = None
            else:
                in_literal = True
                current_quote = c
        elif c in [".", "{"] and not in_literal:
            c = " " * (formatted[-1] != " ") + c + "\n"
        elif c == "}" and not in_literal:
            c = "\n" + c + "\n"
        formatted.append(c)
    return "".join(formatted)


def _count_open_and_closing_brackets(query: str) -> tuple[int, int]:
    current_quote = None
    in_literal = False
    open = close = 0
    for c in query:
        if c in ["'", '"'] and (current_quote is None or current_quote == c):
            if in_literal:
                in_literal = False
                current_quote = None
            else:
                in_literal = True
                current_quote = c
        elif c == "{" and not in_literal:
            open += 1
        elif c == "}" and not in_literal:
            close += 1
    return open, close


SPARQL_KEYWORDS = [
    "BASE",
    "PREFIX",
    "SELECT",
    "DISTINCT",
    "REDUCED",
    "CONSTRUCT",
    "DESCRIBE",
    "ASK",
    "FROM",
    "FROM NAMED",
    "WHERE",
    "ORDER BY",
    "ASC",
    "DESC",
    "LIMIT",
    "OFFSET",
    "VALUES",
    "BIND",
    "UNION",
    "OPTIONAL",
    "FILTER",
    "GRAPH",
    # Aggregate functions
    "COUNT",
    "SUM",
    "MIN",
    "MAX",
    "AVG",
    "GROUP_CONCAT",
    "SAMPLE",
    # Date functions
    "YEAR",
    "MONTH",
    "DAY",
    "HOURS",
    "MINUTES",
    "SECONDS",
    "TIMEZONE",
    "TZ",
    # String functions
    "UCASE",
    "LCASE",
    "STR",
    "STRLANG",
    "STRDT",
    "STRSTARTS",
    "STRENDS",
    "STRLEN",
    "SUBSTR",
    "REPLACE",
    "REGEX",
    # Other functions and operators
    "EXISTS",
    "NOT EXISTS",
    "BOUND",
    "IF",
    "COALESCE",
    "RAND",
    "ABS",
    "ROUND",
    "CEIL",
    "FLOOR",
    "URI",
    "BNODE",
    "MD5",
    "SHA1",
    "SHA256",
    "SHA384",
    "SHA512",
    "NOW",
    "UUID",
    "STRUUID",
    "ISURI",
    "ISBLANK",
    "ISLITERAL",
    "ISNUMERIC",
    "LANG",
    "LANGMATCHES",
    "DATATYPE",
    "IRI",
    "SAMETERM",
    "ISIRI",
    "ISBLANK",
    "ISLITERAL"
]

SPARQL_NEWLINE = {
    "PREFIX",
    "SELECT",
    "OPTIONAL",
    "FILTER",
    "VALUES",
    "ORDER BY",
    "GROUP BY",
    "LIMIT"
}


def _insert_newlines_before_keywords(query: str) -> str:
    for keyword in SPARQL_NEWLINE:
        query = re.sub(
            rf"\b{keyword}\b",
            lambda m: "\n" + m.group(0),
            query,
            flags=re.IGNORECASE
        )
    return query


def _uppercase_sparql_keywords(query: str) -> str:
    start_idx = 0
    new_query = ""
    # uppercase only outside of var, ent, and prop fields
    for match in re.finditer(
        r"<bov>.+?<eov>|<boe>.+?<eoe>|<bop>.+?<eop>",
        query
    ):
        query_part = query[start_idx:match.start()]
        for keyword in SPARQL_KEYWORDS:
            query_part = re.sub(
                rf"\b{keyword}\b",
                lambda m: m.group(0).upper(),
                query_part,
                flags=re.IGNORECASE
            )
        new_query += query_part + match.group(0)
        start_idx = match.end()
    # dont forget the rest of the query
    if start_idx < len(query):
        query_part = query[start_idx:]
        for keyword in SPARQL_KEYWORDS:
            query_part = re.sub(
                rf"\b{keyword}\b",
                lambda m: m.group(0).upper(),
                query_part,
                flags=re.IGNORECASE
            )
        new_query += query_part
    return new_query


def _pretty_format_sparql(query: str) -> str:
    query = _insert_newlines_after_brackets_and_triples(query)
    query = _insert_newlines_before_keywords(query)

    formatted_query = []
    indent_level = 0

    for line in query.splitlines():
        line = line.strip()
        if not line:
            continue

        if line.startswith("}"):
            indent_level -= 1
        formatted_query.append("  " * indent_level + line)
        if line.endswith("{"):
            indent_level += 1

    return "\n".join(formatted_query)


def format_sparql(
    sparql: str,
    prefixes: list[str] | None = None,
    pretty: bool = False
) -> str:
    # always uppercase sparql keywords
    sparql = _uppercase_sparql_keywords(sparql)

    # save existing prefixes for later
    existing_prefixes = []

    def _save_and_erase(m: re.Match) -> str:
        existing_prefixes.append(m.group(0))
        return ""
    sparql = re.sub(PREFIX_REGEX, _save_and_erase, sparql)

    # filter only for used prefixes
    parts = set()
    for pfx in existing_prefixes + (prefixes or []):
        pfx_match = re.search(PREFIX_REGEX, pfx)
        assert pfx_match is not None
        pfx_short = pfx_match.group(2)
        if f"{pfx_short}:" not in sparql:
            continue
        parts.add(pfx)

    sep = " "
    if pretty:
        # pretty format sparql with correct indentation after
        # brackets and other keywords
        sep = "\n"
        sparql = _pretty_format_sparql(sparql)

    parts = list(sorted(parts))
    parts.append(sparql)
    return sep.join(parts)


def prepare_sparql_query(
    s: str,
    entity_index: prefix.Vec,
    property_index: prefix.Vec,
    var_special_tokens: Tuple[str, str] = ("<bov>", "<eov>"),
    entity_special_tokens: Tuple[str, str] = ("<boe>", "<eoe>"),
    property_special_tokens: Tuple[str, str] = ("<bop>", "<eop>"),
    kg: str | None = None,
    pretty: bool = False,
    post_fn: Callable[[str, REP, REP, REP], str] | None = None,
) -> str:
    s, vars = replace_vars(s, *var_special_tokens)
    s, ents = replace_entities(s, entity_index, *entity_special_tokens)
    s, props = replace_properties(s, property_index, *property_special_tokens)
    if post_fn is not None:
        s = post_fn(s, vars, ents, props)
        s = re.sub(r"\s+", " ", s, flags=re.DOTALL).strip()
    s = format_sparql(s, get_prefixes(kg), pretty)
    return s


def _qlever_ask_to_select_post_fn(
    s: str,
    vars: REP,
    ents: REP,
    props: REP
) -> str:
    # qlever does not support ask queries yet,
    # so we transform ask to select
    ask_match = re.search(
        r"^(?:\s*PREFIX\s+\w+:\s*<.*?>)*\s*(\bASK\b\s+\bWHERE\b)\s*{(.*)}",
        s,
        flags=re.IGNORECASE
    )
    if ask_match is None:
        # return if not a ask query
        return s
    if len(vars) > 0:
        # we have some variables,
        # so it is enough to replace ask where with select * where
        return s[:ask_match.start(1)] + "SELECT * WHERE" + s[ask_match.end(1):]
    else:
        reps = ents or props
        if len(reps) == 0:
            # should not happen, except for malformed queries
            return s
        # we have no variables, so we need to introduce one
        # to make qlever happy
        _, rep = reps[0]
        var = _get_unique_var_name()

        prefix = s[:ask_match.start(1)] + "SELECT ?" + var + \
            " WHERE" + s[ask_match.end(1):ask_match.start(2)]
        body = s[ask_match.start(2):ask_match.end(2)]
        values_clause = f" VALUES ?{var} {{ {rep} }}"
        body = body.replace(rep, f"?{var}", 1)
        return prefix + body + values_clause + s[ask_match.end(2):]


class SPARQLRecord:
    def __init__(
        self,
        value: str,
        data_type: str,
        label: Optional[str] = None
    ):
        self.value = value
        self.data_type = data_type
        self.label = label

    def __repr__(self) -> str:
        if self.data_type == "uri":
            last = self.value.split("/")[-1]
            if self.label is not None:
                return f"{self.label} ({last})"
            return last
        else:
            return self.label or self.value


class SPARQLResult:
    def __init__(
        self,
        vars: List[str],
        results: List[Dict[str, SPARQLRecord]]
    ):
        self.vars = vars
        self.results = results

    def __len__(self) -> int:
        return len(self.results)

    def __repr__(self) -> str:
        return f"SPARQLResult({self.vars}, {self.results})"


def query_qlever(
    sparql_query: str,
    kg: str = "wikidata",
    qlever_endpoint: str | None = None
) -> SPARQLResult:
    if qlever_endpoint is None:
        qlever_endpoint = QLEVER_URLS[kg]
    response = requests.get(
        qlever_endpoint,
        params={"query": sparql_query}
    )
    json = response.json()
    if response.status_code != 200:
        msg = json.get("exception", "unknown exception")
        raise RuntimeError(
            f"query {sparql_query} returned with "
            f"status code {response.status_code}:\n{msg}"
        )
    vars = json["head"]["vars"]
    results = []
    for binding in json["results"]["bindings"]:
        result = {}
        for var in vars:
            if var not in binding:
                continue
            value = binding[var]
            result[var] = SPARQLRecord(
                value["value"],
                value["type"]
            )
        results.append(result)
    return SPARQLResult(vars, results)


PREFIX_REGEX = re.compile(
    r"(prefix\s+(\S+?):\s*<.+?>)",
    flags=re.IGNORECASE | re.DOTALL
)


def add_labels(
    result: SPARQLResult,
    sparql: str,
    lang: str = "en",
    kg: str = "wikidata",
    qlever_endpoint: str | None = None
):
    if kg == "wikidata":
        ent_url = "http://www.wikidata.org/entity/"
        ent_re = re.compile(f"^{ent_url}Q\\d+$")
    elif kg == "freebase":
        ent_url = "http://rdf.freebase.com/ns/"
        ent_re = re.compile(f"^{ent_url}.+$")
    elif kg == "dbpedia":
        ent_url = "http://dbpedia.org/resource/"
        ent_re = re.compile(f"^{ent_url}.+$")
    else:
        raise RuntimeError(f"unknown kg {kg}")

    # get vars that refer to entities
    if len(result) > 0:
        vars = [
            var
            for var in result.vars
            if (
                var in result.results[0]
                and ent_re.match(result.results[0][var].value) is not None
            )
        ]
    else:
        vars = []

    if len(vars) == 0:
        return

    label_vars = [f"{var}Label" for var in vars]
    label_var_str = " ".join("?" + var for var in label_vars)
    label_filter = " ".join(
        f"OPTIONAL {{ ?{var} rdfs:label ?{var}Label . "
        f"FILTER(LANG(?{var}Label) = \"{lang}\") }}"
        for var in vars
    )

    prefix = " ".join(
        m.group(1)
        for m in re.finditer(PREFIX_REGEX, sparql)
    )
    sub_sparql = re.sub(PREFIX_REGEX, "", sparql).strip()

    query = "PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#> " \
        f"{prefix} " \
        f"SELECT {label_var_str} WHERE {{ " \
        f"{{ {sub_sparql} }} {label_filter} }} "

    label_result = query_qlever(query, kg, qlever_endpoint)
    for i, record in enumerate(label_result.results):
        for var, l_var in zip(vars, label_vars):
            if l_var not in record:
                continue
            result.results[i][var].label = record[l_var].value


def format_qlever_result(
    result: SPARQLResult,
    max_column_width: int = 80,
) -> str:
    if len(result) == 0:
        return "no results"

    if len(result.vars) == 0:
        return "no bindings"

    data = []
    for record in result.results:
        data.append([
            str(record[var]) if var in record else "-"
            for var in result.vars
        ])

    return generate_table(
        headers=[result.vars],
        data=data,
        alignments=["left"] * len(result.vars),
        max_column_width=max_column_width,
    )


def special_token_or_token_ids(
    s: str,
    tok: tokenization.Tokenizer,
    tokenizer_type: str
) -> Tuple[str, List[int]]:
    assert tokenizer_type in {"t5", "llama-2", "gpt2", "mistral"}
    token_id = tok.special_token_to_id(s.strip())
    if token_id is not None:
        return s, [token_id]
    num_pfx = tok.num_prefix_tokens()
    num_sfx = tok.num_suffix_tokens()
    token_ids = tok.tokenize(s).token_ids
    if tokenizer_type == "t5":
        # t5 tokenizer adds prefix space, which we ignore
        num_pfx += 1
    elif tokenizer_type == "mistral":
        while num_pfx < len(token_ids) and token_ids[num_pfx] == 28705:
            num_pfx += 1
    token_ids = token_ids[num_pfx:len(token_ids)-num_sfx]
    return tok.de_tokenize(token_ids, False).strip(), token_ids


def longest_overlap(
    list1: List[int],
    list2: List[int]
) -> List[int]:
    min_len = min(len(list1), len(list2))
    overlap = 0

    for i in range(1, min_len + 1):
        if list1[-i:] == list2[:i]:
            overlap = i

    return list1[-overlap:] if overlap else []


def format_example(
    question: str,
    sparql: str,
) -> str:
    return f"\"{question}\" to \"{sparql}\""


def format_examples(
    examples: List[str],
) -> str:
    formatted = ""
    for i, example in enumerate(examples):
        formatted += example
        if i < len(examples) - 1:
            formatted += ", "
        if len(examples) > 1 and i == len(examples) - 2:
            formatted += "and "
    return formatted


def format_input(
    question: str,
    examples: List[str],
    kg: Optional[str] = None,
) -> str:
    if kg is None:
        kg = "knowledge graph"
    else:
        kg = KNOWLEDGE_GRAPHS[kg]
    ipt = f"Generate a SPARQL query over {kg} for the question \"{question}\""
    if len(examples) == 0:
        return ipt
    return (
        f"{ipt} with example{'s' * (len(examples) > 1)} "
        + format_examples(examples)
    )


def query_entities(
    sparql: str,
    kg: str = "wikidata",
    qlever_endpoint: str | None = None
) -> Optional[Set[Tuple[str, ...]]]:
    try:
        result = query_qlever(sparql, kg, qlever_endpoint)
        if len(result) == 0:
            return set()
        return set(
            tuple(
                r[var].value if var in r else ""
                for var in result.vars
            )
            for r in result.results
        )
    except Exception:
        return None


def calc_f1(
    pred: str,
    target: str,
    allow_empty_target: bool = True,
    kg: str = "wikidata",
    qlever_endpoint: str | None = None
) -> Tuple[Optional[float], bool, bool]:
    pred_set = query_entities(pred, kg, qlever_endpoint)
    target_set = query_entities(target, kg, qlever_endpoint)
    if pred_set is None or target_set is None:
        return None, pred_set is None, target_set is None
    if len(target_set) == 0 and not allow_empty_target:
        return None, False, True
    if len(pred_set) == 0 and len(target_set) == 0:
        return 1.0, False, False
    tp = len(pred_set.intersection(target_set))
    fp = len(pred_set.difference(target_set))
    fn = len(target_set.difference(pred_set))
    # calculate precision, recall and f1
    if tp > 0:
        p = tp / (tp + fp)
        r = tp / (tp + fn)
        f1 = 2 * p * r / (p + r)
    else:
        f1 = 0.0
    return f1, False, False


def _get_unique_var_name() -> str:
    return str(uuid.uuid4()).replace("-", "_")


def _autocomplete_sparql(
    sparql: str,
    current_state: str,
    additional_constraints: Callable[[str], str] | None = None
) -> tuple[str, str] | None:
    open, close = _count_open_and_closing_brackets(sparql)
    if close >= open:
        return None
    # generate unique variable name
    var = _get_unique_var_name()
    sparql += f" ?{var}"
    if current_state == "subject":
        pred_var = _get_unique_var_name()
        obj_var = _get_unique_var_name()
        sparql += f" ?{pred_var} ?{obj_var} ."
    elif current_state == "predicate":
        obj_var = _get_unique_var_name()
        sparql += f" ?{obj_var} ."
    else:
        sparql += " ."
    if additional_constraints is not None:
        sparql += additional_constraints(var)
    sparql += "".join(" }" * (open - close))
    return sparql, var


def get_completions(
    sparql: str,
    current_state: str,
    entity_index: prefix.Vec,
    property_index: prefix.Vec,
    kg: str = "wikidata",
    lang: str = "en",
    max_size: int = 8192
) -> list[str] | None:
    assert current_state in {"subject", "predicate", "object"}
    additional_constraints = None
    if kg == "wikidata" and current_state == "predicate":
        def _wikidata_predicate_constraints(var: str) -> str:
            prop_var = _get_unique_var_name()
            return f"?{prop_var} wikibase:directClaim ?{var} . " \
                f"?{prop_var} rdfs:label ?{prop_var}_label . "\
                f"FILTER(LANG(?{prop_var}_label) = '{lang}')"
        additional_constraints = _wikidata_predicate_constraints

    completion = _autocomplete_sparql(
        sparql,
        current_state,
        additional_constraints
    )
    if completion is None:
        return None
    sparql, var = completion
    sparql += f" LIMIT {max_size + 1}"

    # replace entities and properties and format sparql
    sparql = prepare_sparql_query(
        sparql,
        entity_index,
        property_index,
        kg=kg,
        post_fn=_qlever_ask_to_select_post_fn
    )
    # replace SELECT ... WHERE with SELECT DISTINCT ?var WHERE
    # in outermost SELECT
    sparql = re.sub(
        r"\bSELECT\b\s+.*?\s+\bWHERE\b",
        f"SELECT DISTINCT ?{var} WHERE",
        sparql,
        flags=re.IGNORECASE,
        count=1
    )
    try:
        result = query_qlever(sparql, kg)
    except Exception:
        return None

    if len(result.results) > max_size:
        return None

    if kg == "wikidata":
        results = []
        if current_state == "predicate":
            pattern = r"^http://www.wikidata.org/prop/direct/(P\d+)$"
            prefix = "wdt:"
        else:
            pattern = r"^http://www.wikidata.org/entity/(Q\d+)$"
            prefix = "wd:"

        pattern = re.compile(pattern)

        for res in result.results:
            rec = res.get(var)
            if rec is None:
                continue
            value = next(pattern.finditer(rec.value), None)
            if value is None:
                continue
            results.append(prefix + value.group(1))
    else:
        raise NotImplementedError
    return results
