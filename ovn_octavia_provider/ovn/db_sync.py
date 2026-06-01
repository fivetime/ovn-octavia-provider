#    Copyright 2026 Red Hat, Inc. All rights reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import contextlib
import os

from neutron_lib.ovn import constants as ovn_const
from neutron_lib.ovn import db_sync
from oslo_config import cfg
from oslo_log import log

from ovn_octavia_provider.common import clients
from ovn_octavia_provider.common import config as ovn_octavia_config
from ovn_octavia_provider import driver

LOG = log.getLogger(__name__)


class OctaviaOvnSynchronizer(db_sync.BaseOvnDbSynchronizer):
    """Synchronizer for Octavia Load Balancers in OVN.

    This plugin synchronizes Octavia Load Balancers (OVN provider) with the
    OVN Northbound database. It can be invoked as part of the
    neutron-ovn-db-sync-util tool.

    The synchronizer does not support LOG mode (read-only verification).
    It will skip synchronization in LOG mode and only perform repairs in
    REPAIR mode.

    Note on configuration handling:
        The ovn-octavia-provider code uses cfg.CONF globally for accessing
        Octavia configuration (database connection, service_auth, etc.).
        To avoid conflicts with Neutron's cfg.CONF, this synchronizer:
        1. Loads Octavia config into a separate ConfigOpts instance
        2. Temporarily swaps cfg.CONF when calling Octavia code
        3. Restores Neutron's cfg.CONF afterwards

        This approach is necessary because modifying the underlying code
        to accept configuration as a parameter would require extensive
        changes across multiple modules.
    """

    # Explicitly require 'ovn-sync' mechanism driver.
    # This is required for the plugin to work when invoked with
    # --sync_plugin octavia_ovn_sync (isolated execution).
    # Note: We explicitly set this instead of relying on inheritance
    # because BaseOvnDbSynchronizer in some neutron-lib versions may
    # not have this attribute yet (depends on patch 970267 being merged).
    _required_mechanism_drivers = ['ovn-sync']

    # No additional Neutron service plugins required
    _required_service_plugins = []

    # No additional ML2 extension drivers required
    _required_ml2_ext_drivers = []

    def __init__(self, core_plugin, ovn_driver, mode, is_maintenance=False):
        """Initialize the Octavia OVN synchronizer.

        :param core_plugin: Neutron core plugin instance
        :param ovn_driver: OVN mechanism driver instance
        :param mode: Sync mode (log, repair, migrate)
        :param is_maintenance: Whether running in maintenance mode
        """
        super().__init__(core_plugin, ovn_driver, mode, is_maintenance)

        # Load Octavia configuration into a separate ConfigOpts instance
        self.octavia_conf = self._load_octavia_config()

        # Initialize the Octavia OVN provider driver with Octavia config
        with self._use_octavia_config():
            self.ovn_octavia_driver = driver.OvnProviderDriver()

        # Share the OVN NB API connection from Neutron
        if hasattr(self.ovn_octavia_driver, '_ovn_helper'):
            self.ovn_octavia_driver._ovn_helper._nb_idl = self.ovn_nb_api

    def _load_octavia_config(self):
        """Load Octavia configuration from its config file.

        Returns a separate ConfigOpts instance to avoid interfering with
        Neutron's cfg.CONF.
        """
        octavia_conf = cfg.ConfigOpts()

        # Register Octavia configuration options
        ovn_octavia_config.register_opts()
        log.register_options(octavia_conf)

        # Find Octavia config file
        octavia_conf_file = self._find_octavia_config()

        if octavia_conf_file:
            try:
                octavia_conf(
                    args=[],
                    project='octavia',
                    default_config_files=[octavia_conf_file]
                )
                LOG.info("Loaded Octavia configuration from %s",
                         octavia_conf_file)
            except Exception as e:
                LOG.warning("Failed to load Octavia configuration from %s: %s",
                            octavia_conf_file, e)
        else:
            LOG.warning("Octavia configuration file not found")

        return octavia_conf

    def _find_octavia_config(self):
        """Find Octavia configuration file in standard locations."""
        locations = [
            '/etc/octavia/octavia.conf',
            os.path.expanduser('~/octavia.conf'),
            './octavia.conf',
        ]
        for location in locations:
            if os.path.exists(location):
                return location
        return None

    @contextlib.contextmanager
    def _use_octavia_config(self):
        """Context manager to temporarily use Octavia configuration.

        This swaps cfg.CONF to use Octavia's configuration, allowing
        ovn-octavia-provider code to access its settings without
        conflicting with Neutron's configuration.
        """
        # Save original cfg.CONF references
        original_conf = cfg.CONF

        try:
            # Swap to Octavia config
            cfg.CONF = self.octavia_conf
            clients.CONF = self.octavia_conf
            yield
        finally:
            # Restore Neutron config
            cfg.CONF = original_conf
            clients.CONF = original_conf

    def do_sync(self):
        """Synchronize Octavia Load Balancers with OVN NB DB.

        Behavior by mode:
        - OFF: No synchronization is performed
        - LOG: Skipped (not supported, logs a warning)
        - REPAIR: Synchronizes all OVN provider load balancers
        - MIGRATE: Same as REPAIR for Octavia resources
        """
        if self.mode == ovn_const.OVN_DB_SYNC_MODE_OFF:
            LOG.debug("Octavia OVN sync mode is OFF")
            return

        if self.mode == ovn_const.OVN_DB_SYNC_MODE_LOG:
            LOG.warning(
                "Octavia OVN synchronizer does not support LOG mode. "
                "To synchronize Octavia load balancers with OVN, use "
                "REPAIR mode. Skipping Octavia synchronization."
            )
            return

        LOG.info("Starting Octavia OVN Load Balancers synchronization")

        try:
            with self._use_octavia_config():
                lb_filters = {'provider': 'ovn'}
                self.ovn_octavia_driver.do_sync(**lb_filters)

            LOG.info("Octavia OVN Load Balancers synchronization completed")
        except Exception as e:
            LOG.error("Error during Octavia OVN synchronization: %s", e,
                      exc_info=True)
            if self.mode == ovn_const.OVN_DB_SYNC_MODE_REPAIR:
                raise

    def stop(self):
        """Stop the synchronizer and cleanup resources."""
        super().stop()
