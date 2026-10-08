.PHONY: install migrate verify api eval

install:
	pip install -r requirements.txt

migrate:
	python3 db/migrate.py

verify:
	python3 eval/verify_chunks.py

api:
	uvicorn src.api:app --reload

eval:
	python3 eval/eval_retrieval.py
