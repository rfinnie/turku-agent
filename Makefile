# SPDX-PackageName: turku-agent
# SPDX-PackageSupplier: Ryan Finnie <ryan@finnie.org>
# SPDX-PackageDownloadLocation: https://github.com/rfinnie/turku-agent
# SPDX-FileCopyrightText: © 2015 Canonical Ltd.
# SPDX-FileCopyrightText: © 2015 Ryan Finnie <ryan@finnie.org>
# SPDX-License-Identifier: GPL-3.0-or-later

SYSTEMD_SYSTEM := /etc/systemd/system

install-systemd:
	install -m 0644 turku-agent-ping.service $(SYSTEMD_SYSTEM)/turku-agent-ping.service
	install -m 0644 turku-agent-ping.timer $(SYSTEMD_SYSTEM)/turku-agent-ping.timer
	systemctl enable turku-agent-ping.timer
	systemctl start turku-agent-ping.timer
	install -m 0644 turku-update-config.service $(SYSTEMD_SYSTEM)/turku-update-config.service
	install -m 0644 turku-update-config.timer $(SYSTEMD_SYSTEM)/turku-update-config.timer
	systemctl enable turku-update-config.timer
	systemctl start turku-update-config.timer
