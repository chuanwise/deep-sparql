WD_PROP=data/kg-index/wikidata-properties-index.tsv
WD_ENT=data/kg-index/wikidata-entities-index.tsv
WD_EX=""

.PHONY: data
data:
	@echo "Preparing simple questions"
	@python scripts/prepare_data.py \
	--wikidata-simple-questions third_party/KGQA-datasets/simple_wikidata_qa \
	--output data/wikidata-simplequestions \
	--entity-index $(WD_ENT) \
	--property-index $(WD_PROP) \
	--example-index $(WD_EX)
	@echo "Preparing lc quad wikidata"
	@python scripts/prepare_data.py \
	--lc-quad2-wikidata third_party/KGQA-datasets/lcquad_v2 \
	--output data/wikidata-lcquad2 \
	--entity-index $(WD_ENT) \
	--property-index $(WD_PROP) \
	--example-index $(WD_EX)
	@echo "Preparing qald 10"
	@python scripts/prepare_data.py \
	--qald-10 third_party/KGQA-datasets/qald/qald-10.py \
	--output data/wikidata-qald10 \
	--entity-index $(WD_ENT) \
	--property-index $(WD_PROP) \
	--example-index $(WD_EX)
	@echo "Preparing mcwq"
	@python scripts/prepare_data.py \
	--mcwq data/raw/mcwq \
	--output data/wikidata-mcwq \
	--entity-index $(WD_ENT) \
	--property-index $(WD_PROP) \
	--example-index $(WD_EX)
	@echo "Preparing qa wiki"
	@python scripts/prepare_data.py \
	--qa-wiki data/raw/qa_wiki/qa_wiki.tsv \
	--output data/wikidata-qa-wiki \
	--entity-index $(WD_ENT) \
	--property-index $(WD_PROP) \
	--example-index $(WD_EX)

MODEL=roberta-base
BATCH_SIZE=32

.PHONY: example-indices
example-indices:
	@echo "Preparing wikidata example index"
	@python scripts/prepare_vector_index_data.py \
	--inputs data/wikidata-*/train_input.txt \
	--targets data/wikidata-*/train_sparql.txt \
	--output data/example-index/wikidata.txt
	@echo "Building wikidata example index"
	@python scripts/build_vector_index.py \
	--data data/example-index/wikidata.txt \
	--output data/example-index/wikidata-$(MODEL) \
	--model $(MODEL) --batch-size $(BATCH_SIZE) --overwrite

.PHONY: wd-indices
wd-indices: wd-nl-index wd-prefix-index wd-vec-index

.PHONY: wd-nl-index
wd-nl-index:
	@echo "Creating knowledge graph natural language indices"
	@make -C third_party/knowledge-graph-natural-language-index index \
	OUT_DIR=data/kg-index

.PHONY: wd-prefix-index
wd-prefix-index:
	@echo "Creating Wikidata prefix indices"
	@cd third_party/text-correction-utils && \
	python scripts/create_prefix_vec.py \
	--file ${SPARQL}/kg-index/wikidata-properties-index.tsv \
	--out ${SPARQL}/prefix-index/wikidata-properties.bin
	@cd third_party/text-correction-utils && \
	python scripts/create_prefix_vec.py \
	--file ${SPARQL}/kg-index/wikidata-entities-index.tsv \
	--out ${SPARQL}/prefix-index/wikidata-entities.bin

