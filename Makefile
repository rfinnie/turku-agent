PYTHON := python3
SYSTEMD_SYSTEM := /etc/systemd/system

all: build

build:
	$(PYTHON) setup.py build

lint:
	$(PYTHON) -mtox -e py-flake8

test:
	$(PYTHON) -mtox

test-quick:
	$(PYTHON) -mtox -e py-black,py-flake8,py-pytest-quick

black-check:
	$(PYTHON) -mtox -e py-black

black:
	$(PYTHON) -mtox -e py-black-reformat

install: build
	$(PYTHON) setup.py install

install-systemd:
	install -m 0644 turku-agent-ping.service $(SYSTEMD_SYSTEM)/turku-agent-ping.service
	install -m 0644 turku-agent-ping.timer $(SYSTEMD_SYSTEM)/turku-agent-ping.timer
	systemctl enable turku-agent-ping.timer
	systemctl start turku-agent-ping.timer
	install -m 0644 turku-update-config.service $(SYSTEMD_SYSTEM)/turku-update-config.service
	install -m 0644 turku-update-config.timer $(SYSTEMD_SYSTEM)/turku-update-config.timer
	systemctl enable turku-update-config.timer
	systemctl start turku-update-config.timer

clean:
	$(PYTHON) setup.py clean
	$(RM) -r build MANIFEST
