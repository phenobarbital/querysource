venv:
	python3.10 -m venv .venv
	echo 'run `source .venv/bin/activate` to start develop QuerySource'

develop:
	pip install git+https://github.com/m-wrzr/populartimes.git@master#egg=populartimes
	pip install --upgrade navigator-api
	pip install -e .
	python -m pip install -Ur docs/requirements-dev.txt
	echo 'start develop QuerySource'

setup:
	python -m pip install -Ur docs/requirements-dev.txt

dev:
	flit install --symlink

release: lint test clean
	flit publish

format:
	python -m black querysource

lint:
	python -m pylint --rcfile .pylintrc querysource/*.py
	python -m pylint --rcfile .pylintrc querysource/outputs/*.py
	python -m pylint --rcfile .pylintrc querysource/providers/*.py
	python -m pylint --rcfile .pylintrc querysource/parsers/*.py
	python -m black --check navigator

test:
	python -m coverage run -m querysource.tests
	python -m coverage report
	python -m mypy querysource/*.py

distclean:
	rm -rf .venv
