.PHONY: install uninstall

install:
	uv tool install --force --reinstall .

uninstall:
	uv tool uninstall remarkable
