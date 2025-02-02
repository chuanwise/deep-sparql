import time
import os
from typing import Dict, Any

from flask import Response, jsonify, request, abort

from text_utils.api.server import TextProcessingServer, Error
from text_utils.api.utils import ProgressIterator

from deep_sparql.api.generator import SPARQLGenerator
from deep_sparql.utils import format_sparql


class SPARQLServer(TextProcessingServer):
    text_processor_cls = SPARQLGenerator

    def __init__(self, config: Dict[str, Any]):
        assert "feedback_file" in config, "missing feedback_file in config"
        super().__init__(config)
        self.use_cache = self.config.get("kv_cache", True)
        self.batch_size = self.config.get("batch_size", 1)
        feedback_dir = os.path.dirname(config["feedback_file"])
        if feedback_dir:
            os.makedirs(feedback_dir, exist_ok=True)

        for cfg in config["models"]:
            if "entity_index" not in cfg or "property_index" not in cfg:
                continue
            if "path" in cfg:
                name = cfg["path"]
            else:
                name = cfg["name"]
            gen_name = self.name_to_text_processor[name]
            gen = self.text_processors[gen_name]
            assert isinstance(gen, SPARQLGenerator)
            example_index = cfg.get("example_index", None)
            gen.set_indices(
                cfg["entity_index"],
                cfg["property_index"],
                example_index
            )
            self.logger.info(
                f"loaded indices from {cfg['entity_index']} "
                f"and {cfg['property_index']} for {gen_name}"
            )

        @self.server.route(f"{self.base_url}/feedback", methods=["POST"])
        def _feedback() -> Response:
            json = request.get_json()
            if json is None:
                return abort(Response("request body must be json", status=400))
            elif "question" not in json:
                return abort(Response("missing question in json", status=400))
            elif "sparql" not in json:
                return abort(Response("missing sparql in json", status=400))
            elif "feedback" not in json:
                return abort(Response("missing feedback in json", status=400))

            feedback = json["feedback"]
            if feedback not in ["helpful", "unhelpful"]:
                return abort(Response("invalid feedback", status=400))

            with open(self.config["feedback_file"], "a", encoding="utf8") as f:
                f.write(f"{json}\n")

            return Response(status=200)

        @self.server.route(f"{self.base_url}/answer", methods=["POST"])
        def _answer() -> Response:
            json = request.get_json()
            if json is None:
                return abort(Response("request body must be json", status=400))
            elif "model" not in json:
                return abort(Response("missing model in json", status=400))
            elif "questions" not in json:
                return abort(Response("missing questions in json", status=400))

            search_strategy = json.get("search_strategy", "greedy")
            beam_width = json.get("beam_width", 5)
            sample_top_k = json.get("sample_top_k", 5)
            subgraph_constraining = json.get("subgraph_constraining", False)
            n_examples = json.get("num_examples", 3)
            kg = json.get("kg", "wikidata")
            lang = json.get("lang", "en")

            try:
                with self.text_processor(json["model"]) as cor:
                    if isinstance(cor, Error):
                        return abort(cor.to_response())
                    assert isinstance(cor, SPARQLGenerator)
                    cor.set_inference_options(
                        strategy=search_strategy,
                        beam_width=beam_width,
                        sample_top_k=sample_top_k,
                        subgraph_constraining=subgraph_constraining,
                        kg=kg,
                        lang=lang,
                        use_cache=self.use_cache
                    )
                    start = time.perf_counter()
                    questions = cor.prepare_questions(
                        [q.strip() for q in json["questions"]],
                        n_examples,
                        self.batch_size
                    )
                    iter = ProgressIterator(
                        ((q, None) for q in questions),
                        size_fn=lambda e: len(e[0].encode("utf8"))
                    )
                    generated = []
                    sparql = []
                    for item in cor.generate_iter(
                        iter,
                        batch_size=self.batch_size,
                        raw=True
                    ):
                        generated.append(format_sparql(item.text, pretty=True))
                        if not cor.has_kg_indices:
                            continue
                        query = cor.prepare_sparql_query(
                            item.text,
                            pretty=True
                        )
                        sparql.append(query)

                    end = time.perf_counter()
                    b = iter.total_size
                    s = end - start

                    output = {
                        "input": questions,
                        "raw": generated,
                        "runtime": {"b": b, "s": s},
                    }
                    if cor.has_kg_indices:
                        output["sparql"] = sparql
                    return jsonify(output)

            except Exception as error:
                return abort(
                    Response(
                        f"request failed with unexpected error: {error}",
                        status=500
                    )
                )
