PIP_BIN=$(shell which pip)
LINK_TARGET=/usr/local/bin/ytap

.PHONY: all install link help

all: install link

install: requirements.in
requirements.in: setup.py
	$(PIP_BIN) install --user -r $@
	touch $@

link: $(LINK_TARGET)
$(LINK_TARGET):
	chmod +x ytap.py
	ln -s $(shell pwd)/ytap.py $(LINK_TARGET)

help:
	@cat README.md

