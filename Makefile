venv:
	python3.11 -m venv .venv
	echo 'run `source .venv/bin/activate` to start develop QuerySource'

install:
	# pip install --upgrade git+https://github.com/GrocerCheck/LivePopularTimes
	pip install --upgrade asyncdb[all]
	pip install --upgrade navigator-session
	pip install --upgrade navigator-auth[uvloop]
	pip install --upgrade navigator-api[uvloop,locale]
	pip install -e .
	echo 'start using QuerySource'

jupyter:
	pip install git+https://github.com/m-wrzr/populartimes.git@master#egg=populartimes
	python -m pip install -Ur docs/requirements-dev.txt
	pip install --upgrade asyncdb[all]
	pip install --upgrade navigator-session
	pip install --upgrade navigator-auth[uvloop]
	pip install --upgrade navigator-api[uvloop,locale]
	pip install elyra[all]==3.15.0
	pip install jupyterlab-code-snippets
	pip install -e .[jupyter]
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
