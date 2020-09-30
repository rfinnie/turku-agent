# Turku backups - client agent
# Copyright 2015 Canonical Ltd.
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License version 3, as published by
# the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranties of MERCHANTABILITY,
# SATISFACTORY QUALITY, or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License along with
# this program.  If not, see <http://www.gnu.org/licenses/>.

import logging
import os
import random
import subprocess
import time

from .utils import json_dumps_p, load_config, fill_config, acquire_lock, api_call


class IncompleteConfigError(Exception):
    pass


def parse_args():
    import argparse

    parser = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument("--config-dir", "-c", type=str, default="/etc/turku-agent")
    parser.add_argument("--wait", "-w", type=float)
    parser.add_argument("--debug", action="store_true")
    return parser.parse_args()


def write_conf_files(config):
    # Build rsyncd.conf
    built_rsyncd_conf = (
        "address = %s\n" % config["rsyncd_local_address"]
        + "port = %d\n" % config["rsyncd_local_port"]
        + "log file = /dev/stdout\n"
        + "uid = %s\n" % config["rsyncd_user"]
        + "gid = %s\n" % config["rsyncd_group"]
        + "list = false\n\n"
    )
    rsyncd_secrets = []
    rsyncd_secrets.append((config["restore_username"], config["restore_password"]))
    built_rsyncd_conf += (
        "[%s]\n"
        + "    path = %s\n"
        + "    auth users = %s\n"
        + "    secrets file = %s\n"
        + "    read only = false\n\n"
    ) % (
        config["restore_module"],
        config["restore_path"],
        config["restore_username"],
        os.path.join(config["var_dir"], "rsyncd.secrets"),
    )
    for s in config["sources"]:
        sd = config["sources"][s]
        rsyncd_secrets.append((sd["username"], sd["password"]))
        built_rsyncd_conf += (
            "[%s]\n"
            + "    path = %s\n"
            + "    auth users = %s\n"
            + "    secrets file = %s\n"
            + "    read only = true\n\n"
        ) % (
            s,
            sd["path"],
            sd["username"],
            os.path.join(config["var_dir"], "rsyncd.secrets"),
        )
    with open(os.path.join(config["var_dir"], "rsyncd.conf"), "w") as f:
        f.write(built_rsyncd_conf)

    # Build rsyncd.secrets
    built_rsyncd_secrets = ""
    for (username, password) in rsyncd_secrets:
        built_rsyncd_secrets += username + ":" + password + "\n"
    with open(os.path.join(config["var_dir"], "rsyncd.secrets"), "w") as f:
        os.fchmod(f.fileno(), 0o600)
        f.write(built_rsyncd_secrets)


def init_is_upstart():
    try:
        return "upstart" in subprocess.check_output(
            ["initctl", "version"], stderr=subprocess.DEVNULL, universal_newlines=True
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


def start_services(service_name="turku-agent-rsyncd"):
    """Start turku services (rsyncd) if not already running

    Note that we do *not* need to reload rsyncd when changing rsyncd.conf,
    as it rereads it on every client connection; but we may need to start
    it as it won't start if its configuration file doesn't exist.
    """
    if init_is_upstart():
        # With Upstart, start will fail if the service is already running,
        # so we need to check for that first.
        try:
            if "start/running" in subprocess.check_output(
                ["status", service_name],
                stderr=subprocess.STDOUT,
                universal_newlines=True,
            ):
                return
        except subprocess.CalledProcessError:
            pass
    else:
        # Check status of rsyncd.
        # All known inits (except Upstart, see above), given "service
        # $SERVICE status", will exit 0 if started, but >0 if not.
        # Most inits will treat "service $STATUS start" as idempotent
        # and silently ignore the start (and exit 0) if already started.
        # However, FreeBSD's rc.d fails hard if started, so let's always
        # check status (unless we're Upstart).
        try:
            subprocess.check_call(
                ["service", service_name, "status"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return
        except subprocess.CalledProcessError:
            pass
    subprocess.check_call(["service", service_name, "start"])


def send_config(config):
    required_keys = ["api_url"]
    if "api_auth" not in config:
        required_keys += ["api_auth_name", "api_auth_secret"]
    for k in required_keys:
        if k not in config:
            raise IncompleteConfigError('Required config "%s" not found.' % k)

    api_out = {}
    if ("api_auth_name" in config) and ("api_auth_secret" in config):
        # name/secret style
        api_out["auth"] = {
            "name": config["api_auth_name"],
            "secret": config["api_auth_secret"],
        }
    else:
        # nameless secret style
        api_out["auth"] = config["api_auth"]

    # Merge the following options into the machine section
    machine_merge_map = (
        ("machine_uuid", "uuid"),
        ("machine_secret", "secret"),
        ("environment_name", "environment_name"),
        ("service_name", "service_name"),
        ("unit_name", "unit_name"),
        ("ssh_public_key", "ssh_public_key"),
        ("published", "published"),
    )
    api_out["machine"] = {}
    for a, b in machine_merge_map:
        if a in config:
            api_out["machine"][b] = config[a]

    api_out["machine"]["sources"] = config["sources"]

    api_call(config["api_url"], "update_config", api_out)


def main():
    args = parse_args()
    # Sleep a random amount of time if requested
    if args.wait:
        time.sleep(random.uniform(0, args.wait))

    config = load_config(args.config_dir)
    lock = acquire_lock(os.path.join(config["lock_dir"], "turku-update-config.lock"))
    fill_config(config)
    if args.debug:
        print(json_dumps_p(config))
    write_conf_files(config)
    try:
        send_config(config)
    except Exception as e:
        if args.debug:
            raise
        logging.exception(e)
        return 1
    if config["rsyncd_service_name"] is not None:
        start_services(config["rsyncd_service_name"])

    lock.close()
