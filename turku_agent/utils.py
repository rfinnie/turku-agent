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

import copy
import json
import logging
import os
import platform
import random
import string
import subprocess
import urllib.parse
import uuid

import requests

try:
    import yaml
except ImportError as e:
    yaml = e


class RuntimeLock:
    name = None
    file = None

    def __init__(self, name):
        import fcntl

        file = open(name, "w")
        try:
            fcntl.lockf(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except IOError as e:
            import errno

            if e.errno in (errno.EACCES, errno.EAGAIN):
                raise
        file.write("%10s\n" % os.getpid())
        file.flush()
        file.seek(0)
        self.name = name
        self.file = file

    def close(self):
        if self.file:
            self.file.close()
            self.file = None
            os.unlink(self.name)

    def __del__(self):
        self.close()

    def __enter__(self):
        self.file.__enter__()
        return self

    def __exit__(self, exc, value, tb):
        result = self.file.__exit__(exc, value, tb)
        self.close()
        return result


def acquire_lock(name):
    return RuntimeLock(name)


def json_dump_p(obj, f):
    """Calls json.dump with standard (pretty) formatting"""
    return json.dump(obj, f, sort_keys=True, indent=4, separators=(",", ": "))


def config_load_file(file):
    """Load and return a .json or (if available) .yaml configuration file"""
    with open(file) as f:
        try:
            if file.endswith(".yaml") and not isinstance(yaml, ImportError):
                return yaml.safe_load(f)
            else:
                return json.load(f)
        except Exception:
            raise ValueError("Error loading {}".format(file))


def dict_merge(s, m):
    """Recursively merge one dict into another."""
    if not isinstance(m, dict):
        return m
    out = copy.deepcopy(s)
    for k, v in list(m.items()):
        if k in out and isinstance(out[k], dict):
            out[k] = dict_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


class SafeWrite:
    """(Try to) safely write files with minimum collision possibility"""

    filename = None
    temp_filename = None
    file = None

    def __init__(self, filename, filemode=None):
        self.filename = filename
        self.temp_filename = "{}.tmp{}~".format(self.filename, str(uuid.uuid4()))
        self.file = open(self.temp_filename, "w")
        if filemode is not None:
            os.fchmod(self.file.fileno(), filemode)
        for m in dir(self.file):
            if m.startswith("_"):
                continue
            if m == "close":
                continue
            setattr(self, m, getattr(self.file, m))

    def close(self):
        if not self.file:
            return
        self.file.close()
        self.file = None
        os.rename(self.temp_filename, self.filename)

    def __del__(self):
        self.close()

    def __enter__(self):
        return self

    def __exit__(self, exc, value, tb):
        self.close()


def load_config(config_dir):
    config = {}
    config["config_dir"] = config_dir

    config_d = os.path.join(config["config_dir"], "config.d")
    sources_d = os.path.join(config["config_dir"], "sources.d")

    # Merge in config.d files to the root level
    config_files = []
    if os.path.isdir(config_d):
        config_files = [
            os.path.join(config_d, fn)
            for fn in os.listdir(config_d)
            if (
                fn.endswith(".json")
                or (fn.endswith(".yaml") and not isinstance(yaml, ImportError))
            )
            and os.path.isfile(os.path.join(config_d, fn))
            and os.access(os.path.join(config_d, fn), os.R_OK)
        ]
    config_files.sort()
    for file in config_files:
        config = dict_merge(config, config_load_file(file))

    if "var_dir" not in config:
        config["var_dir"] = "/var/lib/turku-agent"

    var_config_d = os.path.join(config["var_dir"], "config.d")

    # Load /var config.d files (.json only)
    var_config = {}
    var_config_files = []
    if os.path.isdir(var_config_d):
        var_config_files = [
            os.path.join(var_config_d, fn)
            for fn in os.listdir(var_config_d)
            if fn.endswith(".json")
            and os.path.isfile(os.path.join(var_config_d, fn))
            and os.access(os.path.join(var_config_d, fn), os.R_OK)
        ]
    var_config_files.sort()
    for file in var_config_files:
        var_config = dict_merge(var_config, config_load_file(file))
    # /etc gets priority over /var
    var_config = dict_merge(var_config, config)
    config = var_config

    if "lock_dir" not in config:
        config["lock_dir"] = "/var/lock"

    if "rsyncd_command" not in config:
        config["rsyncd_command"] = ["rsync"]

    if "rsyncd_local_address" not in config:
        config["rsyncd_local_address"] = "127.0.0.1"

    if "ssh_command" not in config:
        config["ssh_command"] = ["ssh"]

    if "ssh_key_type" not in config:
        config["ssh_key_type"] = "ed25519"

    # If a go/no-go program is defined, run it and only go if it exits 0.
    # Type: String (program with no args) or list (program first, optional arguments after)
    if "gonogo_program" not in config:
        config["gonogo_program"] = None

    var_sources_d = os.path.join(config["var_dir"], "sources.d")

    # Validate the unit name
    if "unit_name" not in config:
        config["unit_name"] = platform.node()
        # If this isn't in the on-disk config, don't write it; just
        # generate it every time

    # Pull the SSH public key
    if os.path.isfile(os.path.join(config["var_dir"], "ssh_key.pub")):
        with open(os.path.join(config["var_dir"], "ssh_key.pub")) as f:
            config["ssh_public_key"] = f.read().rstrip()
        config["ssh_public_key_file"] = os.path.join(config["var_dir"], "ssh_key.pub")
        config["ssh_private_key_file"] = os.path.join(config["var_dir"], "ssh_key")

    sources_config = {}
    # Merge in sources.d files to the sources dict
    # (.json/.yaml in /etc, .json only in /var)
    sources_directories = []
    if os.path.isdir(var_sources_d):
        sources_directories.append(var_sources_d)
    if os.path.isdir(sources_d):
        sources_directories.append(sources_d)
    for directory in sources_directories:
        for file in sorted(
            [
                os.path.join(directory, fn)
                for fn in os.listdir(directory)
                if (
                    fn.endswith(".json")
                    or (
                        fn.endswith(".yaml")
                        and directory == sources_d
                        and not isinstance(yaml, ImportError)
                    )
                )
                and os.path.isfile(os.path.join(directory, fn))
                and os.access(os.path.join(directory, fn), os.R_OK)
            ]
        ):
            sources_config = dict_merge(sources_config, config_load_file(file))

    # Check for required sources options
    for s in list(sources_config.keys()):
        if "path" not in sources_config[s]:
            del sources_config[s]

    config["sources"] = sources_config

    return config


def fill_config(config):
    config_d = os.path.join(config["config_dir"], "config.d")
    sources_d = os.path.join(config["config_dir"], "sources.d")
    var_config_d = os.path.join(config["var_dir"], "config.d")
    var_sources_d = os.path.join(config["var_dir"], "sources.d")

    # Create required directories
    for d in (config_d, sources_d, var_config_d, var_sources_d):
        if not os.path.isdir(d):
            os.makedirs(d)

    # Validate the machine UUID/secret
    write_uuid_data = False
    if "machine_uuid" not in config:
        config["machine_uuid"] = str(uuid.uuid4())
        write_uuid_data = True
    if "machine_secret" not in config:
        config["machine_secret"] = "".join(
            random.choice(string.ascii_letters + string.digits) for i in range(30)
        )
        write_uuid_data = True
    # Write out the machine UUID/secret if needed
    if write_uuid_data:
        with SafeWrite(
            os.path.join(var_config_d, "10-machine_uuid.json"), filemode=0o600
        ) as f:
            json_dump_p(
                {
                    "machine_uuid": config["machine_uuid"],
                    "machine_secret": config["machine_secret"],
                },
                f,
            )

    # Restoration configuration
    write_restore_data = False
    if "restore_path" not in config:
        config["restore_path"] = "/var/backups/turku-agent/restore"
        write_restore_data = True
    if "restore_module" not in config:
        config["restore_module"] = "turku-restore"
        write_restore_data = True
    if "restore_username" not in config:
        config["restore_username"] = str(uuid.uuid4())
        write_restore_data = True
    if "restore_password" not in config:
        config["restore_password"] = "".join(
            random.choice(string.ascii_letters + string.digits) for i in range(30)
        )
        write_restore_data = True
    if write_restore_data:
        with SafeWrite(
            os.path.join(var_config_d, "10-restore.json"), filemode=0o600
        ) as f:
            restore_out = {
                "restore_path": config["restore_path"],
                "restore_module": config["restore_module"],
                "restore_username": config["restore_username"],
                "restore_password": config["restore_password"],
            }
            json_dump_p(restore_out, f)
    if not os.path.isdir(config["restore_path"]):
        os.makedirs(config["restore_path"])

    # Generate the SSH keypair if it doesn't exist
    if "ssh_private_key_file" not in config:
        subprocess.check_call(
            [
                "ssh-keygen",
                "-t",
                config["ssh_key_type"],
                "-N",
                "",
                "-C",
                "turku",
                "-f",
                os.path.join(config["var_dir"], "ssh_key"),
            ]
        )
        with open(os.path.join(config["var_dir"], "ssh_key.pub")) as f:
            config["ssh_public_key"] = f.read().rstrip()
        config["ssh_public_key_file"] = os.path.join(config["var_dir"], "ssh_key.pub")
        config["ssh_private_key_file"] = os.path.join(config["var_dir"], "ssh_key")

    for s in config["sources"]:
        # Check for missing usernames/passwords
        if not (
            "username" in config["sources"][s] or "password" in config["sources"][s]
        ):
            if "username" not in config["sources"][s]:
                config["sources"][s]["username"] = str(uuid.uuid4())
            if "password" not in config["sources"][s]:
                config["sources"][s]["password"] = "".join(
                    random.choice(string.ascii_letters + string.digits)
                    for i in range(30)
                )
            with SafeWrite(
                os.path.join(var_sources_d, "10-" + s + ".json"), filemode=0o600
            ) as f:
                json_dump_p(
                    {
                        s: {
                            "username": config["sources"][s]["username"],
                            "password": config["sources"][s]["password"],
                        }
                    },
                    f,
                )


def api_call(api_url, cmd, post_data, timeout=5):
    """Turku API call client"""
    url = urllib.parse.urljoin(api_url + "/", cmd)
    headers = {"Accept": "application/json"}
    logging.debug("API request: {} {}".format(url, post_data))
    r = requests.post(url, json=post_data, headers=headers, timeout=timeout)
    r.raise_for_status()
    response_json = r.json()
    logging.debug("API response: {} {}".format(r.status_code, response_json))
    return response_json
